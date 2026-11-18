"""Readable HTTP projections; statistical and mathematical rules stay in Runtime."""
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from diagnosis import HINTS
from knowledge_relations import recommended_practice_edges


PATH_FIELDS = {'path','source_path','file_path','absolute_path','image_path','artifact_path',
               'data_dir','data_directory','raw_text','ocr'}
ANSWER_FIELDS = {'answer','reference_answer','reference_explanation','rubric_points',
                 'official_point_scores','candidate_point_scores','effective_point_scores'}
VIEW_IDS = {
    'TEACHER': {'dashboard':'teacher_dashboard','classes':'teacher_classroom','profile':'teacher_student_profile',
                'diagnosis':'teacher_step_diagnosis','knowledge':'teacher_constellation',
                'assignments':'teacher_assignment','agent':'teacher_agent','governance':'teacher_data_governance'},
    'STUDENT': {'learning':'student_home','profile':'student_learning_profile','knowledge':'student_learning_profile',
                'diagnosis':'student_step_explanation','assignments':'student_practice',
                'practice':'student_practice','feedback':'student_feedback'},
}
PRESENTATION_SCHEMA = Draft202012Validator(json.loads(
    (Path(__file__).resolve().parents[1]/'contracts/schemas/ui_response.schema.json').read_text(encoding='utf-8')))


def clean_dto(value, audience='TEACHER'):
    if isinstance(value,dict):
        return {key:clean_dto(item,audience) for key,item in value.items()
                if key not in PATH_FIELDS and not (audience=='STUDENT' and key in ANSWER_FIELDS)}
    if isinstance(value,(list,tuple)):
        return [clean_dto(item,audience) for item in value]
    return value


def assignment_dto(draft, audience='TEACHER', student_id=None):
    if audience=='STUDENT' and (student_id not in draft['target_student_refs'] or
            draft['status'] not in {'SUBMITTED_FOR_REVIEW','EXPORTED'}):
        raise ValueError('ACCESS_DENIED')
    result = clean_dto(deepcopy(draft),audience)
    if audience=='STUDENT':
        result.pop('confirmation',None)
        result.pop('group_candidate',None)
    return result


def tool_data(response):
    if not response.get('ok'):
        raise ValueError((response.get('error') or {}).get('code','TOOL_FAILED'))
    return response['data']


def records_for(runtime, audience, student_id=None, node_id=None):
    args = {'limit':100}
    if student_id:
        args['student_refs'] = [student_id]
    if node_id:
        args['node_ids'] = [node_id]
    result=[]
    for _ in range(100):
        page = tool_data(runtime.call_tool('query_learning_records',args,audience,student_id))
        result.extend(page['records'])
        if not page['next_cursor']:
            return result
        args['cursor'] = page['next_cursor']
    raise ValueError('OUTPUT_LIMIT_EXCEEDED')


def summary(runtime,audience,student_id=None,node_id=None,class_id=None,operation='student_summary'):
    filters={}
    if student_id:
        filters['student_refs']=[student_id]
    if node_id:
        filters['node_ids']=[node_id]
    if class_id:
        filters['class_refs']=[class_id]
    data=tool_data(runtime.call_tool('compute_stats',{'operation':operation,'filters':filters,
        'denominator_policy':'scored_only'},audience,student_id))
    metrics=[]
    for index,item in enumerate(data['metrics']):
        metrics.append({**item,'id':item.get('indicator_id') or item.get('student_id') or f'metric-{index}',
            'label':'本次有效得分','value':item['rate']})
    return metrics,data


def findings_from_gate(gate):
    return [{'id':item['assertion_id'],'title':'本次观察' if item['assertion_type']!='RECOMMENDATION' else '下一步行动',
             'description':item['assertion_text'],'kind':item['assertion_type'],
             'verification':item['result'],'evidence_refs':item['evidence_refs']}
            for item in gate.get('assertions',[])]


def presentation_contract(data, audience, view, snapshot_hash):
    findings = [
        {'finding_id': item['id'], 'display_title': item['title'], 'plain_description': item['description'],
         'claim_ref': item['id'], 'evidence_refs': item['evidence_refs'],
         'details_available':bool(item['evidence_refs'])}
        for item in data['findings']
    ]
    response = {'schema_version':'1.1','view_id':VIEW_IDS[audience][view],'audience':audience,
                'snapshot_hash':snapshot_hash,'headline':data['title'],'summary':data['description'],
                'findings':findings,
                'next_actions':[{'label':action['label'],'action_type':action['id'],
                    'requires_teacher_confirmation':audience=='TEACHER' and action['target']=='assignments'}
                    for action in data['actions']],
                'limitations_display':[item for item in data['limitations'] if isinstance(item,str) and item],
                'evidence_detail_refs':list(dict.fromkeys([*data['evidence_refs'],*(
                    ref for item in findings for ref in item['evidence_refs'])])),
                'demo_role_switch':True,'production_auth_confirmed':False}
    if not PRESENTATION_SCHEMA.is_valid(response):
        raise ValueError('PRESENTATION_CONTRACT_INVALID')
    return response


