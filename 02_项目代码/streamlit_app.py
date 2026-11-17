"""智析学伴：本地单用户学情证据工作台。"""
import streamlit as st

from product_store import dataset_store_paths, load_practice, load_reviews
from ui_common import apply_style
from ui_diagnosis import render_diagnosis
from ui_model import render_model
from ui_pages import render_classes, render_curriculum, render_exports
from ui_full_demo import TEACHER_PAGES, STUDENT_PAGES, render_full_demo
from workflow import DEFAULT_DATA, build_bundle
from project_paths import public_demo_mode


def data_controls():
    """Render before loading so an invalid directory remains recoverable."""
    if public_demo_mode():
        directory = str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'demo_data/math_workflow')
        st.session_state['loaded_data_directory'] = directory
        with st.sidebar:
            st.caption('公开演示 · 仅自建虚构学生与题目；服务器文件导入已关闭。')
        return directory
    with st.sidebar:
        st.text_input('本地资料目录', value=str(DEFAULT_DATA), key='data_directory',
                      help='选择完整资料包，或直接填写同一目录内六张CSV的路径。')
        if st.button('加载资料', key='load_dataset'):
            candidate = st.session_state['data_directory'].strip()
            try:
                if not candidate:
                    raise ValueError('本地资料目录不能为空，请填写目录后点击加载资料。')
                build_bundle(candidate)
                st.session_state['loaded_data_directory'] = candidate
            except (ValueError, OSError, KeyError) as error:
                st.error('资料加载失败，继续展示已加载资料。' + str(error))
        st.caption('仅在本机读取。恢复默认资料时填写初始正式资料目录并加载。')
        st.caption('当前展示的已加载目录：' + st.session_state.get('loaded_data_directory', str(DEFAULT_DATA)))
    directory = st.session_state.get('loaded_data_directory', str(DEFAULT_DATA))
    if not directory:
        raise ValueError('本地资料目录不能为空，请填写目录后点击加载资料。')
    return directory


def reset_dataset_state(identity):
    if st.session_state.get('dataset_identity') == identity:
        return
    fields = {'node_id', 'class_id', 'student_id', 'active_student', 'learning_rec',
              'practice_question', 'practice_identity', 'practice_answer', 'practice_process',
              'review_rec', 'review_identity', 'review_materials', 'review_guidance',
              'review_follow_up', 'review_reason', 'reviewer', 'model_result',
              'formal_permission', 'use_formal', 'flash_message'}
    for key in list(st.session_state):
        if key in fields or key.startswith(('hint_', 'reference_', 'full_', 'FormSubmitter:')):
            st.session_state.pop(key, None)
    st.session_state['dataset_identity'] = identity


def sidebar(bundle):
    with st.sidebar:
        st.title('智析学伴')
        st.caption('七年级数学 · 学情证据工作台')
        role = st.radio('查看视角',['教师视角','学生视角'],key='view_role')
        is_teacher = role=='教师视角'
        pages = ['学生诊断', *TEACHER_PAGES, '班级统计','课程地图','核验与导出','模型设置'] if is_teacher else ['我的学习', *STUDENT_PAGES]
        if pending := st.session_state.pop('full_jump_page', None):
            if pending in pages:
                st.session_state['nav_teacher' if is_teacher else 'nav_student'] = pending
        page = st.radio('工作区',pages,key='nav_teacher' if is_teacher else 'nav_student')
        st.divider()
        nodes = list(bundle['analysis']['nodes'])
        nid = st.selectbox('测验节点',nodes,index=nodes.index('N02') if 'N02' in nodes else 0,key='node_id')
        students = bundle['analysis']['nodes'][nid]['students']
        st.caption(f"当前资料包含{len(students)}名学生、{len(bundle['analysis']['nodes'][nid]['classes'])}个班级。")
        if is_teacher:
            cid = st.selectbox('班级',sorted({s['class_id'] for s in students.values()}),key='class_id')
            ids = [sid for sid,s in students.items() if s['class_id']==cid]
            selected = st.session_state.get('active_student','D01')
            if st.session_state.get('student_id') not in ids:
                st.session_state['student_id'] = selected if selected in ids else ids[0]
            sid = st.selectbox('学生',ids,index=ids.index(selected) if selected in ids else 0,key='student_id')
            st.session_state['active_student']=sid
        else:
            sid = st.session_state.get('active_student','D01')
            if sid not in students:
                sid = next(iter(students))
            st.session_state['active_student'] = sid
            st.write('当前学习档案：'+sid)
        st.divider()
        st.caption('本机演示身份 · DEMO_ROLE_SWITCH · NOT_PRODUCTION_AUTH。正式资料仅限授权用途。')
        st.caption('本地事实分析已启用 · 模型默认不调用')
    return is_teacher,page,sid,nid


def main():
    st.set_page_config(page_title='智析学伴 · 学情工作台',page_icon='📘',layout='wide')
    apply_style()
    try:
        directory = data_controls()
        bundle = build_bundle(directory)
        if not bundle['analysis']['nodes']:
            raise ValueError('当前资料没有可展示的测验节点，请检查测验日期与节点数据。')
        paths = dataset_store_paths(bundle)
        reset_dataset_state(str(paths['reviews']))
        reviews = load_reviews(paths['reviews'])
        practice = load_practice(paths['practice'])
        is_teacher,page,sid,nid = sidebar(bundle)
        if all('自建' in question.get('来源或资料文件', '')
               for question in bundle['dataset']['questions'].values()):
            st.info(f'自建演示数据 · {len(bundle["dataset"]["students"])}名虚构学生，'
                    f'{len(bundle["dataset"]["questions"])}道自建题。当前使用本地统计与数学核验。')
        if message := st.session_state.pop('flash_message',None):
            st.success(message)
        if page in {'学生诊断','我的学习'}:
            st.caption('当前入口展示官方原始评分口径。新工作台与画像使用MODEL_READY当前有效口径。')
            render_diagnosis(bundle,sid,nid,reviews,practice,is_teacher=is_teacher)
        elif page=='班级统计':
            st.caption('当前入口展示官方原始评分口径，未叠加数据治理中的有效值裁决。')
            render_classes(bundle,nid)
        elif page=='课程地图':
            st.info('此入口保留旧课程索引的整理摘要。逐字原文、版本与页码可到知识星图查看。')
            render_curriculum(bundle)
        elif page=='核验与导出':
            render_exports(bundle,reviews,practice)
        elif page=='模型设置':
            render_model(bundle,sid,nid)
        else:
            render_full_demo(page,bundle,sid,nid,reviews,practice,is_teacher=is_teacher)
    except (ValueError,OSError,KeyError) as error:
        st.error('当前资料或本地记录无法完成核对，请检查后重新填写目录并加载。')
        st.error(str(error))
        st.stop()


if __name__=='__main__':
    main()
