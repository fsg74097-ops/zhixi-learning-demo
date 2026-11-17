import hashlib
import os
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '02_项目代码'))
os.environ['PUBLIC_DEMO_MODE'] = '1'
from workflow import build_bundle
from knowledge_runtime import KnowledgeBase
from agent_runtime import DemoRuntime
import product_store
import ui_full_demo


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(product_store, 'DEFAULT_STORE', tmp_path / 'records')
    monkeypatch.setattr(ui_full_demo, 'RUNTIME_STORAGE_ROOT', tmp_path / 'runtime')
    return AppTest.from_file(str(ROOT / '02_项目代码/streamlit_app.py'), default_timeout=30).run()


def test_public_sidebar_has_no_server_directory_controls(app):
    assert not app.exception
    assert not any(item.key == 'data_directory' for item in app.text_input)
    assert not any(item.key == 'load_dataset' for item in app.button)


def test_public_session_cannot_replace_dataset_with_server_path(app, tmp_path):
    app.session_state['loaded_data_directory'] = str(tmp_path)
    app.run()
    assert not app.exception
    assert not app.error
    assert any('8名虚构学生' in item.value for item in app.info)


def test_public_bundle_rejects_arbitrary_server_directory(tmp_path):
    with pytest.raises(ValueError, match='PUBLIC_DEMO_DATA_ONLY'):
        build_bundle(tmp_path)


def test_public_knowledge_import_rejects_external_file_before_reading(tmp_path):
    source = tmp_path / 'local.txt'
    source.write_text('private server content', encoding='utf-8')
    kb = KnowledgeBase(build_bundle(ROOT / 'demo_data/math_workflow'), storage_root=tmp_path / 'runtime')
    with pytest.raises(ValueError, match='PUBLIC_DEMO_IMPORT_DISABLED'):
        kb.import_passages([{'passage_id': 'TEST-PASSAGE', 'exact_text': 'private server content',
            'source_path': str(source), 'source_hash': hashlib.sha256(source.read_bytes()).hexdigest(),
            'resource_title': 'private', 'edition': '1', 'node_ids': ['T03I1']}],
            reviewer_id='public test', review_context='DEMO_TEACHER')


def test_public_teacher_and_student_pages_remain_available(app):
    for page in ['教师工作台', '个人画像', '逐题诊断', '知识星图', '智能任务单', 'Agent分析', '数据治理']:
        app.radio(key='nav_teacher').set_value(page).run()
        assert not app.exception, page
        assert not app.error, page
        assert not any(item.key == 'full_source_path' for item in app.text_input)
    app.radio(key='view_role').set_value('学生视角').run()
    for page in ['学习首页', '我的知识探索', '逐题讲解', '我的练习任务', '自检反馈']:
        app.radio(key='nav_student').set_value(page).run()
        assert not app.exception, page
        assert not app.error, page


def test_public_runtime_retains_agent_and_revision_feedback(tmp_path):
    runtime = DemoRuntime(build_bundle(ROOT / 'demo_data/math_workflow'), storage_root=tmp_path / 'runtime')
    result = runtime.run_request('分析 DEMO01 本次表现并给出练习建议', student_id='DEMO01')
    assert result['ok']
    assert result['steps']
    draft = runtime.create_draft({'student_refs': ['DEMO01'], 'target_knowledge_refs': ['T03I1'],
                                  'item_count': 1, 'max_minutes': 15})
    edited = runtime.edit_draft(draft['draft_id'], draft['revision'], {'question_ids': ['DEMOQ1'], 'teacher_content_reviewed': True,
        'item_overrides': {'DEMOQ1': {'exact_text': '解不等式：-2x>8。', 'answer': 'x<-4'}}})
    confirmed = runtime.confirm_draft(edited['draft_id'], edited['revision'], '公开演示教师', 'public-test-confirm')
    assert confirmed['status'] == 'SUBMITTED_FOR_REVIEW'
    submitted = runtime.submit_assignment_answer(edited['draft_id'], edited['revision'],
        edited['items'][0]['draft_item_id'], 'DEMO01', 'x<-4', '-2x>8\nx<-4')
    assert submitted['feedback']['answer_validity'] == 'VALID'
    assert runtime.export_draft(edited['draft_id'], edited['revision'])['status'] == 'EXPORTED'
