"""Student evidence, actionable practice and audited teacher decisions."""
import re

import streamlit as st

from product_service import current_plan, record_practice, student_view, submit_review
from teacher_review import fingerprint
from response_feedback import response_feedback
from ui_common import citations, indicator_table, page_heading, score_metrics


def render_diagnosis(bundle, sid, nid, reviews, practice, *, is_teacher):
    student = student_view(bundle,sid,nid,reviews)
    page_heading('学生诊断' if is_teacher else '我的学习计划',
                 f'{sid} · {student["class_id"]} · {nid} · {bundle["dataset"]["nodes"][nid]["说明"]}')
    score_metrics(student)
    _summary(student)
    if is_teacher:
        tabs = st.tabs(['证据与过程', '学习建议', '教师调整'])
        with tabs[0]:
            _teacher_evidence(bundle,student,sid,nid)
        with tabs[1]:
            _learning_plan(bundle,student,sid,nid,practice,is_teacher=True)
        with tabs[2]:
            _review_form(bundle,student,sid,nid,reviews)
    else:
        _learning_plan(bundle,student,sid,nid,practice,is_teacher=False)
        with st.expander('查看我的指标与历史作答'):
            indicator_table(student)
            _observations(student['observations'],is_teacher=False)


def _summary(student):
    if student['overall']['score_rate'] is None:
        st.info('当前节点缺少有效已评分作答，无法计算得分率。先补充作答或评分，不能据此判断不会。')
        return
    needs = [cell['label'] for cell in student['indicators'].values() if cell['status']=='需巩固线索']
    if needs:
        st.warning('优先复核：'+'；'.join(needs[:2])+'。这是当前证据支持的练习方向，仍需结合解题过程判断。')
    else:
        st.info('已有记录中未触发巩固线索。证据较少的指标仍需补充观察，不能推广为全部掌握。')


def _teacher_evidence(bundle, student, sid, nid):
    st.subheader('从失分点回到原始过程')
    st.caption('优先展示含具体过程线索的作答；每个结论保留记录编号、分点评分和材料出处。')
    original = bundle['analysis']['nodes'][nid]['students'][sid]
    _observations(original['observations'],is_teacher=True,
                  dataset_identity=fingerprint(bundle['dataset']['sources']))
    with st.expander('全部指标与证据覆盖'):
        indicator_table(student)
    rows = [r for r in bundle['dataset']['raw_records'] if r['学生编号']==sid and r['节点编号']==nid]
    with st.expander(f'原始记录与修订处理 · {len(rows)}条'):
        for row in rows:
            st.markdown(f"**{row['记录编号']} · {row['材料编号']} · 修订{row['修订版本']} · {row['评分状态']}**")
            st.json({'原始作答':row,'统计处理':bundle['dataset']['record_meta'][row['记录编号']]},expanded=False)


def _observations(observations, *, is_teacher, dataset_identity=''):
    ordered = sorted(observations,key=lambda o:not any(f['code']!='unearned_scoring_points' for f in o['findings']))
    for index,observation in enumerate(ordered):
        label = f"{observation['question'][:54]} · {observation['question_id']} · {observation['score']:g}/{observation['max_score']:g}分"
        with st.expander(label,expanded=index==0):
            st.write(observation['question'])
            st.markdown('**原始作答**')
            st.text(observation['student_answer'] or '未提供最终作答')
            st.text(observation['student_process'] or '未提供解题过程，无法判断具体步骤。')
            for finding in observation['findings']:
                st.info(finding['text'])
            if is_teacher:
                feedback = response_feedback(observation['question'], observation['reference_answer'],
                                             observation['student_answer'], observation['student_process'])
                _show_feedback(feedback)
            points = [{'评分点':pid,'要求':rubric['label'],'得分':observation['point_scores'].get(pid,0),
                       '满分':rubric['max_score']} for pid,rubric in observation['rubric'].items()]
            st.dataframe(points,hide_index=True,width='stretch')
            if is_teacher and st.checkbox('显示本题参考答案（教师核对）',
                                          key=f"reference_{dataset_identity}_{observation['record_id']}"):
                st.write(observation['reference_answer'])
                st.write(observation['reference_explanation'])


