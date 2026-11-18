"""Versioned Skill loader and tool-only professional workflows."""
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from uuid import uuid4

from jsonschema import Draft202012Validator

ROOT=Path(__file__).resolve().parents[1]
SKILL_IDS=('build_learning_profile','recommend_learning_tasks')
CONSTRAINT_FIELDS={'student_refs','node_ids','target_knowledge_refs','knowledge_refs','denominator_policy',
                   'max_model_calls','create_draft','item_count','max_minutes','mode','class_ref',
                   'verified_claim_refs','source_policy'}
MODEL_CALL_CAP = 5


class SkillInputError(ValueError):
    def __init__(self, code, missing_fields=()):
        super().__init__(code)
        self.missing_fields = list(missing_fields)


class SkillRegistry:
    def __init__(self,roots=None):
        self.roots=roots or (ROOT/'contracts/skills', *(parent/'02_CURRENT/skills' for parent in ROOT.parents[:3]))
        self.entries={}

    def get(self,skill_id):
        if skill_id not in SKILL_IDS:
            raise ValueError('TOOL_UNAVAILABLE')
        if skill_id in self.entries:
            return self.entries[skill_id]
        path=next((root/skill_id/'SKILL.md' for root in self.roots if (root/skill_id/'SKILL.md').is_file()),None)
        if path is None:
            raise ValueError('SKILL_SPEC_NOT_AVAILABLE')
        document=path.read_text(encoding='utf-8')
        version=re.search(r'`skill_version`:\s*`([^`]+)`',document)
        def schema(field):
            found=re.search(r'`'+field+r'`:\s*`([^`]+)`',document)
            if not found:
                raise ValueError('SKILL_SPEC_INVALID')
            location=(path.parent/found[1]).resolve()
            if not location.is_file():
                raise ValueError('SCHEMA_NOT_AVAILABLE')
            result = json.loads(location.read_text(encoding='utf-8'))
            Draft202012Validator.check_schema(result)
            return result
        allowed_section=document.split('## 允许工具',1)[-1].split('\n## ',1)[0]
        tools=set(re.findall(r'`([a-z_]+)`',allowed_section))
        if not version or not tools:
            raise ValueError('SKILL_SPEC_INVALID')
        result={'skill_id':skill_id,'skill_version':version[1],'document':document,
                'document_hash':sha256(document.encode()).hexdigest(),'allowed_tools':tools,
                'input_schema':schema('input_schema'),'output_schema':schema('output_schema')}
        self.entries[skill_id]=result
        return result

    def validate_request(self,request):
        if not isinstance(request, dict):
            raise ValueError('SCHEMA_INVALID')
        if 'skill_id' not in request:
            raise SkillInputError('MISSING_REQUIRED_INPUT', ['skill_id'])
        entry=self.get(request.get('skill_id'))
        errors=list(Draft202012Validator(entry['input_schema']).iter_errors(request))
        if errors:
            missing = sorted(set(entry['input_schema']['required']) - set(request))
            raise SkillInputError('MISSING_REQUIRED_INPUT' if missing else 'SCHEMA_INVALID', missing)
        constraints=request['constraints']
        if set(constraints)-CONSTRAINT_FIELDS:
            raise ValueError('SCHEMA_INVALID')
        for key in ('student_refs','node_ids','knowledge_refs','target_knowledge_refs','verified_claim_refs'):
            if key in constraints and (not isinstance(constraints[key],list) or
                    any(not isinstance(value,str) or not value.strip() for value in constraints[key])):
                raise ValueError('SCHEMA_INVALID')
        for key, minimum, maximum in (
                ('max_model_calls', 0, MODEL_CALL_CAP), ('item_count', 1, 30), ('max_minutes', 1, 120)):
            if key in constraints and (type(constraints[key]) is not int or not minimum <= constraints[key] <= maximum):
                raise ValueError('SCHEMA_INVALID')
        if ('create_draft' in constraints and type(constraints['create_draft']) is not bool
                or constraints.get('source_policy', 'approved_practice_only') != 'approved_practice_only'
                or constraints.get('mode') not in (None, 'whole_class')
                or 'class_ref' in constraints and (not isinstance(constraints['class_ref'], str) or not constraints['class_ref'].strip())
                or constraints.get('denominator_policy', 'scored_only') not in ('scored_only', 'all_assigned')):
            raise ValueError('SCHEMA_INVALID')
        return entry


