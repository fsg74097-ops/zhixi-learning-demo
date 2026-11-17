"""Immutable local assignment revisions and explicit teacher confirmation."""
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
import zipfile

from response_feedback import response_feedback


class AssignmentStore:
    def __init__(self, kb, bundle, directory):
        self.kb, self.bundle = kb, bundle
        self.directory = Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.path = self.directory/'assignments.sqlite'
        with self._connection() as connection:
            connection.executescript('''CREATE TABLE IF NOT EXISTS drafts
                (draft_id TEXT, revision INTEGER, body TEXT NOT NULL, PRIMARY KEY(draft_id,revision));
                CREATE TABLE IF NOT EXISTS heads(draft_id TEXT PRIMARY KEY,revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS confirmations(token TEXT PRIMARY KEY, request TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS practice(attempt_id TEXT PRIMARY KEY,student_id TEXT NOT NULL,body TEXT NOT NULL);''')

    def _connection(self):
        return sqlite3.connect(self.path,timeout=15)

    def _read(self, connection, draft_id, revision=None):
        if revision is None:
            head = connection.execute('SELECT revision FROM heads WHERE draft_id=?',(draft_id,)).fetchone()
            if head is None:
                raise ValueError('DRAFT_NOT_FOUND')
            revision = head[0]
        row = connection.execute('SELECT body FROM drafts WHERE draft_id=? AND revision=?',(draft_id,revision)).fetchone()
        if row is None:
            raise ValueError('DRAFT_NOT_FOUND')
        result = json.loads(row[0])
        if result['snapshot_hash'] != self.kb.snapshot_hash:
            result['status'] = 'STALE'
            result['requires_teacher_confirmation'] = True
        return result

    def get(self, draft_id, revision=None):
        with self._connection() as connection:
            return self._read(connection,draft_id,revision)

    def list(self, student_id=None):
        with self._connection() as connection:
            drafts = [self._read(connection,row[0],row[1]) for row in connection.execute('SELECT draft_id,revision FROM heads ORDER BY rowid DESC')]
        return [draft for draft in drafts if student_id is None or student_id in draft['target_student_refs']]

    def _validate(self, draft):
        errors, warnings = [], []
        if draft['snapshot_hash'] != self.kb.snapshot_hash:
            errors.append('STALE_SNAPSHOT')
        if not draft['items']:
            errors.append('INSUFFICIENT_ITEMS')
        known_students = set(self.bundle['dataset']['students'])
        if not draft['target_student_refs'] or not set(draft['target_student_refs']) <= known_students:
            errors.append('ACCESS_DENIED')
        ids = [item['practice_item_id'] for item in draft['items']]
        if len(ids)>30 or type(draft.get('max_minutes')) is not int or not 1 <= draft['max_minutes'] <= 180:
            errors.append('INVALID_CONSTRAINTS')
        if len(ids) != len(set(ids)):
            errors.append('DUPLICATE_ITEMS')
        for item in draft['items']:
            if item['source_status'] != 'APPROVED_PRACTICE':
                errors.append('SOURCE_NOT_APPROVED')
            if not set(item.get('knowledge_refs',[])) & set(draft['target_knowledge_refs']):
                errors.append('TARGET_KNOWLEDGE_MISMATCH')
            if item.get('teacher_edited'):
                math = response_feedback(item['exact_text'],item['answer'],item['answer'],'')
                if math['answer_validity'] != 'VALID':
                    errors.append('EDITED_CONTENT_UNVERIFIED')
                if not draft.get('teacher_content_reviewed'):
                    warnings.append('TEACHER_CONTENT_REVIEW_REQUIRED')
        if len(ids) < draft['requested_item_count']:
            warnings.append(f'INSUFFICIENT_ITEMS：请求{draft["requested_item_count"]}题，合规题源实际{len(ids)}题。')
        covered = {node for item in draft['items'] for node in item.get('knowledge_refs',[])}
        if not set(draft['target_knowledge_refs']) <= covered:
            errors.append('TARGET_COVERAGE_INCOMPLETE')
        status = 'INVALID' if errors else 'DRAFT' if 'TEACHER_CONTENT_REVIEW_REQUIRED' in warnings else 'NEEDS_REVIEW'
        return {**draft,'status':status,'validation_status':'FAILED' if errors else 'RECHECK_REQUIRED' if status=='DRAFT' else 'PASSED',
                'requires_teacher_confirmation':True,'warnings':warnings,'validation_errors':sorted(set(errors))}

    def _store(self, connection, draft):
        connection.execute('INSERT INTO drafts VALUES(?,?,?)',(draft['draft_id'],draft['revision'],json.dumps(draft,ensure_ascii=False)))
        connection.execute('INSERT INTO heads VALUES(?,?) ON CONFLICT(draft_id) DO UPDATE SET revision=excluded.revision',
                           (draft['draft_id'],draft['revision']))

    def create(self, request):
        students = request.get('student_refs',request.get('target_student_refs',[]))
        if request.get('mode') == 'whole_class':
            class_ref = request.get('class_ref')
            students = [sid for sid,value in self.bundle['dataset']['students'].items()
                        if value['模拟班级编号'] == class_ref]
        nodes = request.get('target_knowledge_refs',list(self.kb.nodes))
        constraints = request.get('constraints',{})
        count = request.get('item_count',constraints.get('item_count',3))
        if type(count) is not int or not 1 <= count <= 30:
            raise ValueError('INVALID_ARGUMENT')
        if not isinstance(students,list) or not isinstance(nodes,list) or not nodes or not set(nodes)<=set(self.kb.nodes):
            raise ValueError('INVALID_ARGUMENT')
        selected = self.kb.practices(node_ids=nodes,item_count=count)
        draft = {'schema_version':'1.1','draft_id':'DRAFT-'+uuid4().hex[:12],'revision':1,
                 'snapshot_hash':self.kb.snapshot_hash,'status':'DRAFT','target_student_refs':students,
                 'requested_item_count':count,'max_minutes':request.get('max_minutes',constraints.get('max_minutes',20)),
                 'target_knowledge_refs':nodes,'guidance':request.get('guidance',''),
                 'mode':request.get('mode','individual'),'class_ref':request.get('class_ref'),
                 'items':[],'validation_status':'NOT_RUN','requires_teacher_confirmation':True,'warnings':[],
                 'created_at':datetime.now(timezone.utc).isoformat(),'teacher_content_reviewed':False}
        for index, question in enumerate(selected):
            qid = question.get('question_id',question.get('id'))
            draft['items'].append({'draft_item_id':f'{draft["draft_id"]}-{index+1}',
                'practice_item_id':qid,'exact_text':question['exact_text'],
                'answer':question.get('reference_answer',''),'purpose':'targeted_practice',
                'knowledge_refs':question.get('knowledge_refs',nodes), 'evidence_refs':[qid],
                'source_status':question.get('source_status','ASSESSMENT_RESERVED'),'teacher_edited':False})
        draft = self._validate(draft)
        with self._connection() as connection:
            self._store(connection,draft)
        return draft

    def edit(self, draft_id, revision, changes):
        allowed = {'question_ids','target_student_refs','target_knowledge_refs','guidance','item_overrides','teacher_content_reviewed'}
        if not isinstance(changes,dict) or set(changes)-allowed:
            raise ValueError('INVALID_ARGUMENT')
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = self._read(connection,draft_id)
            if current['revision'] != revision:
                raise ValueError('STALE_REVISION')
            if current['snapshot_hash'] != self.kb.snapshot_hash:
                raise ValueError('STALE_SNAPSHOT')
            updated = {**deepcopy(current),'revision':revision+1,'status':'DRAFT','confirmation':None,'receipt':None}
            for field in ('target_student_refs','target_knowledge_refs','guidance','teacher_content_reviewed'):
                if field in changes:
                    updated[field] = changes[field]
            if 'question_ids' in changes:
                question_ids = changes['question_ids']
                catalog = self.kb.practice_items
                if not isinstance(question_ids,list) or any(qid not in catalog for qid in question_ids):
                    raise ValueError('INVALID_ARGUMENT')
                previous = {item['practice_item_id']:item for item in updated['items']}
                updated['items'] = []
                for index,qid in enumerate(question_ids):
                    question = catalog[qid]
                    item = previous.get(qid,{'practice_item_id':qid,'exact_text':question['exact_text'],
                        'answer':question.get('reference_answer',''),'knowledge_refs':question.get('knowledge_refs',[]),
                        'evidence_refs':[qid],'source_status':question.get('source_status','ASSESSMENT_RESERVED'),
                        'purpose':'targeted_practice','teacher_edited':False})
                    updated['items'].append({**item,'draft_item_id':f'{draft_id}-{revision+1}-{index+1}'})
            overrides = changes.get('item_overrides',{})
            if not isinstance(overrides,dict) or any(qid not in {item['practice_item_id'] for item in updated['items']} for qid in overrides):
                raise ValueError('INVALID_ARGUMENT')
            if overrides and 'teacher_content_reviewed' not in changes:
                updated['teacher_content_reviewed'] = False
            for item in updated['items']:
                override = overrides.get(item['practice_item_id'],{})
                if not isinstance(override,dict) or set(override)-{'exact_text','answer'}:
                    raise ValueError('INVALID_ARGUMENT')
                if override:
                    if any(not isinstance(value,str) or not value.strip() or len(value)>12000 for value in override.values()):
                        raise ValueError('INVALID_ARGUMENT')
                    item.update(override)
                    item['teacher_edited'] = True
            updated = self._validate(updated)
            self._store(connection,updated)
            return updated

    def validate(self, draft_id, revision):
        draft = self.get(draft_id,revision)
        if draft['status'] in {'SUBMITTED_FOR_REVIEW','EXPORTED'}:
            return draft
        return self._validate(draft)

    def confirm(self, draft_id, revision, teacher_id, token, *, audience='TEACHER'):
        if audience != 'TEACHER' or not isinstance(teacher_id,str) or not teacher_id.strip() or not token:
            raise ValueError('ACCESS_DENIED')
        request = json.dumps([draft_id,revision,teacher_id],ensure_ascii=False)
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            prior = connection.execute('SELECT request,body FROM confirmations WHERE token=?',(token,)).fetchone()
            if prior:
                if prior[0] != request:
                    raise ValueError('IDEMPOTENCY_CONFLICT')
                historical = json.loads(prior[1])
                if historical['snapshot_hash'] != self.kb.snapshot_hash:
                    raise ValueError('STALE_SNAPSHOT')
                return historical
            draft = self._read(connection,draft_id)
            if draft['revision'] != revision:
                raise ValueError('STALE_REVISION')
            checked = self._validate(draft)
            if checked['validation_status'] != 'PASSED':
                raise ValueError('VALIDATION_REQUIRED')
            confirmed = {**draft,'status':'SUBMITTED_FOR_REVIEW','requires_teacher_confirmation':False,
                         'confirmation':{'teacher_id':teacher_id,'token':token,'confirmed_at':datetime.now(timezone.utc).isoformat(),
                                         'production_auth':False}}
            body = json.dumps(confirmed,ensure_ascii=False)
            connection.execute('UPDATE drafts SET body=? WHERE draft_id=? AND revision=?',(body,draft_id,revision))
            connection.execute('INSERT INTO confirmations VALUES(?,?,?)',(token,request,body))
            return confirmed

    def export(self, draft_id, revision):
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            draft = self._read(connection,draft_id,revision)
            if draft['status'] not in {'SUBMITTED_FOR_REVIEW','EXPORTED'}:
                raise ValueError('CONFIRMATION_REQUIRED')
            if draft['snapshot_hash'] != self.kb.snapshot_hash:
                raise ValueError('STALE_SNAPSHOT')
            receipt = draft.get('receipt') or {'channel':'EXPORT_ONLY','artifact_ref':f'{draft_id}-r{revision}.zip',
                        'created_at':datetime.now(timezone.utc).isoformat(),'snapshot_hash':self.kb.snapshot_hash}
            exported = {**draft,'status':'EXPORTED','receipt':receipt}
            connection.execute('UPDATE drafts SET body=? WHERE draft_id=? AND revision=?',
                               (json.dumps(exported,ensure_ascii=False),draft_id,revision))
        text = ['# 针对性练习任务单','',f'对象 {"、".join(draft["target_student_refs"])}',draft.get('guidance',''),'']
        for index,item in enumerate(draft['items'],1):
            text.extend([f'## 题目 {index}',item['exact_text'],''])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
            artifacts = {'任务单.md':'\n'.join(text).encode('utf-8'),
                '教师核对答案.json':json.dumps([{'question_id':item['practice_item_id'],'answer':item['answer']} for item in draft['items']],ensure_ascii=False).encode('utf-8'),
                'assignment.json':json.dumps(exported,ensure_ascii=False,indent=2).encode('utf-8')}
            for name,content in artifacts.items():
                info = zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info,content)
        return {'draft_id':draft_id,'revision':revision,'status':'EXPORTED','filename':receipt['artifact_ref'],
                'content':buffer.getvalue(),'receipt':receipt}

    def submit_answer(self, draft_id, revision, item_id, student_id, answer, process, hint_level=0):
        if (not isinstance(answer,str) or not answer.strip() or len(answer)>4000
                or not isinstance(process,str) or len(process)>12000
                or type(hint_level) is not int or not 0<=hint_level<=3):
            raise ValueError('INVALID_ARGUMENT')
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            draft = self._read(connection,draft_id,revision)
            if draft['snapshot_hash'] != self.kb.snapshot_hash:
                raise ValueError('STALE_SNAPSHOT')
            if draft['status'] not in {'SUBMITTED_FOR_REVIEW','EXPORTED'}:
                raise ValueError('CONFIRMATION_REQUIRED')
            if student_id not in draft['target_student_refs']:
                raise ValueError('ACCESS_DENIED')
            item = next((item for item in draft['items'] if item['draft_item_id']==item_id),None)
            if item is None:
                raise ValueError('EVIDENCE_NOT_FOUND')
            record = {'attempt_id':'ATTEMPT-'+uuid4().hex,'draft_id':draft_id,'revision':revision,
                      'draft_item_id':item_id,'question_id':item['practice_item_id'],'student_id':student_id,
                      'answer':answer,'process':process,'hint_level':hint_level,
                      'snapshot_hash':draft['snapshot_hash'],'status':'待教师评阅',
                      'submitted_at':datetime.now(timezone.utc).isoformat(),
                      'feedback':response_feedback(item['exact_text'],item['answer'],answer,process)}
            connection.execute('INSERT INTO practice VALUES(?,?,?)',
                               (record['attempt_id'],student_id,json.dumps(record,ensure_ascii=False)))
        return record

    def answers(self, student_id=None):
        with self._connection() as connection:
            if student_id is None:
                rows = connection.execute('SELECT body FROM practice ORDER BY rowid DESC').fetchall()
            else:
                rows = connection.execute('SELECT body FROM practice WHERE student_id=? ORDER BY rowid DESC',(student_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]