def _recommendation_picker(student):
    recommendations = student['recommendations']
    if not recommendations:
        st.info('本节点暂无可执行建议。请先核对记录与教学范围。')
        return None
    options = [rec['recommendation_id'] for rec in recommendations]
    by_id = {rec['recommendation_id']:rec for rec in recommendations}
    if st.session_state.get('learning_rec') not in options:
        st.session_state['learning_rec'] = options[0]
    selected = st.selectbox('选择学习方向',options,key='learning_rec',
                            format_func=lambda rid:f"{by_id[rid]['requirement_ref']['观察指标']} · {by_id[rid]['kind']}")
    return by_id[selected]


def _learning_plan(bundle, student, sid, nid, practice, *, is_teacher):
    st.subheader('下一步，练什么、怎样练')
    rec = _recommendation_picker(student)
    if rec is None:
        return
    st.write(rec['reason'])
    st.caption('建议状态：'+rec['review_status']+' · 依据：'+('、'.join(rec['evidence_ids']) or '暂无有效评分记录'))
    if rec.get('teacher_guidance'):
        st.info('教师指导：'+rec['teacher_guidance'])
    for step in rec['steps']:
        st.markdown(f"**{step['order']:02d} · {step['activity']}**")
        st.caption('练习：'+'、'.join(step['question_ids']))
    with st.expander('完成后如何判断下一步'):
        st.write(rec['follow_up'])
    if is_teacher:
        with st.expander('课标与教材来源'):
            citations(rec)
    _practice(bundle,rec,sid,nid,practice)


def _practice(bundle, rec, sid, nid, practice):
    if st.session_state.get('practice_question') not in rec['question_ids']:
        st.session_state['practice_question'] = rec['question_ids'][0]
    question_id = st.selectbox('本次练习题目',rec['question_ids'],key='practice_question')
    identity = (sid,nid,rec['recommendation_id'],question_id)
    if st.session_state.get('practice_identity') != identity:
        st.session_state.update({'practice_identity':identity,'practice_answer':'','practice_process':''})
    material = bundle['dataset']['materials'][question_id]
    st.write(material['题干或内容'])
    if material.get('选项'):
        st.text(material['选项'])
    hints = list(dict.fromkeys(hint for step in rec['steps'] if question_id in step['question_ids'] for hint in step['hints']))[:3]
    hint_key = f'hint_{sid}_{nid}_{rec["recommendation_id"]}_{question_id}'
    level = min(st.session_state.get(hint_key,0),len(hints))
    for index,hint in enumerate(hints[:level],1):
        st.info(f'提示 {index}：{hint}')
    if hints and st.button('展开下一级提示',key='next_hint',disabled=level>=len(hints)):
        st.session_state[hint_key] = level+1
        st.rerun()
    st.caption(f'已使用 {level}/{len(hints)} 级提示。先尝试，再按需要展开。')
    _practice_form(bundle,rec,sid,nid,question_id,level)
    current = [p for p in practice if p['student_id']==sid and p['node_id']==nid]
    if current:
        st.caption(f'本节点已提交 {len(current)} 次练习。新增记录独立保存，原始得分统计不变。')
        with st.expander('我的练习反馈记录'):
            for item in reversed(current[-10:]):
                st.write(f"{item['question_id']} · {item['status']} · 提示{item['hint_level']}级")
                st.text(item['answer'])
                if item.get('feedback'):
                    _show_feedback(item['feedback'])
                st.caption(item['submitted_at_beijing'])


def _practice_form(bundle, rec, sid, nid, question_id, level):
    with st.form('practice_form'):
        answer = st.text_area('我的答案',key='practice_answer',max_chars=4000)
        process = st.text_area('解题过程与依据',key='practice_process',max_chars=12000)
        submitted = st.form_submit_button('提交练习记录',type='primary')
    if submitted:
        try:
            record_practice(bundle,{'student_id':sid,'node_id':nid,'recommendation_id':rec['recommendation_id'],
                                  'question_id':question_id,'answer':answer,'process':process,'hint_level':level})
            st.session_state['flash_message']='练习已保存，数学自检反馈见下方记录，评分待教师评阅。'
            st.rerun()
        except (ValueError,OSError) as error:
            st.error(str(error))


