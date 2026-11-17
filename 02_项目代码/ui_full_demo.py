"""Teacher and student workspaces over the shared evidence runtime."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import streamlit as st
import streamlit.components.v1 as components

from product_service import record_practice, student_view
from diagnosis import HINTS
from response_feedback import response_feedback
from teacher_review import fingerprint
from ui_common import page_heading, rate
from project_paths import public_demo_mode


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_STORAGE_ROOT = ROOT / 'outputs/full_demo_runtime'
TEACHER_PAGES = ['教师工作台', '个人画像', '逐题诊断', '知识星图', '智能任务单', 'Agent分析', '数据治理']
STUDENT_PAGES = ['学习首页', '我的知识探索', '逐题讲解', '我的练习任务', '自检反馈']
_graph = components.declare_component('zhixi_knowledge_graph', path=str(Path(__file__).parent / 'knowledge_graph_component'))


def runtime_for(bundle):
    from agent_runtime import DemoRuntime
    identity = fingerprint(bundle['dataset']['sources'])
    if st.session_state.get('full_runtime_identity') != identity:
        st.session_state['full_runtime'] = DemoRuntime(bundle, storage_root=RUNTIME_STORAGE_ROOT)
        st.session_state['full_runtime_identity'] = identity
    return st.session_state['full_runtime']


def render_full_demo(page, bundle, sid, nid, reviews, practice, *, is_teacher):
    runtime = runtime_for(bundle)
    context = (runtime.kb.snapshot_hash, sid, nid, is_teacher)
    if st.session_state.get('full_view_context') != context:
        for key in ('full_profile_result', 'full_diagnosis_result', 'full_agent_result', 'full_practice_candidates',
                    'full_profile_external_authorized', 'full_diagnosis_external_authorized', 'full_agent_external_authorized'):
            st.session_state.pop(key, None)
        st.session_state['full_view_context'] = context
    if notice := st.session_state.pop('full_notice', None):
        st.success(notice)
    state = runtime.state()
    st.caption(f'证据快照 {state["snapshot_hash"][:16]} · '
               + ('DeepSeek后端已配置，按操作调用' if state['model_configured'] else '本地确定性核验已启用'))
    handlers = {
        '教师工作台': lambda: _teacher_dashboard(runtime, bundle, nid),
        '个人画像': lambda: _profile(runtime, bundle, sid, nid, reviews),
        '逐题诊断': lambda: _step_explanation(runtime, bundle, sid, nid, is_teacher=True),
        '知识星图': lambda: _knowledge_explorer(runtime, bundle, sid, nid, is_teacher=True),
        '智能任务单': lambda: _assignment_studio(runtime, bundle, sid),
        'Agent分析': lambda: _agent_workspace(runtime, sid, nid),
        '数据治理': lambda: _governance(runtime, bundle),
        '学习首页': lambda: _student_home(runtime, bundle, sid, nid, reviews),
        '我的知识探索': lambda: _knowledge_explorer(runtime, bundle, sid, nid, is_teacher=False),
        '逐题讲解': lambda: _step_explanation(runtime, bundle, sid, nid, is_teacher=False),
        '我的练习任务': lambda: _student_tasks(runtime, bundle, sid, nid, reviews, practice),
        '自检反馈': lambda: _student_feedback(runtime, bundle, sid, nid, reviews, practice),
    }
    try:
        handlers[page]()
    except ValueError as error:
        _runtime_error(error)


def _runtime_error(error):
    messages = {'STALE_SNAPSHOT': '证据快照已变化，请刷新数据后重新检查。',
                'ACCESS_DENIED': '当前演示身份不能执行这项操作。',
                'CONFIRMATION_REQUIRED': '请先由教师检查当前版本并明确确认。',
                'INSUFFICIENT_ITEMS': '授权题库中的题目数量不足，请减少题量或调整目标。',
                'EVIDENCE_NOT_FOUND': '所需原文尚未进入知识库，请补充并核对来源。'}
    st.error(messages.get(str(error), '本次操作未完成，请核对输入、证据版本与审核状态。'))
    st.caption(str(error))


def _external_authorization(runtime, key):
    passages = [item for item in runtime.kb.evidence.values() if item.get('type') == 'SOURCE_PASSAGE']
    needs_permission = not runtime.kb.is_self_created or any(item.get('external_authorized') is not True for item in passages)
    if not needs_permission:
        return True
    confirmed = st.checkbox('我已取得将选定学生作答和必要知识原文发送至DeepSeek用于本次分析的许可', key=key)
    runtime.grant_external_authorization(runtime.kb.snapshot_hash, confirmed, audience='TEACHER')
    return confirmed


def _metric(runtime, sid, nid, knowledge_ref=None, *, audience='TEACHER'):
    filters = {'student_refs': [sid], 'node_ids': [nid]}
    if knowledge_ref:
        filters['knowledge_refs'] = [knowledge_ref]
    response = runtime.call_tool('compute_stats', {'operation': 'student_summary', 'filters': filters,
                                 'denominator_policy': 'scored_only'}, audience=audience, student_id=sid)
    if not response.get('ok'):
        raise ValueError(response.get('error', {}).get('code', 'STATISTICS_UNAVAILABLE'))
    return response['data']['metrics'][0]


def _teacher_dashboard(runtime, bundle, nid):
    page_heading('教师工作台', '先查看本次有效记录，再从具体作答选择教学行动。')
    rows = [row for row in runtime.kb.snapshot['records'] if row['node_id'] == nid]
    scored = [row for row in rows if row['include_in_score_stats']]
    students = bundle['analysis']['nodes'][nid]['students']
    columns = st.columns(4)
    columns[0].metric('本次学生', len(students))
    columns[1].metric('有效已评分事件', len(scored))
    columns[2].metric('当前有效得分 / 满分', f'{sum(row["effective_score"] for row in scored)} / {sum(row["max_score"] for row in scored)}')
    columns[3].metric('待评分 / 明确未作答', f'{sum(row["status"] == "待评分" for row in rows)} / {sum(row["status"] == "未作答" for row in rows)}')
    st.caption('分母为当前有效、已评分事件的满分和。没有记录的题目保持未观察。')
    st.subheader('从学生的本次表现开始')
    summary = runtime.call_tool('compute_stats', {'operation': 'student_summary',
        'filters': {'student_refs': list(students), 'node_ids': [nid]}, 'denominator_policy': 'scored_only'})
    if not summary.get('ok'):
        raise ValueError(summary.get('error', {}).get('code', 'STATISTICS_UNAVAILABLE'))
    overview = []
    for metric in summary['data']['metrics']:
        overview.append({'学生': metric['student_id'], '本次得分': metric['numerator'], '对应满分': metric['denominator'],
                         '已评分事件 n': metric['n'], '覆盖率': rate(metric['coverage']),
                         '当前得分率': rate(metric.get('rate'))})
    st.dataframe(overview, hide_index=True, width='stretch')
    st.subheader('建议先做的教学动作')
    issues = []
    for sid, student in students.items():
        for observation in student['observations']:
            feedback = response_feedback(observation['question'], observation['reference_answer'],
                                         observation['student_answer'], observation['student_process'])
            if feedback['answer_validity'] == 'INVALID':
                issues.append({'学生': sid, '题目': observation['question'], '下一步': feedback['next_action'],
                               '依据': observation['record_id']})
    if issues:
        st.dataframe(issues, hide_index=True, width='stretch')
    else:
        st.info('目前可核验的作答中暂无需优先修正的最终答案，请继续检查过程与评分依据。')
    _source_warnings(bundle)


def _source_warnings(bundle):
    if any(issue['code'] in {'source_missing', 'source_changed'} for issue in bundle['curriculum']['issues']):
        st.info('当前教材或课标原件尚未就绪。知识要求和题目可查看，教材逐字引文需补充原件后核对。')


def _profile(runtime, bundle, sid, nid, reviews):
    page_heading('个人画像', f'{sid} · 以本次作答、知识覆盖与可核验步骤组织学习发现。')
    metric = _metric(runtime, sid, nid)
    columns = st.columns(3)
    columns[0].metric('本次观测得分率', rate(metric.get('rate')))
    columns[1].metric('有效得分 / 满分', f'{metric["numerator"]} / {metric["denominator"]}')
    columns[2].metric('已评分事件 n', metric['n'])
    st.caption(f'当前口径为已评分事件，覆盖率 {rate(metric["coverage"])}。结论仅覆盖本次已观察题目。')
    _student_findings(runtime, bundle, sid, nid)
    student = student_view(bundle, sid, nid, reviews)
    st.subheader('可以开始的学习行动')
    for recommendation in student['recommendations']:
        st.write(recommendation['requirement_ref']['观察指标'])
        st.write(recommendation.get('teacher_guidance') or recommendation['reason'])
        st.caption('作答依据 ' + ('、'.join(recommendation['evidence_ids']) or '需补充观察'))
    authorized = _external_authorization(runtime, 'full_profile_external_authorized')
    if st.button('DeepSeek独立分析本次画像', key='full_profile_ds', disabled=not authorized):
        with st.spinner('正在读取知识依据并进行受控分析…'):
            response = runtime.run_request(f'分析学生 {sid} 在 {nid} 的作答并给出具体学习建议',
                                           student_id=sid, use_model=True)
        st.session_state['full_profile_result'] = response
    if saved := st.session_state.get('full_profile_result'):
        _runtime_result(saved)


def _student_findings(runtime, bundle, sid, nid):
    student = bundle['analysis']['nodes'][nid]['students'][sid]
    strengths, changes = [], []
    for observation in student['observations']:
        feedback = response_feedback(observation['question'], observation['reference_answer'],
                                     observation['student_answer'], observation['student_process'])
        claim = {'assertion_type': 'OBSERVED_FACT', 'assertion_text': feedback['next_action'],
                 'evidence_refs': [observation['record_id']], 'derived_by': 'deterministic', 'verifier_id': 'G1'}
        verified = runtime.verify_claims([claim], audience='STUDENT', student_id=sid)['assertions'][0]['result'] == 'verified'
        if not verified:
            continue
        if feedback['answer_validity'] == 'VALID' and all(step['local_validity'] == 'VALID' for step in feedback['steps']):
            strengths.append(observation)
        elif feedback['answer_validity'] == 'INVALID':
            changes.append((observation, feedback))
    st.subheader('本次已经做对的部分')
    if strengths:
        for observation in strengths[:3]:
            st.write(f'在“{observation["question"]}”中，你写出的最终结果与题目数学等价。')
            st.caption('作答依据 ' + observation['record_id'])
    else:
        st.info('目前没有足够的自动核验结果来描述正确步骤。可以先补充一次带过程的作答。')
    st.subheader('下一步检查哪里')
    for observation, feedback in changes[:3]:
        st.warning(f'{observation["question"]} {feedback["next_action"]}')
        st.caption('作答依据 ' + observation['record_id'])
    if not changes:
        st.info('继续说明每一步的依据，并完成下一道可用练习，观察相同方法能否独立使用。')


def _student_home(runtime, bundle, sid, nid, reviews):
    page_heading('学习首页', f'{sid} · 从自己的实际步骤开始，安排下一次练习。')
    _student_findings(runtime, bundle, sid, nid)
    student = student_view(bundle, sid, nid, reviews)
    if student['recommendations']:
        recommendation = student['recommendations'][0]
        st.subheader('现在可以做什么')
        st.write(recommendation.get('teacher_guidance') or recommendation['steps'][0]['activity'])
        st.caption('可到“我的练习任务”开始，到“自检反馈”保存作答并核对。')


def _step_explanation(runtime, bundle, sid, nid, *, is_teacher):
    page_heading('逐题诊断' if is_teacher else '逐题讲解', f'{sid} · 逐项核对实际数学操作和原始作答。')
    observations = bundle['analysis']['nodes'][nid]['students'][sid]['observations']
    if not observations:
        st.info('本次还没有有效已评分作答。已有提交可在待评分状态下保留，教师补评后再查看统计。')
        return
    by_id = {item['record_id']: item for item in observations}
    selected = st.selectbox('选择作答', list(by_id), key='full_response_id',
                            format_func=lambda rid: by_id[rid]['question'])
    observation = by_id[selected]
    st.subheader(observation['question'])
    columns = st.columns(2)
    with columns[0]:
        st.markdown('**原始解题过程**')
        st.text(observation['student_process'] or '本次没有提供过程。')
    with columns[1]:
        st.markdown('**原始最终作答**')
        st.text(observation['student_answer'])
    feedback = response_feedback(observation['question'], observation['reference_answer'],
                                 observation['student_answer'], observation['student_process'])
    _feedback(feedback)
    with st.expander('评分依据和原文来源'):
        st.dataframe([{'要求': point['label'], '本次得分': observation['point_scores'].get(pid),
                       '满分': point['max_score']} for pid, point in observation['rubric'].items()],
                      hide_index=True, width='stretch')
        _evidence(runtime, observation['question_id'])
        st.caption('原始作答依据 ' + observation['record_id'])
    authorized = _external_authorization(runtime, 'full_diagnosis_external_authorized') if is_teacher else False
    if is_teacher and st.button('DeepSeek双路复核该作答', key='full_diagnose_ds', disabled=not authorized):
        with st.spinner('正在进行独立分析和数学核对…'):
            result = runtime.call_tool('analyze_responses', {'response_refs': [selected],
                'diagnostic_goal': '核对具体步骤、首错、评分依据并提出可执行建议',
                'verification_mode': 'A_AND_B', 'max_model_calls': 4}, student_id=sid)
        st.session_state['full_diagnosis_result'] = result
    if is_teacher and (result := st.session_state.get('full_diagnosis_result')):
        _runtime_result(result)


def _feedback(feedback):
    if feedback['answer_validity'] == 'VALID':
        st.success(feedback['next_action'])
    elif feedback['answer_validity'] == 'INVALID':
        st.warning(feedback['next_action'])
    else:
        st.info(feedback['next_action'])
    if feedback['steps']:
        names = {'VALID': '数学等价', 'INVALID': '不等价', 'UNRESOLVED': '待核对'}
        st.dataframe([{'步骤': step['step'], '原式': step['source'], '本步': step['target'],
                       '核验': names[step['local_validity']]} for step in feedback['steps']],
                      hide_index=True, width='stretch')
    for limitation in feedback['limitations']:
        st.caption(limitation)


def _knowledge_explorer(runtime, bundle, sid, nid, *, is_teacher):
    page_heading('知识星图' if is_teacher else '我的知识探索', '点击知识节点查看证据，选择两个节点检索关联练习。')
    nodes = runtime.kb.nodes
    response = runtime.call_tool('compute_stats', {'operation': 'indicator_distribution',
        'filters': {'student_refs': [sid], 'node_ids': [nid]}, 'denominator_policy': 'scored_only'},
        audience='TEACHER' if is_teacher else 'STUDENT', student_id=sid)
    if not response.get('ok'):
        raise ValueError(response.get('error', {}).get('code', 'STATISTICS_UNAVAILABLE'))
    metrics = {metric['indicator_id']: metric for metric in response['data']['metrics']}
    graph_nodes = []
    for identifier, node in nodes.items():
        if identifier not in metrics:
            raise ValueError('STATISTICS_UNAVAILABLE')
        metric = metrics[identifier]
        graph_nodes.append({'id': identifier, 'name': node['name'], 'n': metric['n'],
                            'coverage': metric['coverage'], 'rate': metric.get('rate')})
    edges = [{'source': relation['from'], 'target': relation['to'], 'label': relation['reason']}
             for relation in bundle['curriculum']['relations']
             if relation['from'] in nodes and relation['to'] in nodes]
    event = _graph(nodes=graph_nodes, edges=edges, snapshot_hash=runtime.kb.snapshot_hash,
                   default={'selected_node_ids': []}, key='full_knowledge_graph')
    if event and event.get('nonce') != st.session_state.get('full_graph_event_nonce'):
        selected = [identifier for identifier in event.get('selected_node_ids', []) if identifier in nodes]
        st.session_state['full_explore_nodes'] = selected[:2]
        st.session_state['full_graph_event_nonce'] = event.get('nonce')
    selected = st.multiselect('列表选择知识点', list(nodes), key='full_explore_nodes', max_selections=2,
                              format_func=lambda identifier: nodes[identifier]['name'])
    st.dataframe([{'知识名称': node['name'], '已评分事件 n': node['n'], '作答覆盖率': rate(node['coverage']),
                   '本次得分率': rate(node['rate'])} for node in graph_nodes], hide_index=True, width='stretch')
    for identifier in selected:
        with st.expander(nodes[identifier]['name'] + ' · 查看知识依据'):
            _evidence(runtime, identifier)
            passages = [item for item in runtime.kb.search(node_ids=[identifier], limit=100)
                        if item['entity_type'] == 'SOURCE_PASSAGE']
            if not passages:
                st.info('此知识点尚未关联可逐字核对的教材或课标片段，请补充来源后继续核对。')
            for passage in passages:
                st.markdown('**' + passage['display_name'] + '**')
                _evidence(runtime, passage['id'])
    if st.button('检索关联练习', key='full_find_practice', disabled=not selected):
        response = runtime.call_tool('find_practice_items', {'knowledge_refs': selected, 'item_count': 3,
            'source_policy': 'approved_practice_only', 'constraints': {}},
            audience='TEACHER' if is_teacher else 'STUDENT', student_id=sid)
        st.session_state['full_practice_candidates'] = {'selected': selected, 'response': response}
    stored = st.session_state.get('full_practice_candidates')
    if stored and stored['selected'] == selected:
        result = stored['response']
        if result.get('ok'):
            data = result['data']
            items = data.get('items', data.get('practice_items', []))
            for item in items:
                st.write(item['exact_text'])
                st.caption('知识关联 ' + '、'.join(nodes[ref]['name'] for ref in item.get('knowledge_refs', []) if ref in nodes))
            if data.get('shortfall'):
                st.info(f'本次可用题目不足，缺口 {data["shortfall"]} 道。')
        else:
            st.info('当前没有满足授权与目标范围的练习，请调整知识目标。')
    if is_teacher and st.button('按所选知识创建任务草稿', key='full_graph_create_draft', disabled=not selected):
        draft = runtime.create_draft({'student_refs': [sid], 'target_knowledge_refs': selected, 'item_count': 3, 'max_minutes': 15})
        st.session_state['full_active_draft'] = draft['draft_id']
        st.success('已创建待审核草稿，可到智能任务单继续编辑和确认。')
    _source_warnings(bundle)
    if is_teacher:
        _knowledge_import(runtime)
        _practice_library(runtime)


def _knowledge_import(runtime):
    if public_demo_mode():
        st.caption('公开演示使用自建知识目录，服务器文件导入已关闭。')
        return
    nodes = runtime.kb.nodes
    with st.expander('导入知识原文'):
        st.caption('从已获准使用的本地文本、Markdown或PDF导入精确片段，保存实际文件摘要与来源定位。')
        with st.form('full_import_passage_form'):
            path_text = st.text_input('本地原文文件路径', key='full_source_path')
            title = st.text_input('资料名称', key='full_source_title')
            edition = st.text_input('版本或版次', key='full_source_edition')
            columns = st.columns(2)
            physical = columns[0].number_input('PDF物理页（文本文件填0）', min_value=0, max_value=10000, value=0,
                                               key='full_source_physical')
            printed = columns[1].text_input('印刷页或页码范围', key='full_source_printed')
            exact_text = st.text_area('来源中的精确原文片段', key='full_source_exact', max_chars=12000)
            node_ids = st.multiselect('关联知识', list(nodes),
                                      format_func=lambda identifier: nodes[identifier]['name'], key='full_source_nodes')
            reviewer = st.text_input('原文核对者', key='full_source_reviewer')
            approved = st.checkbox('已确认资料使用范围并逐字核对片段', key='full_source_confirm')
            external = st.checkbox('允许将此片段及必要来源信息发送至DeepSeek用于教学分析', key='full_source_external')
            submitted = st.form_submit_button('校验原文并导入知识库')
        if submitted:
            if not approved or not all(value.strip() for value in (path_text, title, edition, exact_text, reviewer)) or not node_ids:
                st.warning('请填写完整来源信息、关联知识并确认逐字核对。')
                return
            path = Path(path_text).expanduser().resolve()
            if not path.is_file():
                st.warning('原文文件尚未找到，请核对本地路径。')
                return
            source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            identity = fingerprint({'source_hash': source_hash, 'text': exact_text, 'nodes': node_ids,
                                    'title': title, 'edition': edition, 'physical': physical, 'printed': printed,
                                    'external_authorized': external})
            passage = {'passage_id': 'PASSAGE-' + identity[:20], 'exact_text': exact_text,
                'source_path': str(path), 'source_hash': source_hash, 'resource_title': title,
                'edition': edition, 'node_ids': node_ids, 'physical_page': int(physical) or None,
                'printed_page': printed or None, 'external_authorized': external}
            runtime.kb.import_passages([passage], reviewer_id=reviewer.strip(),
                expected_snapshot_hash=runtime.kb.snapshot_hash, review_context='DEMO_TEACHER')
            st.session_state.pop('full_runtime_identity', None)
            st.session_state['full_notice'] = '原文片段已逐字校验并进入知识库，相关证据快照已更新。'
            st.rerun()


def _practice_library(runtime):
    knowledge_nodes = runtime.kb.nodes
    with st.expander('练习库登记与审核'):
        st.caption('将已获准使用的补充练习登记到当前批次，候选题经内容核对与明确授权后才能用于任务单。')
        inventory = {**runtime.kb.practice_items,
                     **{identity: item for identity, item in runtime.kb.evidence.items()
                        if item.get('type') == 'PRACTICE_ITEM'}}
        if inventory:
            st.dataframe([{'练习': item['exact_text'], '题源状态': item['source_status'],
                           '审核状态': item.get('review_status', '待核对'),
                           '来源': item.get('source_description', item.get('source_ref', {}).get('source_description',
                               item.get('source_ref', {}).get('source_file_id', '当前题库')))}
                          for item in inventory.values()], hide_index=True, width='stretch')
        registered = [identity for identity, item in inventory.items() if item.get('type') == 'PRACTICE_ITEM']
        if registered:
            _practice_review(runtime, registered, inventory)
        with st.form('full_practice_library_form'):
            identifier = st.text_input('练习版本编号', key='full_library_id')
            question = st.text_area('补充练习题干', key='full_library_question', max_chars=12000)
            answer = st.text_input('补充练习参考答案', key='full_library_answer')
            explanation = st.text_area('补充练习参考解析', key='full_library_explanation', max_chars=12000)
            nodes = st.multiselect('考查知识', list(knowledge_nodes),
                format_func=lambda value: knowledge_nodes[value]['name'], key='full_library_nodes')
            source = st.text_input('题目来源与使用依据', key='full_library_source')
            author = st.text_input('作者或来源作者', key='full_library_author')
            reviewer = st.text_input('练习核对者', key='full_library_reviewer')
            authorized = st.checkbox('已确认此题允许用于本地教学练习', key='full_library_authorized')
            approved = st.checkbox('已核对题干、答案、解析和知识关联', key='full_library_approved')
            submitted = st.form_submit_button('登记练习与审核状态')
        if submitted:
            if not nodes or not all(value.strip() for value in (identifier, question, answer, explanation, source, author, reviewer)):
                st.warning('请填写完整题目、来源、作者、核对者及知识关联。')
                return
            item = {'practice_id': identifier.strip(), 'exact_text': question, 'reference_answer': answer,
                'reference_explanation': explanation, 'knowledge_refs': nodes, 'source_description': source,
                'author': author, 'authorized_use': authorized}
            runtime.kb.import_practice_items([item], reviewer_id=reviewer.strip(),
                expected_snapshot_hash=runtime.kb.snapshot_hash, approved=approved,
                review_context='DEMO_TEACHER' if approved else 'AUTOMATED')
            st.session_state.pop('full_runtime_identity', None)
            st.session_state['full_notice'] = '练习登记已保存，任务选题会按授权和审核状态筛选。'
            st.rerun()


def _practice_review(runtime, identifiers, inventory):
    selected = st.selectbox('选择已登记的练习进行审核', identifiers, key='full_library_review_id',
        format_func=lambda identifier: inventory[identifier]['exact_text'])
    item = runtime.kb.get_practice_for_review(selected)
    st.write(item['exact_text'])
    st.text(item['reference_answer'])
    st.write(item['reference_explanation'])
    st.caption('来源 ' + item['source_description'] + ' · 作者 ' + item['author'])
    identity = (selected, runtime.kb.snapshot_hash)
    if st.session_state.get('full_library_review_identity') != identity or 'full_library_review_authorized' not in st.session_state:
        st.session_state.update({'full_library_review_identity': identity,
            'full_library_review_authorized': item['authorized_use'], 'full_library_review_content': False})
    decisions = {'approve': '通过练习审核', 'withdraw': '撤回当前练习审核'}
    decision = st.selectbox('练习审核决定', list(decisions), format_func=decisions.get, key='full_library_review_decision')
    reviewer = st.text_input('练习审核者标记', key='full_library_review_actor')
    authorized = st.checkbox('已确认这份练习的本地使用授权', key='full_library_review_authorized')
    content = st.checkbox('已核对当前题干、答案、解析与教学目标', key='full_library_review_content')
    if st.button('保存练习审核决定', key='full_library_save_review',
                 disabled=not reviewer.strip() or decision == 'approve' and not content):
        runtime.kb.review_practice(selected, reviewer_id=reviewer.strip(), approved=decision == 'approve',
            authorized_use=authorized, expected_snapshot_hash=runtime.kb.snapshot_hash, review_context='DEMO_TEACHER')
        st.session_state.pop('full_runtime_identity', None)
        st.session_state['full_notice'] = '练习审核决定已追加保存，题库可用状态已更新。'
        st.rerun()


def _evidence(runtime, identifier):
    item = runtime.kb.get(identifier)
    claim = {'assertion_type': 'SOURCE_QUOTE', 'assertion_text': item['exact_text'],
             'evidence_refs': [identifier], 'derived_by': 'deterministic', 'verifier_id': 'FACT_ACCESS'}
    gate = runtime.verify_claims([claim])
    if gate['assertions'][0]['result'] != 'verified':
        st.warning('该来源文本尚未通过逐字引用核对，请先检查知识库。')
        return
    st.text(item['exact_text'])
    source = item.get('source_ref', {})
    st.caption('来源 ' + str(source.get('source_file', source.get('source_file_id', source.get('file', '当前数据文件')))))
    with st.expander('来源版本和审核记录'):
        st.json({'evidence_id': item.get('id', identifier), 'source': source, 'review_status': item.get('review_status')}, expanded=False)


def _assignment_studio(runtime, bundle, sid):
    page_heading('智能任务单', '选择学习目标，编辑真实练习，经教师确认后导出当前版本。')
    nodes = runtime.kb.nodes
    student_ids = list(bundle['dataset']['students'])
    with st.container(border=True):
        columns = st.columns([2, 2, 1])
        targets = columns[0].multiselect('练习对象', student_ids, default=[sid], key='full_target_students')
        knowledge = columns[1].multiselect('学习目标', list(nodes), default=list(nodes)[:1],
                                           format_func=lambda identifier: nodes[identifier]['name'], key='full_target_knowledge')
        amount = columns[2].number_input('题量', 1, 10, 3, key='full_target_count')
        if st.button('生成待审核任务草稿', key='full_create_draft', type='primary', disabled=not targets or not knowledge):
            draft = runtime.create_draft({'student_refs': targets, 'target_knowledge_refs': knowledge,
                                         'item_count': int(amount), 'max_minutes': 15})
            st.session_state['full_active_draft'] = draft['draft_id']
            st.session_state['full_draft_selector'] = draft['draft_id']
            st.session_state['full_notice'] = '任务草稿已创建，请核对题目、目标与对象。'
            st.rerun()
    drafts = runtime.list_drafts()
    if not drafts:
        st.info('还没有任务草稿。先选择对象和学习目标。')
        return
    by_id = {draft['draft_id']: draft for draft in drafts}
    if st.session_state.get('full_draft_selector') not in by_id:
        st.session_state['full_draft_selector'] = st.session_state.get('full_active_draft', drafts[-1]['draft_id'])
    selected = st.selectbox('查看草稿', list(by_id), key='full_draft_selector',
                            format_func=lambda identifier: f'{identifier} · 版本{by_id[identifier]["revision"]}')
    st.session_state['full_active_draft'] = selected
    draft = runtime.get_draft(selected)
    _draft_editor(runtime, draft, student_ids)


def _draft_editor(runtime, draft, student_ids):
    nodes = runtime.kb.nodes
    identity = (draft['draft_id'], draft['revision'])
    if st.session_state.get('full_draft_edit_identity') != identity or 'full_draft_questions' not in st.session_state:
        st.session_state.update({'full_draft_edit_identity': identity,
            'full_draft_questions': [item['practice_item_id'] for item in draft['items']],
            'full_draft_students': draft['target_student_refs'], 'full_draft_guidance': draft.get('guidance', ''),
            'full_draft_order': '、'.join(item['practice_item_id'] for item in draft['items']),
            'full_teacher_confirm': False, 'full_content_reviewed': False})
    st.caption(f'当前版本 {draft["revision"]} · 审核状态 {draft["status"]} · 自动复核 {draft["validation_status"]}')
    for warning in draft.get('warnings', []):
        st.info(str(warning))
    questions = runtime.kb.practice_items
    eligible = [identifier for identifier, question in questions.items() if question['source_status'] == 'APPROVED_PRACTICE']
    columns = st.columns([2, 1])
    with columns[0]:
        selected = st.multiselect('换题与增减题目', eligible, key='full_draft_questions',
                                  format_func=lambda identifier: questions[identifier]['exact_text'])
        target_students = st.multiselect('调整任务对象', student_ids, key='full_draft_students')
        order = st.text_input('题目顺序（用顿号分隔题号）', key='full_draft_order')
        guidance = st.text_area('任务指导语', key='full_draft_guidance')
        overrides = {}
        for item in draft['items']:
            qid = item['practice_item_id']
            with st.expander(item['exact_text'] + ' · 编辑或查看答案'):
                text = st.text_area('题干', value=item['exact_text'], key=f'full_item_text_{draft["draft_id"]}_{draft["revision"]}_{qid}')
                answer = st.text_input('参考答案', value=item['answer'], key=f'full_item_answer_{draft["draft_id"]}_{draft["revision"]}_{qid}')
                if text != item['exact_text'] or answer != item['answer']:
                    overrides[qid] = {'exact_text': text, 'answer': answer}
                st.caption('题目来源 ' + item['source_status'])
        content_reviewed = st.checkbox('已核对修改后的题目与学习目标', key='full_content_reviewed')
        if st.button('保存为新版本并复核', key='full_save_draft'):
            ordered = [value.strip() for value in order.replace(',', '、').replace('，', '、').split('、') if value.strip()]
            if set(ordered) == set(selected) and len(ordered) == len(selected):
                selected = ordered
            changes = {'question_ids': selected, 'target_student_refs': target_students, 'guidance': guidance}
            if overrides:
                changes.update({'item_overrides': overrides, 'teacher_content_reviewed': content_reviewed})
            runtime.edit_draft(draft['draft_id'], draft['revision'], changes)
            st.session_state['full_notice'] = '已保存新版本并重新执行题源、数学和内容检查。'
            st.session_state.pop('full_export', None)
            st.rerun()
    with columns[1]:
        st.subheader('推荐依据')
        for ref in dict.fromkeys(ref for item in draft['items'] for ref in item.get('knowledge_refs', [])):
            node = nodes.get(ref, {})
            st.write(node.get('name', ref))
        st.write('练习对象 ' + '、'.join(draft['target_student_refs']))
        if st.button('重新验证当前版本', key='full_validate_draft'):
            runtime.validate_draft(draft['draft_id'], draft['revision'])
            st.session_state['full_notice'] = '当前版本已重新核对，请查看复核状态。'
            st.rerun()
        teacher = st.text_input('演示教师标记', key='full_teacher_id')
        confirmed = st.checkbox('我已检查对象、题目、答案与当前版本', key='full_teacher_confirm')
        if st.button('教师确认当前版本', key='full_confirm_draft', disabled=not confirmed or not teacher.strip()):
            runtime.confirm_draft(draft['draft_id'], draft['revision'], teacher.strip(),
                                  f'{draft["draft_id"]}:{draft["revision"]}:{teacher.strip()}')
            st.session_state['full_notice'] = '教师已确认当前版本，可以准备导出。'
            st.rerun()
        if st.button('准备导出文件', key='full_prepare_export', disabled=draft['status'] not in {'SUBMITTED_FOR_REVIEW', 'EXPORTED'}):
            st.session_state['full_export'] = runtime.export_draft(draft['draft_id'], draft['revision'])
        exported = st.session_state.get('full_export')
        if exported and (exported['draft_id'], exported['revision']) == identity and draft['status'] in {'SUBMITTED_FOR_REVIEW', 'EXPORTED'}:
            st.download_button('下载已确认任务单', exported['content'], file_name=exported['filename'],
                               mime='application/zip', key='full_download_assignment')
            st.success('导出文件已就绪，可下载给教师留存。')
    st.caption('当前为本机演示教师身份。每次编辑均保留版本，教师确认与导出会记录审计事件。')


def _agent_workspace(runtime, sid, nid):
    page_heading('Agent分析', '用自然语言提出教学目标，查看受控工具调用、证据和验证结果。')
    goal = st.text_area('希望完成什么', value=f'分析 {sid} 在 {nid} 的作答，给出有依据的练习建议',
                        key='full_agent_goal', max_chars=2000)
    use_model = st.checkbox('使用DeepSeek规划与双路诊断', value=False, key='full_agent_use_model')
    authorized = _external_authorization(runtime, 'full_agent_external_authorized')
    st.caption('勾选后按预算调用真实模型。当前演示班级中的查询仍经过范围、快照和输出检查。')
    if st.button('执行分析目标', key='full_run_agent', type='primary', disabled=not goal.strip() or use_model and not authorized):
        with st.spinner('正在按目标组织工具并核对证据…'):
            st.session_state['full_agent_result'] = runtime.run_request(goal.strip(), student_id=sid, use_model=use_model)
            st.session_state['full_agent_request'] = (goal.strip(), use_model, sid, nid, runtime.kb.snapshot_hash)
    response = st.session_state.get('full_agent_result')
    if response and st.session_state.get('full_agent_request') == (goal.strip(), use_model, sid, nid, runtime.kb.snapshot_hash):
        _runtime_result(response)


def _runtime_result(response):
    if not response.get('ok', True):
        error = response.get('error') or {}
        st.warning('本次请求未取得完整可用结果，请根据限制调整目标。')
        st.caption(error.get('message', error.get('code', '')))
    gate = response.get('gate') or response.get('data', {}).get('gate') or {}
    if gate:
        names = {'GATE_PASS': '输出依据已通过核对', 'GATE_PARTIAL': '部分内容需要进一步核对',
                 'GATE_BLOCKED': '当前结果尚不能作为已核验结论'}
        st.caption(names.get(gate.get('status'), '输出依据待核对'))
    claims = response.get('claims', response.get('data', {}).get('claims', []))
    if claims:
        st.subheader('分析发现与行动')
        for claim in claims:
            text = claim.get('assertion_text', claim.get('text', ''))
            verification = next((item.get('result') for item in gate.get('assertions', [])
                                 if item.get('assertion_text') == text and
                                 item.get('evidence_refs') == claim.get('evidence_refs')), None)
            if verification == 'refuted':
                st.warning('证据反驳的候选结论：' + text)
            elif verification == 'verified':
                st.write(text)
            else:
                st.info('需要继续核对：' + text)
            if claim.get('evidence_refs'):
                st.caption('依据 ' + '、'.join(claim['evidence_refs']))
    with st.expander('执行摘要与验证依据', expanded=not claims):
        st.json({'plan': response.get('plan'), 'steps': response.get('steps'), 'gate': gate,
                 'result': response.get('result', response.get('data'))}, expanded=False)
    for limitation in response.get('limitations', []):
        st.info(str(limitation))


def _student_tasks(runtime, bundle, sid, nid, reviews, practice):
    page_heading('我的练习任务', f'{sid} · 阅读老师确认的任务，逐步完成自己的练习。')
    drafts = [draft for draft in runtime.list_drafts(student_id=sid)
              if draft['status'] in {'SUBMITTED_FOR_REVIEW', 'EXPORTED'}]
    if drafts:
        for draft in drafts:
            with st.expander(f'老师确认的任务 · 版本{draft["revision"]}', expanded=True):
                st.write(draft.get('guidance') or '独立完成题目，写清每一步的依据。')
                for item in draft['items']:
                    st.write(item['exact_text'])
                if st.button('开始这份任务', key=f'full_start_task_{draft["draft_id"]}_{draft["revision"]}'):
                    item = draft['items'][0]
                    st.session_state['full_jump_assignment_item'] = f'{draft["draft_id"]}:{draft["revision"]}:{item["draft_item_id"]}'
                    st.session_state['full_jump_source'] = 'confirmed_assignments'
                    st.session_state['full_jump_page'] = '自检反馈'
                    st.rerun()
    else:
        st.info('老师尚未确认任务单。你可以先尝试当前学习建议中的自建练习。')
    student = student_view(bundle, sid, nid, reviews)
    for rec in student['recommendations']:
        with st.container(border=True):
            st.subheader(rec['requirement_ref']['观察指标'])
            st.write(rec.get('teacher_guidance') or rec['steps'][0]['activity'])
            for qid in rec['question_ids']:
                st.write(bundle['dataset']['questions'][qid]['题干或内容'])
    st.caption('到“自检反馈”保存答案、过程和提示使用情况。')


def _student_feedback(runtime, bundle, sid, nid, reviews, practice):
    page_heading('自检反馈', f'{sid} · 先写自己的过程，再查看数学核验与下一步动作。')
    if source := st.session_state.pop('full_jump_source', None):
        st.session_state['full_feedback_source'] = source
    source_names = {'learning_recommendations': '当前学习建议中的练习', 'confirmed_assignments': '老师确认的任务'}
    source = st.radio('练习来源', list(source_names), format_func=source_names.get, key='full_feedback_source')
    if source == 'confirmed_assignments':
        _assignment_feedback(runtime, sid)
        return
    student = student_view(bundle, sid, nid, reviews)
    rec_by_question = {qid: rec for rec in student['recommendations'] for qid in rec['question_ids']}
    allowed = {item.get('question_id', item.get('practice_id')) for item in runtime.kb.practices(item_count=30)}
    choices = [qid for qid in rec_by_question if qid in allowed]
    if not choices:
        st.info('目前没有符合当前节点和题源权限的自检题，请老师补充练习。')
        return
    qid = st.selectbox('本次自检题目', choices, key='full_practice_question',
                       format_func=lambda value: bundle['dataset']['questions'][value]['题干或内容'])
    question = bundle['dataset']['questions'][qid]
    rec = rec_by_question[qid]
    hints = list(dict.fromkeys(hint for step in rec['steps'] for hint in step['hints']))[:3]
    hint_level = st.slider('需要几级提示', 0, len(hints), 0, key='full_practice_hint')
    for hint in hints[:hint_level]:
        st.info(hint)
    with st.form('full_practice_form'):
        answer = st.text_area('我的最终答案', key='full_practice_answer', max_chars=4000)
        process = st.text_area('我的解题过程', key='full_practice_process', max_chars=12000)
        submitted = st.form_submit_button('保存自检记录', type='primary')
    if submitted:
        try:
            record = record_practice(bundle, {'student_id': sid, 'node_id': nid,
                'recommendation_id': rec['recommendation_id'], 'question_id': qid,
                'answer': answer, 'process': process, 'hint_level': hint_level})
            st.session_state['full_last_feedback'] = record
        except (ValueError, OSError) as error:
            _runtime_error(error)
    latest = st.session_state.get('full_last_feedback')
    if latest and latest['student_id'] == sid and latest['node_id'] == nid:
        _feedback(latest['feedback'])
        st.caption('记录已保存，教师可复核；本地新增练习与原始成绩分开保留。')
    own = [item for item in practice if item['student_id'] == sid and item['node_id'] == nid]
    if own:
        with st.expander('已保存的自检记录'):
            for item in reversed(own[-10:]):
                st.write(bundle['dataset']['questions'].get(item['question_id'], {}).get('题干或内容', item['question_id']))
                st.text(item['answer'])
                if item.get('feedback'):
                    _feedback(item['feedback'])


def _assignment_feedback(runtime, sid):
    choices = {}
    for draft in runtime.list_drafts(student_id=sid):
        if draft['status'] not in {'SUBMITTED_FOR_REVIEW', 'EXPORTED'}:
            continue
        for item in draft['items']:
            token = f'{draft["draft_id"]}:{draft["revision"]}:{item["draft_item_id"]}'
            choices[token] = (draft, item)
    if not choices:
        st.info('目前还没有老师确认的有效任务，请先查看当前学习建议，或请老师核对任务单。')
        return
    pending = st.session_state.pop('full_jump_assignment_item', None)
    if pending in choices:
        st.session_state['full_assignment_item'] = pending
    if st.session_state.get('full_assignment_item') not in choices:
        st.session_state['full_assignment_item'] = next(iter(choices))
    token = st.selectbox('选择老师确认的题目', list(choices), key='full_assignment_item',
                         format_func=lambda value: f'{choices[value][1]["exact_text"]} · 版本{choices[value][0]["revision"]}')
    draft, item = choices[token]
    st.subheader(item['exact_text'])
    st.caption(f'老师确认的任务 · 版本{draft["revision"]} · 本次反馈按该版本题干和答案核对。')
    if draft.get('guidance'):
        st.info(draft['guidance'])
    hints = list(dict.fromkeys(hint for ref in item['knowledge_refs'] for hint in HINTS.get(ref, [])))[:3]
    level = st.slider('任务提示层级', 0, len(hints), 0, key='full_assignment_hint') if hints else 0
    for hint in hints[:level]:
        st.info(hint)
    identity = (token, sid)
    if st.session_state.get('full_assignment_answer_identity') != identity or 'full_assignment_answer' not in st.session_state:
        st.session_state.update({'full_assignment_answer_identity': identity,
                                 'full_assignment_answer': '', 'full_assignment_process': ''})
    with st.form('full_assignment_form'):
        answer = st.text_area('我的任务答案', key='full_assignment_answer', max_chars=4000)
        process = st.text_area('我的任务解题过程', key='full_assignment_process', max_chars=12000)
        submitted = st.form_submit_button('保存任务作答', type='primary')
    if submitted:
        record = runtime.submit_assignment_answer(draft['draft_id'], draft['revision'], item['draft_item_id'],
            sid, answer, process, hint_level=level)
        st.session_state['full_assignment_feedback'] = record
    saved = st.session_state.get('full_assignment_feedback')
    if saved and (saved['draft_id'], saved['revision'], saved['draft_item_id'], saved['student_id']) == (
            draft['draft_id'], draft['revision'], item['draft_item_id'], sid):
        _feedback(saved['feedback'])
        st.caption('本次作答已绑定老师确认的任务版本保存，评分待教师复核。')
    history = runtime.list_assignment_answers(student_id=sid)
    if history:
        with st.expander('我的任务作答历史'):
            for record in reversed(history[-10:]):
                st.write(f'任务版本{record["revision"]} · {record["question_id"]} · {record["status"]}')
                st.text(record['answer'])


def _governance(runtime, bundle):
    page_heading('数据治理', '查看当前有效输入、原值、候选、教师决定和用途级纳入状态。')
    snapshot = runtime.kb.snapshot
    records = snapshot['records']
    columns = st.columns(3)
    columns[0].metric('当前作答事件', len(records))
    columns[1].metric('可用于成绩统计', sum(row['include_in_score_stats'] for row in records))
    columns[2].metric('源变化后失效审计', snapshot.get('stale_event_count', 0))
    st.caption('MODEL_READY快照 ' + runtime.kb.snapshot_hash)
    st.dataframe([{'学生': row['student_id'], '题目': bundle['dataset']['questions'].get(row['question_id'], {}).get('题干或内容', row['question_id']),
                   '评分状态': row['status'], '原始分': row['official_score'], '候选分': row['candidate_score'],
                   '当前有效分': row['effective_score'], '有效来源': row['effective_score_source'],
                   '成绩纳入': row['include_in_score_stats'], '步骤可用': row['include_in_step_diagnosis']}
                  for row in records], hide_index=True, width='stretch')
    if snapshot.get('unresolved_issues'):
        st.warning('当前存在待核对项目。受影响用途已独立标记，请查看具体记录及依据。')
        with st.expander('待核对记录'):
            st.json(snapshot['unresolved_issues'], expanded=False)
    by_id = {row['record_id']: row for row in records}
    selected = st.selectbox('选择需要裁决的记录', list(by_id), key='full_governance_record',
                            format_func=lambda value: f'{by_id[value]["student_id"]} · {by_id[value]["question_id"]} · {by_id[value]["status"]}')
    row = by_id[selected]
    with st.expander('原始作答与当前有效值', expanded=True):
        st.text(row['student_answer'] or '没有最终作答')
        st.text(str(row['student_steps']) or '没有解题过程')
        st.write(f'原始分 {row["official_score"]}，当前有效分 {row["effective_score"]}。')
    actions = {'FLAG_FOR_LATER': '标记稍后核对', 'REVERT_TO_OFFICIAL': '恢复官方原始值',
               'EXCLUDE_SCOPE': '暂时排除成绩统计', 'ACCEPT_AI': '接受已通过校验的候选',
               'MODIFY_VALUE': '根据评分依据调整有效分数'}
    action = st.selectbox('处理方式', list(actions), format_func=actions.get, key='full_governance_action')
    points = {}
    adjusted_score = None
    if action == 'MODIFY_VALUE':
        adjusted_score = st.number_input('调整后的总分', min_value=0, max_value=int(row['max_score']),
            value=int(row['effective_score'] or 0), key=f'full_adjusted_score_{selected}')
        for point in runtime.kb.questions[row['question_id']]['rubric_points']:
            identifier = point['rubric_id']
            points[identifier] = st.number_input(point['exact_text'], min_value=0,
                max_value=int(point['max_score']), value=0, key=f'full_adjusted_point_{selected}_{identifier}')
        st.caption('总分与评分点合计须一致，原始分值会完整保留。')
    reviewer = st.text_input('演示审核者', key='full_governance_reviewer')
    reason = st.text_area('裁决依据与理由', key='full_governance_reason')
    confirm = st.checkbox('我已查看原始证据和本次变更范围', key='full_governance_confirm')
    if st.button('保存教师裁决并生成新快照', key='full_governance_save', disabled=not confirm or not reviewer.strip() or not reason.strip()):
        decision = {'request_id': str(uuid4()), 'record_id': selected, 'source_revision': row['selected_revision'],
            'source_dataset_hash': snapshot['dataset_hash'], 'previous_snapshot_hash': runtime.kb.snapshot_hash,
            'teacher_action': action, 'decision_reason': reason.strip(), 'reviewer_id': reviewer.strip()}
        if action == 'EXCLUDE_SCOPE':
            decision['scopes'] = ['include_in_score_stats']
        if action == 'MODIFY_VALUE':
            decision.update({'confirmed': True, 'effective_score': int(adjusted_score),
                             'effective_point_scores': {identifier: int(value) for identifier, value in points.items()}})
        runtime.kb.teacher_decision(decision)
        st.session_state.pop('full_runtime_identity', None)
        st.session_state['full_notice'] = '教师裁决已追加保存，新快照已生成，相关旧产物需重新核对。'
        st.rerun()
    with st.expander('教师裁决审计与文件来源'):
        st.json(runtime.kb.teacher_events, expanded=False)
        st.json(bundle['dataset']['sources'], expanded=False)