def response_view(runtime,record_id,audience,student_id=None):
    bundle=runtime.kb.evidence_bundle(record_id,audience=audience,student_id=student_id)
    result=tool_data(runtime.call_tool('analyze_responses',{'response_refs':[record_id],
        'max_model_calls':0},audience,student_id))
    detail = {'record':bundle['response'],'question':bundle['question'],
        'rubric_points':bundle['rubric_points'],'student_steps':bundle['student_steps'],
        'knowledge_links':bundle['knowledge_links'],'source_passages':bundle['source_passages'],
        'evidence_refs':bundle['evidence_refs'],'mathematics':result['diagnoses'][0]['mathematics'],
        'gate':result['gate']}
    if audience == 'TEACHER':
        for field in ('step_rubric_links', 'step_node_links', 'step_alignment', 'rubric_verification'):
            if field in bundle:
                detail[field] = bundle[field]
    return clean_dto(detail,audience)


def governance_view(runtime):
    snapshot=runtime.kb.snapshot
    questions = runtime.kb.questions
    records = [{**row, 'question_display': questions[row['question_id']]['exact_text']}
               for row in snapshot['records']]
    comparable = [row for row in records if row['include_in_score_stats'] and row['official_score'] is not None]
    sensitivity = {
        'policy': 'SAME_CURRENT_SCORABLE_EVENTS',
        'denominator_definition': '当前成绩统计可用且保有官方原分的作答事件满分之和',
        'n': len(comparable),
        'coverage': len(comparable) / len(records) if records else 0,
        'denominator': sum(row['max_score'] for row in comparable),
        'official_score': sum(row['official_score'] for row in comparable),
        'effective_score': sum(row['effective_score'] for row in comparable),
        'changed_events': sum(row['official_score'] != row['effective_score'] for row in comparable),
        'excluded_count': len(records) - len(comparable),
    }
    return clean_dto({**snapshot,'records':records,'candidates':runtime.kb.candidates,'decisions':runtime.kb.teacher_events,
                      'invalidations':runtime.kb.invalidations,'practice_reviews':runtime.kb.practice_review_events,
                      'field_mapping':runtime.kb.export_field_mapping(),
                      'sensitivity':sensitivity,
                      'source_files':snapshot.get('source_files',runtime.bundle['dataset']['sources'])})