def execute_skill_request(runtime, request, audience, student_id, budget=None):
    fields = request if isinstance(request, dict) else {}
    skill_id = fields.get('skill_id')
    trace_id = fields.get('trace_id')
    request_id = fields.get('request_id')
    response = {
        'schema_version':'1.1',
        'request_id':request_id if isinstance(request_id, str) and request_id else 'REQ-'+uuid4().hex,
        'skill_id':skill_id,
        'status':'FAILED',
        'data':{},
        'evidence_refs':[],
        'warnings':[],
        'limitations':[],
        'error':None,
        'model_metadata':{'mode':'NOT_RUN', 'calls':0, 'ledger':[]},
        'trace_id':trace_id if isinstance(trace_id, str) and trace_id else 'SKILL-'+uuid4().hex,
    }
    budget = budget if budget is not None else {'limit':MODEL_CALL_CAP,'used':0,'ledger':[], 'deadline':time.monotonic()+180}
    start = budget['used']
    entry = None
    try:
        entry = runtime.skill_registry.validate_request(request)
        response['model_metadata'].update(skill_version=entry['skill_version'],
            skill_document_hash=entry['document_hash'], prompt_version=entry['skill_version'])
        data, warnings, limitations, status = run_skill(runtime, request, entry, audience, student_id, budget)
        response.update(data=data, warnings=warnings, limitations=limitations, status=status,
                        evidence_refs=data['evidence_refs'])
    except (ValueError, KeyError, TypeError) as error:
        code = str(error) if isinstance(error, ValueError) and str(error).isupper() else 'SCHEMA_INVALID'
        response['error'] = {'code':code,'message':code,'retryable':code in {'MODEL_TIMEOUT','TASK_TIMEOUT'},
                            'safe_fallback':'核对快照、证据和请求范围后重试。',
                            'missing_fields':getattr(error,'missing_fields',[])}
    ledger = budget['ledger'][start:]
    if ledger:
        response['model_metadata'].update(mode=ledger[0]['mode'], calls=len(ledger), ledger=ledger,
            provider='DeepSeek' if ledger[0]['mode']=='REAL_API' else ledger[0]['mode'],
            model=next((call['model'] for call in ledger if call.get('model')),None))
    if entry is not None:
        Draft202012Validator(entry['output_schema']).validate(response)
    runtime.logs.append({'trace_id':response['trace_id'],'skill_id':skill_id,'status':response['status'],
        'snapshot_hash':runtime.snapshot_hash,'evidence_refs':response['evidence_refs'],
        'error_code':response['error']['code'] if response['error'] else None})
    return response


