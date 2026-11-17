"""Explicit, session-only optional model explanation controls."""
import os
from pathlib import Path

import streamlit as st

from deepseek_client import ExplanationError, cache_key, explain
from model_eval import synthetic_cases
from ui_common import page_heading
from workflow import model_context


def render_model(bundle, sid, nid):
    page_heading('模型设置','本地统计已可独立使用。DeepSeek只生成待核验的解释草稿，按按钮才发起请求。')
    st.info('默认只联调自建合成样例。正式资料的外部服务使用范围尚待明确；“可能允许”不视为已获得明确许可。')
    model = st.selectbox('解释模型',['deepseek-flash','deepseek-v4-pro'],key='model_name')
    thinking = st.toggle('启用推理模式（耗时与费用可能增加）',key='model_thinking',value=False)
    api_key = st.text_input('DeepSeek API Key（仅当前会话）',type='password',key='model_api_key',
                            help='也可使用当前进程已设置的DEEPSEEK_API_KEY。不会写入文件或导出。')
    if os.environ.get('DEEPSEEK_API_KEY', '').strip():
        st.success('后端模型凭证已配置，可以直接生成解释草稿。')
        st.caption('密钥由当前后端进程提供，密码框可留空。')
    else:
        st.caption('可在当前浏览器会话输入凭证，或启动配置了后端环境变量的服务。')
    cases = synthetic_cases()
    demo_directory = Path(__file__).resolve().parents[1] / 'demo_data/math_workflow'
    if Path(bundle['dataset']['data_dir']).resolve() == demo_directory.resolve():
        demo_context = {**model_context(bundle, sid, nid),
                        'scope': '完全自建演示数据；只分析当前虚构学生的本次作答，解释草稿须核对。'}
        cases.append({'case_id': 'current_demo', 'name': f'当前自建演示学生 {sid}', 'context': demo_context})
    names = {case['case_id']:case['name'] for case in cases}
    case_id = st.selectbox('自建合成样例',list(names),format_func=names.get,key='model_case')
    permission = st.checkbox('我已取得将当前正式数据范围发送至DeepSeek的明确许可',key='formal_permission',value=False)
    use_formal = st.checkbox(f'本次使用正式案例 {sid} / {nid}',key='use_formal',value=False,disabled=not permission)
    formal = permission and use_formal
    context = model_context(bundle,sid,nid) if formal else next(c['context'] for c in cases if c['case_id']==case_id)
    if formal:
        context = {**context,'scope':'正式赛事资料，用户已在本机会话确认外部服务使用范围；当前节点；解释草稿须人工核验'}
    scope_label = '当前选中正式案例' if formal else names[case_id]
    st.caption('本次范围：' + scope_label)
    with st.expander('发送内容预览'):
        st.json(context,expanded=False)
    identity = cache_key(context,model=model,thinking=thinking)
    if st.button('生成解释草稿',type='primary',key='run_explanation'):
        _generate(context,api_key,model,thinking,identity)
    saved = st.session_state.get('model_result')
    if saved and saved['identity']==identity:
        _show_result(saved['result'])
    elif saved:
        st.caption('上下文或模型配置已变化，先前解释不适用于当前选择。')


def _generate(context, api_key, model, thinking, identity):
    st.session_state.pop('model_result',None)
    if not (api_key.strip() or os.environ.get('DEEPSEEK_API_KEY','').strip()):
        st.error('请先输入当前会话API Key，或使用已有进程环境变量。')
        return
    try:
        with st.spinner('正在请求解释草稿，统计结果保持不变…'):
            result = explain(context,api_key=api_key.strip() or None,model=model,thinking=thinking)
        st.session_state['model_result']={'identity':identity,'result':result}
    except ExplanationError as error:
        st.error(str(error))
        st.caption('请求状态：'+error.metadata.get('reason_code','unknown'))


def _show_result(result):
    st.warning('已收到模型解释草稿。通过结构与引用范围检查，不代表事实语义或教学适切性已核验。')
    explanation = result['explanation']
    if explanation['observations']:
        st.subheader('本次观察')
        for observation in explanation['observations']:
            st.write(observation['text'])
            st.caption('作答依据：' + '、'.join(observation['evidence_ids']))
    if explanation['suggestions']:
        st.subheader('下一步建议')
        for suggestion in explanation['suggestions']:
            st.write(suggestion['text'])
            st.caption('对应题目：' + '、'.join(suggestion['material_ids']) +
                       ' · 作答依据：' + ('、'.join(suggestion['evidence_ids']) or '作答关联待核对'))
    for uncertainty in explanation['uncertainties']:
        st.info(uncertainty)
    with st.expander('查看结构化解释和引用'):
        st.json(explanation,expanded=False)
    with st.expander('本次请求元数据（不含凭证或内部推理）'):
        st.json(result.get('metadata',{}),expanded=False)
