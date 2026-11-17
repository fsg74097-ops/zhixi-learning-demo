"""Shared visual vocabulary and evidence presentation for the local workbench."""
import streamlit as st


STYLE = """
<style>
:root { --ink:#e7eef7; --muted:#9cacc1; --paper:#0c1422; --line:#27364c; }
.stApp { background:var(--paper); color:var(--ink); }
[data-testid="stHeader"] { background:rgba(12,20,34,.94); }
[data-testid="stSidebar"] { background:#101c2d; border-right:1px solid var(--line); }
[data-testid="stSidebar"] h1 { font-size:1.65rem; letter-spacing:.05em; }
.block-container { max-width:1260px; padding-top:2.4rem; padding-bottom:3rem; }
h1,h2,h3 { color:var(--ink); font-family:Inter,"Microsoft YaHei",sans-serif; }
h1 { font-size:2rem !important; font-weight:700; letter-spacing:.02em; }
h2 { font-size:1.38rem !important; } h3 { font-size:1.06rem !important; }
[data-testid="stMetric"] { padding:1rem 1.1rem; border-top:2px solid #42cfca;
 background:#142236; border-bottom:1px solid var(--line); min-height:110px; border-radius:8px; }
[data-testid="stMetricLabel"] { color:var(--muted); }
[data-testid="stMetricValue"] { font-size:1.9rem; color:var(--ink); }
[data-testid="stCaptionContainer"] { color:var(--muted); }
[data-testid="stExpander"] { background:#111e30; border-color:var(--line); }
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"] {
 background:#2dc9c4; border-color:#2dc9c4; color:#06181e; }
[data-testid="stTabs"] button { font-weight:600; }
.workbench-kicker { color:#64d9d1; font-size:.78rem; font-weight:700;
 letter-spacing:.16em; margin:0 0 .35rem; }
.workbench-rule { height:2px; background:#7584e8; width:60px; margin:12px 0 20px; }
@media(max-width:700px) { .block-container {padding:1.6rem 1rem 2rem;}
 h1 {font-size:1.6rem !important;} [data-testid="stMetric"] {min-height:90px;} }
</style>
"""


def apply_style():
    st.markdown(STYLE, unsafe_allow_html=True)


def page_heading(title, subtitle):
    st.markdown('<p class="workbench-kicker">ZHIXI · LEARNING EVIDENCE</p>', unsafe_allow_html=True)
    st.title(title)
    st.caption(subtitle)
    st.markdown('<div class="workbench-rule"></div>', unsafe_allow_html=True)


def rate(value):
    return '—' if value is None else f'{value:.1%}'


def score_metrics(student):
    overall = student['overall']
    cells = list(student['indicators'].values())
    covered = sum(item['scored_events'] >= 2 for item in cells)
    columns = st.columns(4)
    columns[0].metric('本节点得分率', rate(overall['score_rate']))
    columns[1].metric('有效得分 / 满分', f"{overall['score']:g} / {overall['max_score']:g}")
    columns[2].metric('有效已评分事件', str(overall['scored_events']))
    columns[3].metric('至少两条证据的指标', f'{covered} / {len(cells)}')
    st.caption('得分率＝有效得分和 ÷ 对应满分和。不是掌握概率；两条证据与80%线均为团队暂定观察规则。')


def indicator_table(student):
    rows = [{'指标':iid, '观察内容':cell['label'], '得分率':rate(cell['score_rate']),
             '有效事件':cell['scored_events'], '判断边界':cell['status']}
            for iid,cell in student['indicators'].items()]
    st.dataframe(rows, hide_index=True, width='stretch')


def citation(ref, title):
    st.markdown(f'**{title}**')
    if ref.get('status') != 'source_verified':
        st.warning('来源变化或缺失，原定位待重新核查。')
        return
    st.write(ref['excerpt'])
    st.caption(f"{ref['source_file']} · PDF第{ref['physical_page']}页 / 正文{ref['printed_page']}页")


def citations(rec):
    citation(rec['standard_citation'], '课程标准依据')
    for ref in rec.get('textbook_citations', []):
        citation(ref, '教材定位')
    st.caption('引用状态由源文件摘要与已核定位决定，教学适切性仍待教师复核。')
