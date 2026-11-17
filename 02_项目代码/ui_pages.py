"""Class comparisons, curriculum sources and reproducible local exports."""
import json

import streamlit as st

from product_exports import export_product
from ui_common import citations, page_heading, rate


def render_classes(bundle, nid):
    page_heading('班级统计','同一测验节点下比较有效记录，同时保留参与覆盖和证据不足人数。')
    node = bundle['analysis']['nodes'][nid]
    st.info('各班作答题目、有效事件与证据覆盖可能不同。汇总得分率不能直接解释为班级能力排名。不同节点教学范围可能不同，不据分数差推断进步。')
    columns = st.columns(len(node['classes']))
    for column,(cid,group) in zip(columns,node['classes'].items()):
        with column:
            st.subheader(cid)
            st.metric('有效记录汇总得分率',rate(group['overall']['score_rate']))
            st.write(f"有有效记录 {group['observed_student_count']} / {len(group['student_ids'])} 人")
            st.caption(f"{group['overall']['scored_events']} 个已评分事件 · {len(group['overall']['question_ids'])}道题 · "
                       f"{group['overall']['score']:g}/{group['overall']['max_score']:g}分")
    st.subheader('同指标，连同覆盖一起读')
    rows = []
    for cid,group in node['classes'].items():
        for need in group['common_needs']:
            cell = group['indicators'][need['indicator_id']]
            rows.append({'班级':cid,'指标':need['indicator_id'],'观察内容':need['label'],
                         '得分率':rate(cell['score_rate']),'有效事件':cell['scored_events'],
                         '巩固线索人数':len(need['consolidation_student_ids']),
                         '证据不足人数':len(need['insufficient_student_ids']),'班级人数':need['class_student_count']})
    st.dataframe(rows,hide_index=True,width='stretch')
    st.caption('巩固线索与证据不足是不同状态，不能相加为“未掌握人数”。应先核对相同题目与分点评分，再安排教学。')


def render_curriculum(bundle):
    curriculum = bundle['curriculum']
    page_heading('课程地图',f"{len(curriculum['topics'])}个主题、{len(curriculum['indicators'])}个指标，将课标要求、教材页码和具体练习连起来。")
    for topic in curriculum['topics']:
        st.subheader(f"{topic['id']} · {topic['name']}")
        st.write(topic['goal'])
        for indicator in curriculum['indicators']:
            if indicator['topic_id'] != topic['id']:
                continue
            with st.expander(f"{indicator['id']} · {indicator['description']}"):
                st.caption('对应题目：'+'、'.join(indicator['question_ids']))
                citations(indicator)
    with st.expander('指标之间的教学联系（团队建议）'):
        st.dataframe([{'前置指标':r['from'],'后续指标':r['to'],'联系说明':r['reason']}
                      for r in curriculum['relations']],hide_index=True,width='stretch')
        st.caption('这些联系为团队教学推断，不冒充课程标准原文。')


def render_exports(bundle, reviews, practice):
    page_heading('核验与导出','正式原始数据只读；导出当前分析、原始证据、教师审计和独立练习记录。')
    summary = bundle['dataset']['summary']
    columns = st.columns(4)
    for column,label,key in zip(columns,['原始记录','作答事件','有效已评分','异常隔离'],
                                ['raw_records','unique_events','valid_records','quarantined_selected']):
        column.metric(label,str(summary[key]))
    st.caption(f"另有未作答 {summary['selected_statuses'].get('未作答',0)} 条、待评分 {summary['selected_statuses'].get('待评分',0)} 条；"
               f"{summary['superseded_records']} 条旧修订保留但不重复计分。")
    _downloads(bundle,reviews,practice)
    with st.expander('处理问题与隔离依据',expanded=True):
        st.json(bundle['dataset']['issues'],expanded=False)
    with st.expander('原始文件摘要与来源'):
        st.dataframe([{'文件':name,'行数':source['rows'],'SHA256':source['sha256']}
                      for name,source in bundle['dataset']['sources'].items()],hide_index=True,width='stretch')
    with st.expander('本次本地处理轨迹'):
        st.dataframe(bundle['trace'],hide_index=True,width='stretch')
    st.info('导出为内部核对材料，包含正式资料，仅限已授权范围使用。教师语义核验、真实教学效果、正式提交状态须分别确认。')


def _downloads(bundle, reviews, practice):
    columns = st.columns(2)
    columns[0].download_button('下载完整核验包 ZIP',export_product(bundle,reviews,practice),
                               file_name='智析学伴_本地核验包.zip',mime='application/zip',type='primary')
    data = json.dumps(bundle['analysis'],ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')
    columns[1].download_button('下载原始分析 JSON',data,file_name='智析学伴_原始分析.json',mime='application/json')
    st.caption(f'完整包含CSV/JSON、字段说明、证据、摘要和 {len(reviews)} 次教师调整、{len(practice)} 次新增练习。模型凭证及内部推理不进入导出。')