def build_view(runtime,view,audience,student_id=None,node_id=None,class_id=None,record_id=None):
    requested = view
    if view not in VIEW_IDS['TEACHER'] and view not in VIEW_IDS['STUDENT']:
        raise ValueError('VIEW_NOT_FOUND')
    if view not in VIEW_IDS.get(audience,{}):
        raise ValueError('ACCESS_DENIED')
    if view == 'feedback':
        view = 'practice'
    titles={'dashboard':'学情总览','classes':'班级分析','profile':'学生画像','diagnosis':'逐题诊断',
            'knowledge':'知识星图','assignments':'学习任务编排','agent':'Agent分析工作台',
            'governance':'数据治理','learning':'我的学习','practice':'练习与反馈'}
    if view not in titles:
        raise ValueError('VIEW_NOT_FOUND')
    if audience=='STUDENT' and view=='governance':
        raise ValueError('ACCESS_DENIED')
    before=runtime.snapshot_hash
    base={'view':view,'title':titles[view],'description':'从本次有效作答出发，查看证据并选择下一步行动。',
          'scope':{'node_id':node_id,'student_id':student_id,'class_id':class_id},
          'metrics':[],'findings':[],'actions':[],'evidence_refs':[],'limitations':[]}
    if view in {'dashboard','classes','profile','learning'}:
        summary_student=None if view in {'dashboard','classes'} and audience=='TEACHER' else student_id
        if view in {'dashboard','classes'}:
            base['scope']['student_id']=summary_student
        metrics,statistics=summary(runtime,audience,summary_student,node_id,class_id)
        base.update(metrics=metrics,gate=statistics['gate'],findings=findings_from_gate(statistics['gate']),
                    evidence_refs=[ref for metric in metrics for ref in metric['evidence_refs']])
        if view in {'dashboard','classes'}:
            aggregate, class_stats = summary(runtime,audience,summary_student,node_id,class_id,operation='class_summary')
            for metric in aggregate:
                metric['label'] = '当前范围有效得分'
            base.update(metrics=aggregate, gate=class_stats['gate'],
                        findings=findings_from_gate(class_stats['gate']),
                        evidence_refs=[ref for metric in aggregate for ref in metric['evidence_refs']])
            base['students']=[{'id':metric['student_id'],'label':metric['student_id'],
                'class_id':runtime.bundle['dataset']['students'][metric['student_id']]['模拟班级编号'],
                'metric':metric} for metric in metrics if metric['student_id']]
            base['records_count']=statistics['records_count']
            base['observed_students'] = class_stats['observed_students']
            base['quality_summary']=runtime.kb.snapshot.get('summary',{})
            if view == 'classes':
                indicators, _ = summary(runtime,audience,summary_student,node_id,class_id,operation='indicator_distribution')
                nodes = runtime.kb.nodes
                for metric in indicators:
                    metric['label'] = nodes.get(metric['id'], {}).get('name', metric['id'])
                base['indicator_metrics'] = indicators
            base['actions'] = [{'id':'classes','label':'比较班级内同题作答','target':'classes'},
                               {'id':'assignments','label':'准备针对性练习','target':'assignments'}]
        else:
            skill_options = {'student_refs':[student_id]}
            if node_id:
                skill_options['node_ids'] = [node_id]
            profile_reply = runtime.call_skill('build_learning_profile',skill_options,audience,student_id)
            profile = tool_data(profile_reply)
            questions = runtime.kb.questions
            rows=[{**row, 'question_display': questions[row['question_id']]['exact_text']}
                  for row in records_for(runtime,audience,student_id,node_id)]
            base.update(profile=profile,records=rows,
                        assignments=[assignment_dto(item,audience,student_id) for item in runtime.list_drafts(student_id)
                                     if audience=='TEACHER' or item['status'] in {'SUBMITTED_FOR_REVIEW','EXPORTED'}])
            base['limitations'] += profile_reply.get('warnings', [])
            if profile.get('gate'):
                base['gate'] = profile['gate']
                base['findings'] = findings_from_gate(profile['gate'])
            base['actions']=[{'id':'practice','label':'安排针对性练习' if audience=='TEACHER' else '开始本次练习','target':'assignments' if audience=='TEACHER' else 'practice'}]
    elif view=='diagnosis':
        rows=records_for(runtime,audience,student_id,node_id)
        questions=runtime.kb.questions
        base['records']=[{**row,'question_display':questions[row['question_id']]['exact_text']} for row in rows]
        selected=record_id or (rows[0]['record_id'] if rows else None)
        if selected and selected not in {row['record_id'] for row in rows}:
            raise ValueError('ACCESS_DENIED')
        base['selected_record_id']=selected
        base['response']=response_view(runtime,selected,audience,student_id) if selected else None
    elif view=='knowledge':
        metrics,statistics=summary(runtime,audience,student_id,node_id,operation='indicator_distribution')
        lookup={metric['indicator_id']:metric for metric in metrics}
        base['nodes']=[{**node,**lookup.get(identity,{}),'id':identity,'label':node.get('name',identity)}
                       for identity,node in runtime.kb.nodes.items()]
        course_relationships = getattr(runtime.kb, 'course_relationships', None)
        edges = course_relationships()['node_edges'] if callable(course_relationships) else []
        edges.extend(recommended_practice_edges(runtime.kb.practice_items))
        base['edges'] = [{**edge, 'source':edge['from_id'], 'target':edge['to_id']}
                         for edge in edges if edge['review_status'] not in {'REJECTED','DISPUTED'}]
        if not base['edges']:
            base['limitations'].append('当前资料未提供可核对的节点连线依据。')
        base['practice_items']=tool_data(runtime.call_tool('find_practice_items',{'item_count':30},audience,student_id))['items']
        base['metrics']=metrics
        base['gate']=statistics['gate']
    elif view in {'assignments','practice'}:
        drafts=runtime.list_drafts(student_id if audience=='STUDENT' else None)
        base['assignments']=[assignment_dto(item,audience,student_id) for item in drafts
                            if audience=='TEACHER' or item['status'] in {'SUBMITTED_FOR_REVIEW','EXPORTED'}]
        base['practice_items']=tool_data(runtime.call_tool('find_practice_items',{'item_count':30},audience,student_id))['items']
        base['knowledge_nodes']=[{'id':identity,'label':node.get('name',identity)} for identity,node in runtime.kb.nodes.items()]
        base['students']=[{'id':identity,'label':identity,'class_id':student['模拟班级编号']}
            for identity,student in runtime.bundle['dataset']['students'].items() if audience=='TEACHER' or identity==student_id]
        if view=='practice':
            base['hints']=HINTS
    elif view=='agent':
        state=runtime.state()
        registered = hasattr(runtime,'skill_registry')
        base.update(registry=state['registry'],model_configured=state['model_configured'],
                    skill_contract_status='VERSIONED_SKILLS' if registered else 'LEGACY_WORKFLOW')
        if not registered:
            base['limitations'].append('LEGACY_SKILL_CONTRACT')
    elif view=='governance':
        base.update(governance_view(runtime))
    if runtime.snapshot_hash != before:
        raise ValueError('STALE_SNAPSHOT')
    dto = clean_dto(base,audience)
    if view == 'practice':
        dto['answers'] = clean_dto(runtime.list_assignment_answers(student_id))
    if requested == 'feedback':
        dto.update(view='feedback',title='自检反馈')
    dto['presentation'] = presentation_contract(dto,audience,requested,before)
    return dto