def run_skill(runtime,request,entry,audience,student_id,budget):
    if request['snapshot_hash']!=runtime.snapshot_hash:
        raise ValueError('STALE_SNAPSHOT')
    runtime._scope(audience,student_id)
    constraints=request['constraints']
    tool_steps=[]
    warnings=[]
    limitations=[]
    trusted={'skill_id':entry['skill_id'],'skill_version':entry['skill_version'],
             'skill_document_hash':entry['document_hash'],'input_refs':request['input_refs'],
             'goal':request['goal'],'instructions':entry['document']}

    def tool(identifier,arguments):
        if identifier not in entry['allowed_tools']:
            raise ValueError('SKILL_TOOL_NOT_ALLOWED')
        if time.monotonic() > budget.get('deadline', float('inf')):
            raise ValueError('TASK_TIMEOUT')
        response=runtime.call_tool(identifier,{**arguments,'snapshot_hash':request['snapshot_hash']},
            audience,student_id,_budget=budget,_skill_context=trusted)
        tool_steps.append({'tool_id':identifier,'trace_id':response['trace_id'],'status':response['status']})
        warnings.extend(response.get('warnings',[]))
        if not response['ok']:
            raise ValueError(response['error']['code'])
        return response['data']

    profile = entry['skill_id']=='build_learning_profile'
    if constraints.get('create_draft') and (audience!='TEACHER' or profile):
        raise ValueError('ACCESS_DENIED')
    students=constraints.get('student_refs',[student_id] if student_id else [])
    class_ref = constraints.get('class_ref')
    if class_ref or constraints.get('mode') == 'whole_class':
        if audience == 'STUDENT':
            raise ValueError('ACCESS_DENIED')
        if not class_ref:
            raise SkillInputError('MISSING_REQUIRED_INPUT', ['constraints.class_ref'])
        class_students = [ref for ref, row in runtime.bundle['dataset']['students'].items()
                          if row['模拟班级编号'] == class_ref]
        if not class_students:
            raise ValueError('EVIDENCE_NOT_FOUND')
        if not set(students) <= set(class_students):
            raise ValueError('ACCESS_DENIED')
        students = students or class_students
    if not students and audience == 'TEACHER':
        raise SkillInputError('MISSING_REQUIRED_INPUT', ['constraints.student_refs'])
    if audience == 'STUDENT':
        if students and set(students) != {student_id}:
            raise ValueError('ACCESS_DENIED')
        students = [student_id]
    students = list(dict.fromkeys(students))
    filters={'student_refs':students}
    node_ids = constraints.get('node_ids', [])
    nodes = list(dict.fromkeys([
        *constraints.get('target_knowledge_refs', constraints.get('knowledge_refs', [])),
        *(node for node in node_ids if node in runtime.kb.nodes),
    ]))
    if not set(nodes) <= set(runtime.kb.nodes):
        raise ValueError('EVIDENCE_NOT_FOUND')
    assessment_ids = [node for node in node_ids if node not in runtime.kb.nodes]
    if assessment_ids:
        filters['node_ids']=assessment_ids
    stats_filters = {**filters, **({'knowledge_refs':nodes} if nodes else {})}
    explicit=set(request['input_refs'])
    if explicit and audience == 'STUDENT':
        for identity in explicit:
            runtime.kb.get(identity, audience, student_id=student_id)
    tool('get_data_state',{})
    records=[]
    if profile:
        cursor=None
        for _ in range(100):
            page=tool('query_learning_records',{**filters,'limit':100,**({'cursor':cursor} if cursor else {})})
            records.extend(page['records'])
            cursor=page['next_cursor']
            if not cursor:break
        if cursor:
            raise ValueError('OUTPUT_LIMIT_EXCEEDED')
        if nodes:
            records=[row for row in records if set(row['knowledge_refs']) & set(nodes)]
    if explicit:
        stats_filters['response_refs']=sorted(explicit)
        selected = records if profile else runtime._records(stats_filters,audience,student_id)
        if not explicit <= {row['record_id'] for row in selected}:
            raise ValueError('EVIDENCE_NOT_FOUND')
        records=[row for row in selected if row['record_id'] in explicit]
    statistics=tool('compute_stats',{'operation':'student_summary','filters':stats_filters,
        'denominator_policy':constraints.get('denominator_policy','scored_only')})
    if not profile and not explicit:
        observed_refs = [ref for metric in statistics['metrics'] for ref in metric['evidence_refs']]
        records=runtime._records({**stats_filters,'response_refs':observed_refs},audience,student_id) if observed_refs else []
    if not nodes:
        nodes=list(dict.fromkeys(ref for row in records for ref in row.get('knowledge_refs',[])))
    candidates=tool('search_knowledge',{'node_ids':nodes,'limit':100})['candidates'] if nodes else []
    sources=[]
    for candidate in candidates:
        if candidate['entity_type'] in {'SOURCE_PASSAGE','KNOWLEDGE_NODE'}:
            sources.append(tool('get_evidence_item',{'entity_id':candidate['id'],'entity_type':candidate['entity_type']}))
        if len(sources)>=12:break
    trusted['knowledge_evidence_refs']=[source['id'] for source in sources]
    trusted['scope_knowledge_refs']=nodes
    requested_claims=constraints.get('verified_claim_refs',[])
    items=[]
    if not profile:
        permitted_evidence={row['record_id'] for row in records}
        for identifier in requested_claims:
            verified=runtime._verified_claims.get(identifier)
            if not verified or verified.get('source_snapshot_hash')!=request['snapshot_hash']:
                raise ValueError('EVIDENCE_NOT_FOUND')
            if not set(verified['evidence_refs']) <= permitted_evidence:
                raise ValueError('ACCESS_DENIED')
        if not nodes:
            raise SkillInputError('MISSING_REQUIRED_INPUT', ['constraints.target_knowledge_refs'])
        practice=tool('find_practice_items',{'knowledge_refs':nodes,'item_count':constraints.get('item_count',3),
            'source_policy':constraints.get('source_policy','approved_practice_only')})
        items=practice['items']
        if not items:
            raise ValueError('RESOURCE_UNAVAILABLE')
        trusted['approved_practice_items']=[
            {key:item[key] for key in ('question_id','exact_text','knowledge_refs')} for item in items
        ]
    requested_calls=constraints.get('max_model_calls',0)
    diagnoses=[]
    claims=list(statistics.get('claims',[]))
    refs=[row['record_id'] for row in records if row.get('include_in_step_diagnosis')]
    diagnosis=None
    if refs:
        diagnosis=tool('analyze_responses',{'response_refs':refs,'diagnostic_goal':request['goal'],
            'verification_mode':'AUTO','max_model_calls':requested_calls})
        diagnoses=diagnosis['diagnoses']
        claims.extend(diagnosis['claims'])
    evidence_refs=[row['record_id'] for row in records]
    if profile:
        limitations.append('画像基于本次作答观察与独立统计，解释和建议保留各自的证据。')
        for metric in statistics['metrics']:
            if not metric['n']:
                limitations.append(f'{metric["student_id"]} 在当前范围内没有可评分记录。')
        data={'scope':{'audience':audience,'student_id':student_id,'student_refs':students,'node_ids':constraints.get('node_ids',[])},
            'summary_metrics':statistics['metrics'],
            'observed_strengths':[item for item in diagnoses if item['mathematics']['answer_validity']=='VALID'],
            'observed_difficulties':[item for item in diagnoses if item['mathematics']['answer_validity']=='INVALID'],
            'patterns':[],'hypotheses':[],
            'action_candidates':[{'text':item['mathematics']['next_action'],'evidence_refs':[item['record_id']]}
                                 for item in diagnoses if item['mathematics']['answer_validity']=='INVALID'],
            'knowledge_candidates':candidates,'knowledge_sources':sources,
            'ai_observations_pending_review':[claim for claim in claims
                if claim.get('derived_by')=='model' and claim['assertion_type']=='OBSERVED_FACT'],
            'ai_actions_pending_review':[claim for claim in claims
                if claim.get('derived_by')=='model' and claim['assertion_type']=='RECOMMENDATION']}
    else:
        actions=[item for item in claims if item.get('assertion_type')=='RECOMMENDATION' and item.get('derived_by')=='model']
        why=list(dict.fromkeys(item['mathematics']['next_action'] for item in diagnoses
                               if item['mathematics']['answer_validity']=='INVALID'))
        if not why:
            why=['先完成所选知识目标的诊断练习，提交关键步骤后再判断下一步。']
        data={'learning_goals':nodes,'recommended_items':items,'why':why,'shortfall':practice['shortfall'],
            'knowledge_sources':sources,'task_draft_ref':None,'verified_claim_refs':requested_claims,
            'ai_actions_pending_review':actions,
            'goal_source':'VERIFIED_CLAIMS' if requested_claims else 'REQUESTED_TARGET_WITH_OBSERVATIONS'}
        limitations.append('练习来自已授权题库，教学难度和迁移顺序由教师确认。')
        if constraints.get('create_draft'):
            identity=request['request_id']
            runtime._generation_requests[identity]={'student_refs':students,'target_knowledge_refs':nodes,
                'item_count':constraints.get('item_count',3),'max_minutes':constraints.get('max_minutes',20)}
            data['task_draft_ref']=tool('create_assignment_draft',{'generation_request_id':identity,'idempotency_key':identity})
        item_refs=[item['question_id'] for item in items]
        evidence_refs.extend(item_refs)
        claims.append({'assertion_type':'RECOMMENDATION','assertion_text':f'已找到{len(items)}道授权练习，题量缺口{practice["shortfall"]}道。',
            'evidence_refs':item_refs,'derived_by':'deterministic','verifier_id':'TASK_VALIDATOR',
            'verification_payload':{'kind':'PRACTICE_COUNT','question_ids':item_refs,'shortfall':practice['shortfall'],
                                    'requested_count':constraints.get('item_count',3)}})
    data['model_advice_review'] = []
    for item in diagnoses:
        if item['model_status'] == 'NOT_RUN':
            continue
        path_a = item['path_a'] or {}
        data['model_advice_review'].append({
            'record_id':item['record_id'],
            'model_status':item['model_status'],
            'path_a_output_hash':path_a.get('output_hash'),
            'arbitration_status':item['arbitration']['status'],
            'suggestion_count':len(path_a.get('data',{}).get('suggestions',[])),
            'retained_count':sum(claim['assertion_type']=='RECOMMENDATION' for claim in item['model_claims']),
            'rejected_reasons':item['rejected_suggestions'],
        })
    data['claims']=claims
    data['gate']=runtime.verify_claims(claims,audience,student_id)
    evidence_refs.extend(source['id'] for source in sources)
    evidence_refs.extend(ref for claim in data['gate']['assertions']
                         if claim['result'] in {'verified','unverifiable'} for ref in claim['evidence_refs'])
    data.update(tool_steps=tool_steps, source_snapshot_hash=request['snapshot_hash'],
                evidence_refs=list(dict.fromkeys(evidence_refs)), warnings=warnings, limitations=limitations)
    if runtime.snapshot_hash!=request['snapshot_hash']:
        raise ValueError('STALE_SNAPSHOT')
    if data['gate']['status']=='GATE_BLOCKED':
        raise ValueError('VERIFICATION_CONFLICT')
    if data['gate']['status']=='GATE_PARTIAL' or any(item['model_status'] in {'DISPUTED','PARTIAL_NEEDS_REVIEW'} for item in diagnoses):
        status = 'NEEDS_REVIEW'
    else:
        status = 'PARTIAL' if warnings or not records else 'SUCCEEDED'
    return data,list(dict.fromkeys(warnings)),limitations,status