def _show_feedback(feedback):
    validity = feedback['answer_validity']
    if validity == 'VALID' and not any(step['local_validity'] != 'VALID' for step in feedback['steps']):
        st.success(feedback['next_action'])
    elif validity == 'UNRESOLVED':
        st.info(feedback['next_action'])
    else:
        st.warning(feedback['next_action'])
    if feedback['steps']:
        labels = {'VALID': '等价', 'INVALID': '不等价', 'UNRESOLVED': '待核对'}
        st.dataframe([{'步骤': step['step'], '从': step['source'], '到': step['target'],
                       '数学核验': labels[step['local_validity']]}
                      for step in feedback['steps']], hide_index=True, width='stretch')
    st.caption('本地数学自检 · 适用于有理系数一元一次式、方程与单个不等式。评分由教师复核。')


def _review_form(bundle, student, sid, nid, reviews):
    st.subheader('教师复核与调整')
    recs = student['recommendations']
    if not recs:
        st.info('暂无可调整的建议。')
        return
    options = [r['recommendation_id'] for r in recs]
    if st.session_state.get('review_rec') not in options:
        st.session_state['review_rec'] = options[0]
    rid = st.selectbox('选择待复核建议',options,key='review_rec')
    plan = current_plan(bundle,sid,nid,rid,reviews)
    original = next(r for r in bundle['analysis']['nodes'][nid]['students'][sid]['recommendations'] if r['recommendation_id']==rid)
    with st.expander('保留的机器原建议'):
        st.write(original['reason'])
        st.write('原练习：'+'、'.join(original['question_ids']))
        st.write(original['follow_up'])
    _review_fields(bundle,sid,nid,rid,plan)
    events = [e for e in reviews if (e['student_id'],e['node_id'],e['recommendation_id'])==(sid,nid,rid)]
    for event in reversed(events):
        with st.expander(f"调整审计 · {event['audit']['reviewed_at_beijing']}"):
            st.json({'操作者':event['audit']['reviewer'],'理由':event['audit']['reason'],
                     '调整前':event['previous_plan']['material_ids'],'调整后':event['teacher_plan']['material_ids'],
                     '指导语':event['teacher_plan']['guidance'],'前摘要':event['before_sha256'],
                     '后摘要':event['after_sha256']})


def _review_fields(bundle, sid, nid, rid, plan):
    iid = plan['recommendation']['indicator_id']
    allowed = re.split('[、,，\\s]+',bundle['dataset']['nodes'][nid]['可用题目编号'])
    options = [qid for qid in allowed if iid in re.split('[、,，\\s]+',bundle['dataset']['questions'].get(qid,{}).get('指标编号',''))]
    identity = (sid,nid,rid,plan['expected_sha256'])
    if st.session_state.get('review_identity') != identity or 'review_materials' not in st.session_state:
        st.session_state.update({'review_identity':identity,'review_materials':plan['material_ids'],
                                 'review_guidance':plan['guidance'],'review_follow_up':plan['follow_up'],'review_reason':''})
    with st.form('review_form'):
        reviewer = st.text_input('操作者姓名或标记',key='reviewer',max_chars=80)
        materials = st.multiselect('调整后的练习（同节点、同指标，1至6道）',options,key='review_materials',max_selections=6)
        guidance = st.text_area('给学生的指导语',key='review_guidance',max_chars=2000)
        follow_up = st.text_area('再次观察条件',key='review_follow_up',max_chars=2000)
        reason = st.text_area('调整理由',key='review_reason',max_chars=1000)
        submitted = st.form_submit_button('保存教师调整',type='primary')
    if submitted:
        try:
            submit_review(bundle,{'student_id':sid,'node_id':nid,'recommendation_id':rid,
                                 'expected_sha256':plan['expected_sha256'],'reviewer':reviewer,'reason':reason,
                                 'material_ids':materials,'guidance':guidance,'follow_up':follow_up})
            st.session_state['flash_message']='教师调整已保存，并同步到学生学习计划。原始成绩与机器建议均保留。'
            st.rerun()
        except (ValueError,OSError) as error:
            st.error(str(error))
