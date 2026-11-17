"""Shared effective learning plans for teacher and student views."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4

from diagnosis import HINTS
from product_store import STORE_LOCK, append_record, dataset_store_paths, load_reviews
from teacher_review import fingerprint
from response_feedback import response_feedback


def _now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _text(value, name, limit, *, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'{name}应为{limit}字以内的' + ('非空文字。' if required else '文字。'))
    return value.strip()


def _student(bundle, sid, nid):
    try:
        return bundle['analysis']['nodes'][nid]['students'][sid]
    except (KeyError, TypeError):
        raise ValueError('学生或节点不存在。') from None


def _machine_recommendation(bundle, sid, nid, rid):
    student = _student(bundle, sid, nid)
    for rec in student['recommendations']:
        if rec['recommendation_id'] == rid:
            return deepcopy(rec)
    raise ValueError('该学生在本节点没有这条建议。')


def _base_plan(bundle, sid, nid, rid):
    recommendation = _machine_recommendation(bundle, sid, nid, rid)
    evidence = {'sources':bundle['dataset']['sources'],
                'records':[r for r in bundle['dataset']['raw_records']
                           if r['学生编号']==sid and r['节点编号']==nid],
                'materials':bundle['dataset']['materials']}
    return {'student_id':sid, 'node_id':nid, 'recommendation_id':rid,
            'material_ids':recommendation['question_ids'][:], 'guidance':'',
            'follow_up':recommendation['follow_up'], 'recommendation':recommendation,
            'data_sha256':fingerprint(evidence)}


def current_plan(bundle, sid, nid, rid, reviews=()):
    plan = _base_plan(bundle, sid, nid, rid)
    for event in reviews:
        if (event.get('student_id'),event.get('node_id'),event.get('recommendation_id')) != (sid,nid,rid):
            continue
        if event.get('before_sha256') != fingerprint(plan):
            raise ValueError('教师记录版本与当前数据不一致，请复核，不自动套用旧意见。')
        candidate = event.get('teacher_plan')
        if not isinstance(candidate, dict) or event.get('after_sha256') != fingerprint(candidate):
            raise ValueError('教师记录损坏或摘要不一致，请复核。')
        plan = deepcopy(candidate)
    return {**plan, 'expected_sha256':fingerprint(plan)}


def student_view(bundle, sid, nid, reviews=()):
    student = deepcopy(_student(bundle, sid, nid))
    student['recommendations'] = [current_plan(bundle,sid,nid,r['recommendation_id'],reviews)['recommendation']
                                  for r in student['recommendations']]
    student['observations'] = [{k:v for k,v in o.items() if not k.startswith('reference_')}
                               for o in student['observations']]
    return student


def _materials(bundle, nid, indicator_id, values):
    if not isinstance(values,list) or not 1 <= len(values) <= 6:
        raise ValueError('请选择1至6道本指标练习。')
    if any(not isinstance(q,str) for q in values) or len(set(values)) != len(values):
        raise ValueError('材料编号必须是无重复的文字列表。')
    allowed = set(re.split(r'[、,，\s]+', bundle['dataset']['nodes'][nid]['可用题目编号']))
    for qid in values:
        question = bundle['dataset']['questions'].get(qid,{})
        indicators = re.split(r'[、,，\s]+', question.get('指标编号',''))
        if qid not in allowed or indicator_id not in indicators:
            raise ValueError('练习必须属于本节点允许题库及当前建议指标。')
    return values[:]


def _effective_recommendation(rec, material_ids, guidance, follow_up):
    updated = deepcopy(rec)
    hints = HINTS.get(rec['indicator_id'], ['逐步说明解答依据。'])
    updated.update({'question_ids':material_ids[:], 'teacher_guidance':guidance,
                    'follow_up':follow_up, 'review_status':'教师已调整，仍须观察后续表现'})
    if material_ids != rec['question_ids']:
        updated['steps'] = [{'order':index+1, 'activity':('独立尝试，保留过程并说明依据；需要时逐级使用提示。'
                             if index == 0 else '独立完成下一题并检验，记录是否使用提示。'),
                             'question_ids':[qid], 'hints':hints[:]}
                            for index,qid in enumerate(material_ids)]
    return updated


def submit_review(bundle, decision, store_path=None):
    if store_path is None:
        store_path = dataset_store_paths(bundle)['reviews']
    if not isinstance(decision,dict):
        raise ValueError('教师调整必须为对象。')
    keys = ('student_id','node_id','recommendation_id','expected_sha256','reviewer','reason',
            'material_ids','guidance','follow_up')
    if set(decision) != set(keys):
        raise ValueError('教师调整字段不完整或含未知字段。')
    sid,nid,rid = (_text(decision[k],k,100) for k in keys[:3])
    reviewer = _text(decision['reviewer'],'操作者',80)
    reason = _text(decision['reason'],'调整理由',1000)
    guidance = _text(decision['guidance'],'指导语',2000)
    follow_up = _text(decision['follow_up'],'观察条件',2000)
    with STORE_LOCK:
        current = current_plan(bundle,sid,nid,rid,load_reviews(store_path))
        if decision['expected_sha256'] != current['expected_sha256']:
            raise ValueError('建议版本已经变化，请刷新后重新提交。')
        prior = {k:v for k,v in current.items() if k != 'expected_sha256'}
        materials = _materials(bundle,nid,prior['recommendation']['indicator_id'],decision['material_ids'])
        effective = {**deepcopy(prior), 'material_ids':materials, 'guidance':guidance, 'follow_up':follow_up,
                     'recommendation':_effective_recommendation(prior['recommendation'],materials,guidance,follow_up)}
        event = {'student_id':sid,'node_id':nid,'recommendation_id':rid,
                 'original_plan':_base_plan(bundle,sid,nid,rid), 'previous_plan':prior, 'teacher_plan':effective,
                 'before_sha256':fingerprint(prior),'after_sha256':fingerprint(effective),
                 'audit':{'reviewer':reviewer,'reason':reason,'reviewed_at_beijing':_now(),
                          'scope':'教学建议调整，不修改原始统计'}, 'review_id':str(uuid4())}
        return append_record(event,store_path=store_path,kind='reviews')


def record_practice(bundle, submission, store_path=None):
    if store_path is None:
        store_path = dataset_store_paths(bundle)['practice']
    expected = {'student_id','node_id','recommendation_id','question_id','answer','process','hint_level'}
    if not isinstance(submission,dict) or set(submission) != expected:
        raise ValueError('练习提交字段不完整或含未知字段。')
    sid,nid,rid = (_text(submission[k],k,100) for k in ('student_id','node_id','recommendation_id'))
    rec = _machine_recommendation(bundle,sid,nid,rid)
    question_id = _text(submission['question_id'],'题号',100)
    _materials(bundle,nid,rec['indicator_id'],[question_id])
    answer = _text(submission['answer'],'答案',4000)
    process = _text(submission['process'],'解答过程',12000,required=False)
    hint = submission['hint_level']
    if type(hint) is not int or not 0 <= hint <= 3:
        raise ValueError('提示层级必须为0至3的整数。')
    event = {'practice_id':str(uuid4()),'student_id':sid,'node_id':nid,'recommendation_id':rid,
             'question_id':question_id,'answer':answer,'process':process,'hint_level':hint,
             'submitted_at_beijing':_now(),'status':'待教师评阅','source':'本地新增练习，非官方赛题原记录'}
    question = bundle['dataset']['questions'][question_id]
    event['feedback'] = response_feedback(question['题干或内容'], question['参考答案'], answer, process)
    return append_record(event,store_path=store_path,kind='practice')
