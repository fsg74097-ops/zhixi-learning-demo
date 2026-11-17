"""Versioned local knowledge index and append-only MODEL_READY governance.

Course text and student responses occupy separate SQLite tables. Values exposed
to callers are copies of a content-addressed snapshot. Model agreement alone
cannot activate scores, which also need independently reproducible rubric facts.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from governance_core import SCORE_ERRORS, _events
from response_feedback import response_feedback
from step_math import clean_expression
from project_paths import public_demo_mode


ROOT = Path(__file__).resolve().parents[1]
RULES_VERSION = 'GData-1.2-Adjudication-0.3-runtime-1'
PURPOSES = ('include_in_score_stats', 'include_in_step_diagnosis',
            'include_in_participation_stats', 'include_in_quality_audit', 'include_in_revision_audit')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _ids(value):
    return [part for part in re.split(r'[、,，;；\s]+', value or '') if part]


class KnowledgeBase:
    def __init__(self, bundle, storage_root=None):
        self.bundle = deepcopy(bundle)
        self.dataset = self.bundle['dataset']
        self.dataset_hash = _hash({name: item['sha256'] for name, item in self.dataset['sources'].items()})
        folder = Path(self.dataset['data_dir']).resolve()
        resource_root = folder.parent.parent if folder.parent.name == '4_赛题数据' else folder
        self._verified_resources = [(resource_root / item['relative_path'], item['sha256'])
                                    for item in self.bundle.get('curriculum', {}).get('sources', {}).values()
                                    if item.get('verified') and item.get('relative_path') and item.get('sha256')]
        self.is_self_created = folder in {(ROOT / 'demo_data/math_workflow').resolve(),
                                          (ROOT / 'tests/fixtures/six_csv').resolve()}
        self.storage_root = Path(storage_root) if storage_root is not None else ROOT / 'outputs/knowledge_runtime'
        location_id = _hash(str(folder).casefold())[:20]
        self.database_path = self.storage_root / location_id / 'knowledge.sqlite3'
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._entities, self._nodes, self._questions, self.relations = {}, {}, {}, []
        self._imported_passages = []
        self._stale_passage_count = 0
        self._registered_practices = {}
        self._assert_sources()
        self._build_knowledge()
        self._base_records, self._history, self._revision_issues = self._record_views()
        self.field_mapping = self._field_mapping()
        self._initialize_database()
        self.refresh()

    @property
    def nodes(self):
        self._assert_sources()
        return deepcopy(self._nodes)

    @property
    def questions(self):
        self._assert_sources()
        return deepcopy(self._questions)

    @property
    def evidence(self):
        self.refresh()
        return deepcopy(self._entities)

    @property
    def snapshot(self):
        self.refresh()
        return deepcopy(self._snapshot)

    @property
    def snapshot_hash(self):
        self.refresh()
        return self._snapshot['snapshot_hash']

    @property
    def teacher_events(self):
        with self._connect() as connection:
            return self._load_events(connection, 'teacher_decisions')

    @property
    def candidates(self):
        with self._connect() as connection:
            return self._load_events(connection, 'score_candidates')

    @property
    def practice_items(self):
        self.refresh()
        items = {identity: {**deepcopy(item), 'practice_id': identity} for identity, item in self._questions.items()
                 if item['source_status'] == 'APPROVED_PRACTICE'}
        items.update({identity: deepcopy(item) for identity, item in self._registered_practices.items()
                      if item['source_status'] == 'APPROVED_PRACTICE'})
        return items

    @property
    def practice_review_events(self):
        with self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                'SELECT payload FROM practice_reviews WHERE dataset_hash=? ORDER BY rowid', (self.dataset_hash,))]

    def _assert_sources(self):
        verified = {}
        for item in self.dataset['sources'].values():
            path = Path(item['path'])
            if not self._file_matches(path, item['sha256'], verified):
                raise ValueError('STALE_SOURCE：源文件已变化，请重新导入并生成快照')
        for path, expected in self._verified_resources:
            if not self._file_matches(path, expected, verified):
                raise ValueError('STALE_SOURCE：已核验教材资源已变化，请重新建立来源索引')
        for item in self._imported_passages:
            source = item['source_ref']
            if not self._passage_sources_valid(source, verified):
                raise ValueError('STALE_SOURCE：知识来源已变化，请重新导入并核验原文')

    @staticmethod
    def _file_matches(path, expected, verified):
        path = Path(path)
        identity = (path, expected)
        if identity not in verified:
            verified[identity] = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected
        return verified[identity]

    @classmethod
    def _passage_sources_valid(cls, source, verified=None):
        verified = {} if verified is None else verified
        files = [(source['source_path'], source['source_hash'])]
        if source.get('ocr'):
            files += [(source['ocr']['artifact_path'], source['ocr']['artifact_hash']),
                      (source['ocr']['image_path'], source['ocr']['image_hash'])]
        return all(cls._file_matches(path, digest, verified)
                   for path, digest in files)

    def _verified_ocr_text(self, value, path, physical):
        """Bind an auditable transcription to the actual rendered PDF page.

        Provenance verification does not constitute independent human OCR review.
        Small explicit visual corrections are preserved alongside raw OCR text.
        """
        evidence = value['ocr_evidence']
        artifact_path = Path(evidence.get('artifact_path', '')).resolve()
        if (not artifact_path.is_file() or
                hashlib.sha256(artifact_path.read_bytes()).hexdigest() != evidence.get('artifact_hash')):
            raise ValueError('OCR_ARTIFACT_HASH_MISMATCH')
        artifact = json.loads(artifact_path.read_text(encoding='utf-8-sig'))
        image_path = Path(artifact['image_path']).resolve()
        if (artifact.get('physical_page') != physical or not image_path.is_file() or
                hashlib.sha256(image_path.read_bytes()).hexdigest() != artifact.get('image_sha256')):
            raise ValueError('OCR_IMAGE_BINDING_MISMATCH')
        scale = evidence.get('render_scale_to')
        if type(scale) is not int or not 100 <= scale <= 5000:
            raise ValueError('INVALID_RENDER_SCALE')
        with tempfile.TemporaryDirectory(prefix='ocr_', dir=self.storage_root) as temporary:
            rendered = Path(temporary) / 'page'
            try:
                subprocess.run(['pdftoppm', '-f', str(physical), '-l', str(physical), '-scale-to', str(scale),
                                '-singlefile', '-png', str(path), str(rendered)],
                               check=True, capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError) as error:
                raise ValueError('PDF_RENDER_VERIFICATION_FAILED') from error
            if hashlib.sha256(rendered.with_suffix('.png').read_bytes()).hexdigest() != artifact['image_sha256']:
                raise ValueError('PDF_RENDER_HASH_MISMATCH')
        raw = artifact.get('text')
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('OCR_TEXT_REQUIRED')
        transcript = re.sub(r'\s+', '', raw)
        corrections = evidence.get('corrections', [])
        if not isinstance(corrections, list) or len(corrections) > 100:
            raise ValueError('INVALID_OCR_CORRECTIONS')
        prior_start = len(transcript)
        for correction in sorted(corrections, key=lambda item: item.get('start', -1), reverse=True):
            start, before, after = correction.get('start'), correction.get('before'), correction.get('after')
            if (type(start) is not int or not isinstance(before, str) or not isinstance(after, str) or
                    not 1 <= len(before) <= 4 or not 1 <= len(after) <= 4 or start < 0 or
                    start + len(before) > prior_start or transcript[start:start + len(before)] != before or
                    not correction.get('reason')):
                raise ValueError('INVALID_OCR_CORRECTIONS')
            transcript = transcript[:start] + after + transcript[start + len(before):]
            prior_start = start
        review = evidence.get('visual_review', {})
        if review.get('actor_type') not in {'AI', 'DEMO_TEACHER'} or not review.get('reviewer_id') or not review.get('note'):
            raise ValueError('OCR_VISUAL_REVIEW_REQUIRED')
        provenance = {'artifact_path': str(artifact_path), 'artifact_hash': evidence['artifact_hash'],
                      'image_path': str(image_path), 'image_hash': artifact['image_sha256'],
                      'method': artifact.get('method'), 'language': artifact.get('language'),
                      'render_method': 'pdftoppm', 'render_scale_to': scale, 'raw_text': raw,
                      'whitespace_normalization': 'remove_all_unicode_whitespace', 'corrections': corrections,
                      'transcript_hash': hashlib.sha256(transcript.encode('utf-8')).hexdigest(),
                      'visual_review': review,
                      'semantic_accuracy': 'AI_REVIEWED' if review['actor_type'] == 'AI' else 'DEMO_TEACHER_REVIEWED'}
        return transcript, provenance

    def _source_ref(self, table, row, column, span=None):
        name = table + '.csv'
        source = self.dataset['sources'][name]
        ref = {'source_file_id': name, 'source_hash': source['sha256'], 'source_version': source['sha256'],
               'row': row, 'column': column}
        if span is not None:
            ref['source_span'] = span
        return ref

    def _entity(self, identity, kind, text, source, **values):
        item = {'id': identity, 'type': kind, 'exact_text': text, 'source_ref': source,
                'review_status': 'PROJECT_DEFINED' if self.is_self_created else 'SOURCE_PROVIDED',
                'content_hash': hashlib.sha256(text.encode('utf-8')).hexdigest(), **values}
        self._entities[identity] = item
        return item

    def _build_knowledge(self):
        for number, raw in enumerate(self.dataset['tables']['requirements'], 2):
            identity = raw['指标编号']
            node = self._entity(identity, 'KNOWLEDGE_NODE', raw['观察指标'],
                                self._source_ref('课程要求', number, '观察指标'),
                                node_id=identity, name=raw['观察指标'], definition=raw['观察指标'],
                                objective=raw['主题目标'], topic_id=raw['主题编号'],
                                knowledge_refs=[identity], question_ids=[])
            self._nodes[identity] = node
            self._entity('REQ:' + identity, 'CURRICULUM_REQUIREMENT', raw['主题目标'],
                         self._source_ref('课程要求', number, '主题目标'), knowledge_refs=[identity],
                         display_name='课程目标 · ' + raw['观察指标'])
        for number, raw in enumerate(self.dataset['tables']['materials'], 2):
            if raw['材料类型'] != '题目':
                continue
            identity = raw['材料编号']
            knowledge_refs = [key for key in _ids(raw['指标编号']) if key in self._nodes]
            source_status = 'APPROVED_PRACTICE' if self.is_self_created else 'ASSESSMENT_RESERVED'
            question = {'id': identity, 'question_id': identity, 'exact_text': raw['题干或内容'],
                        'reference_answer': raw['参考答案'], 'reference_explanation': raw['参考解析'],
                        'max_score': int(raw['满分']), 'knowledge_refs': knowledge_refs,
                        'rubric_points': [], 'source_status': source_status,
                        'authorized_use': self.is_self_created,
                        'source_ref': self._source_ref('题目与材料', number, '题干或内容'),
                        'review_status': 'PROJECT_DEFINED' if self.is_self_created else 'SOURCE_PROVIDED'}
            self._entity(identity, 'QUESTION', raw['题干或内容'], question['source_ref'],
                         question_id=identity, knowledge_refs=knowledge_refs, source_status=source_status,
                         display_name=' / '.join(self._nodes[key]['name'] for key in knowledge_refs) + ' · ' + raw['题型'])
            self._entity('REFERENCE:' + identity, 'REFERENCE_ANSWER', raw['参考答案'],
                         self._source_ref('题目与材料', number, '参考答案'), question_id=identity,
                         knowledge_refs=knowledge_refs)
            for line in raw['评分点'].splitlines():
                match = re.fullmatch(r'(\S+)\s+(.+)\s+(\d+)分', line.strip())
                if match is None:
                    continue
                rubric_id, description, maximum = match.groups()
                start = raw['评分点'].find(description)
                source = self._source_ref('题目与材料', number, '评分点', [start, start + len(description)])
                point = self._entity(rubric_id, 'RUBRIC_POINT', description, source,
                                     rubric_id=rubric_id, question_id=identity, max_score=int(maximum),
                                     is_official=not self.is_self_created, knowledge_refs=knowledge_refs,
                                     display_name=f'评分依据 · {identity} · {rubric_id}')
                question['rubric_points'].append(point)
                for node_id in knowledge_refs:
                    self.relations.append({'relation_type': 'DIRECT_ASSESSED', 'from_id': rubric_id,
                                           'to_id': node_id, 'question_id': identity,
                                           'review_status': point['review_status'], 'source_ref': source})
            self._questions[identity] = question
            for node_id in knowledge_refs:
                self._nodes[node_id]['question_ids'].append(identity)
                self.relations.append({'relation_type': 'DIRECT_ASSESSED', 'from_id': identity,
                                       'to_id': node_id, 'question_id': identity,
                                       'review_status': question['review_status'], 'source_ref': question['source_ref']})
        # The legacy resource index contains formatted summaries and page
        # locators. Exact SOURCE_PASSAGE objects require an actual source import.
        self.resource_locators = []
        for indicator in self.bundle.get('curriculum', {}).get('indicators', []):
            passages = [indicator.get('standard_citation', {}), *indicator.get('textbook_citations', [])]
            for passage in passages:
                if passage.get('status') != 'source_verified' or not passage.get('excerpt'):
                    continue
                source = {'source_file_id': passage['source_file'], 'source_hash': passage['source_sha256'],
                          'physical_page': passage['physical_page'], 'printed_page': passage['printed_page']}
                self.resource_locators.append({'knowledge_refs': [indicator['id']], 'source_ref': source,
                                               'status': 'LOCATOR_ONLY',
                                               'excerpt_type': passage.get('excerpt_type', '来源整理信息')})

    def _record_views(self):
        rows = self.dataset['raw_records']
        metadata = self.dataset['record_meta']
        eligible = [row for row in rows if metadata[row['记录编号']]['eligible']]
        events, selected_ids, revision_issues = _events(eligible, metadata)
        event_by_id = {event['event_id']: event for event in events}
        current, history = [], []
        for raw in rows:
            identity = raw['记录编号']
            meta = metadata[identity]
            question = self._questions.get(raw['材料编号'], {})
            issues = [code for code in meta['issue_codes'] if code != 'ambiguous_revision']
            issues += [item['code'] for item in revision_issues if item.get('record_id') == identity
                       and item['code'] != 'duplicate_historical_revision']
            issues = sorted(set(issues))
            selected = identity in selected_ids
            score_valid = raw['评分状态'] == '已评分' and not set(issues).difference({'topic_mismatch'})
            score = meta['score'] if score_valid else None
            source = self._source_ref('作答记录', meta['source_row'], '学生最终作答')
            self._entity(identity, 'STUDENT_RESPONSE', raw['学生最终作答'] + '\n' + raw['解答过程'], source,
                         student_id=raw['学生编号'], question_id=raw['材料编号'],
                         knowledge_refs=question.get('knowledge_refs', []), review_status='SOURCE_PROVIDED')
            steps = []
            offset = 0
            for number, line in enumerate(raw['解答过程'].splitlines(keepends=True), 1):
                text = line.rstrip('\r\n')
                if text.strip():
                    step_id = f'{identity}:S{number}'
                    step_source = self._source_ref('作答记录', meta['source_row'], '解答过程', [offset, offset + len(text)])
                    step = self._entity(step_id, 'STUDENT_STEP', text, step_source,
                                        student_id=raw['学生编号'], question_id=raw['材料编号'],
                                        step_id=step_id, step_text=text, source_span=[offset, offset + len(text)],
                                        review_status='OBSERVATION_ONLY')
                    steps.append(step)
                offset += len(line)
            row = {'record_id': identity, 'event_id': raw['作答事件编号'], 'student_id': raw['学生编号'],
                   'class_id': self.dataset['students'].get(raw['学生编号'], {}).get('模拟班级编号'),
                   'node_id': raw['节点编号'], 'question_id': raw['材料编号'], 'selected_revision': meta['revision'],
                   'revision': meta['revision'], 'status': raw['评分状态'], 'selected': selected,
                   'original_question': question.get('exact_text', ''), 'student_answer': raw['学生最终作答'],
                   'student_steps': raw['解答过程'], 'step_refs': [item['id'] for item in steps],
                   'official_score': meta['score'], 'official_point_scores': deepcopy(meta['point_scores']),
                   'official_raw_score': raw['得分'], 'max_score': meta['max_score'],
                   'candidate_score': None, 'candidate_point_scores': None, 'candidate_status': 'NOT_RUN',
                   'effective_score': score, 'effective_point_scores': deepcopy(meta['point_scores']) if score_valid else None,
                   'effective_score_source': 'OFFICIAL',
                   'effective_value_status': 'OFFICIAL_UNCHANGED' if score_valid else 'UNRESOLVED',
                   'include_in_score_stats': selected and score_valid,
                   'include_in_step_diagnosis': selected and bool(raw['学生最终作答'] or raw['解答过程']),
                   'include_in_participation_stats': selected,
                   'include_in_quality_audit': True, 'include_in_revision_audit': True,
                   'issue_codes': issues, 'exclude_reason': issues[:], 'source_ref': source,
                   'knowledge_refs': question.get('knowledge_refs', []),
                   'rubric_refs': [point['id'] for point in question.get('rubric_points', [])],
                   'decision_audit_ids': [], 'candidate_id': None,
                   'validation': {'schema_validation': not bool(issues), 'semantic_status': 'NOT_RUN'}}
            history.append(row)
            if selected:
                current.append(deepcopy(row))
            elif raw['作答事件编号'] in event_by_id and event_by_id[raw['作答事件编号']]['resolution'] == 'CONFLICT':
                row['revision_resolution'] = 'CONFLICT'
        return current, history, revision_issues

    def _field_mapping(self):
        fields = {'记录编号': 'record_id', '作答事件编号': 'event_id', '学生编号': 'student_id',
                  '节点编号': 'node_id', '材料编号': 'question_id', '修订版本': 'selected_revision',
                  '得分': 'official_score', '评分点得分': 'official_point_scores',
                  '学生最终作答': 'student_answer', '解答过程': 'student_steps', '评分状态': 'status'}
        return [{'source_name': '作答记录.csv', 'source_column': source, 'canonical_field': target,
                 'nullable': source in {'得分', '评分点得分', '学生最终作答', '解答过程'},
                 'source_authority': 'PROJECT_DEFINED' if self.is_self_created else 'SOURCE_PROVIDED',
                 'mapping_rule': '保留来源并使用既有official_data类型校验', 'review_status': 'STRUCTURE_CHECKED'}
                for source, target in fields.items()]

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.execute('PRAGMA foreign_keys=ON')
        return connection

    def _initialize_database(self):
        with self._connect() as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS knowledge_entities (
                    dataset_hash TEXT NOT NULL, entity_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(dataset_hash, entity_id));
                CREATE TABLE IF NOT EXISTS knowledge_relations (
                    dataset_hash TEXT NOT NULL, relation_hash TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(dataset_hash, relation_hash));
                CREATE TABLE IF NOT EXISTS student_records (
                    dataset_hash TEXT NOT NULL, record_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(dataset_hash, record_id));
                CREATE TABLE IF NOT EXISTS score_candidates (
                    event_id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, dataset_hash TEXT NOT NULL,
                    request_hash TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS teacher_decisions (
                    event_id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, dataset_hash TEXT NOT NULL,
                    request_hash TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_ready_snapshots (
                    snapshot_hash TEXT PRIMARY KEY, dataset_hash TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS imported_passages (
                    dataset_hash TEXT NOT NULL, passage_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(dataset_hash, passage_id));
                CREATE TABLE IF NOT EXISTS independent_practices (
                    dataset_hash TEXT NOT NULL, practice_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(dataset_hash, practice_id));
                CREATE TABLE IF NOT EXISTS practice_reviews (
                    review_id TEXT PRIMARY KEY, dataset_hash TEXT NOT NULL, practice_id TEXT NOT NULL,
                    payload TEXT NOT NULL);
            ''')
            for identity, item in self._entities.items():
                if item['type'] not in {'STUDENT_RESPONSE', 'STUDENT_STEP'}:
                    connection.execute('INSERT OR IGNORE INTO knowledge_entities VALUES(?,?,?)',
                                       (self.dataset_hash, identity, _json(item)))
            for item in self.relations:
                connection.execute('INSERT OR IGNORE INTO knowledge_relations VALUES(?,?,?)',
                                   (self.dataset_hash, _hash(item), _json(item)))
            for row in self._history:
                connection.execute('INSERT OR IGNORE INTO student_records VALUES(?,?,?)',
                                   (self.dataset_hash, row['record_id'], _json(row)))

    def _load_events(self, connection, table):
        return [json.loads(row[0]) for row in connection.execute(
            f'SELECT payload FROM {table} WHERE dataset_hash=? ORDER BY rowid', (self.dataset_hash,))]

    def _build_snapshot(self, connection, candidates=None, decisions=None):
        candidates = self._load_events(connection, 'score_candidates') if candidates is None else candidates
        decisions = self._load_events(connection, 'teacher_decisions') if decisions is None else decisions
        records = deepcopy(self._base_records)
        by_id = {row['record_id']: row for row in records}
        validated_by_id = {item['candidate_id']: item for item in candidates if item['candidate_status'] == 'VALIDATED'}
        for candidate in candidates:
            row = by_id[candidate['record_id']]
            row.update({key: deepcopy(candidate[key]) for key in ('candidate_id', 'candidate_score', 'candidate_point_scores', 'candidate_status', 'validation')})
            if candidate['candidate_status'] == 'VALIDATED':
                row.update(effective_score=candidate['candidate_score'], effective_point_scores=deepcopy(candidate['candidate_point_scores']),
                           effective_score_source='AI_DEFAULT', effective_value_status='AI_DEFAULT_ACTIVE',
                           effective_candidate_id=candidate['candidate_id'])
                row['include_in_score_stats'] = not bool(set(row['issue_codes']) - SCORE_ERRORS - {'topic_mismatch'})
        for event in decisions:
            row = by_id[event['record_id']]
            action = event['teacher_action']
            row['decision_audit_ids'].append(event['decision_audit_id'])
            if action == 'MODIFY_VALUE':
                row.update(effective_score=event['effective_score'], effective_point_scores=deepcopy(event['effective_point_scores']),
                           effective_score_source='TEACHER_OVERRIDE', effective_value_status='TEACHER_MODIFIED')
                row['include_in_score_stats'] = row['status'] == '已评分' and not bool(set(row['issue_codes']) - SCORE_ERRORS - {'topic_mismatch'})
            elif action == 'REVERT_TO_OFFICIAL':
                row.update(effective_score=row['official_score'], effective_point_scores=deepcopy(row['official_point_scores']),
                           effective_score_source='OFFICIAL', effective_value_status='TEACHER_REVERTED')
                row['include_in_score_stats'] = next(base['include_in_score_stats'] for base in self._base_records if base['record_id'] == row['record_id'])
            elif action == 'ACCEPT_AI':
                accepted = validated_by_id[event['accepted_candidate_id']]
                row.update(effective_score=accepted['candidate_score'], effective_point_scores=deepcopy(accepted['candidate_point_scores']),
                           effective_score_source='AI_DEFAULT', effective_value_status='TEACHER_ACCEPTED_AI',
                           effective_candidate_id=accepted['candidate_id'])
                row['include_in_score_stats'] = row['status'] == '已评分' and not bool(set(row['issue_codes']) - SCORE_ERRORS - {'topic_mismatch'})
            elif action == 'EXCLUDE_SCOPE':
                for purpose in event['scopes']:
                    row[purpose] = False
                row['effective_value_status'] = 'TEACHER_EXCLUDED'
                row['exclude_reason'].append(event['decision_reason'])
            else:
                row['flagged_for_later'] = True
        scored = [row for row in records if row['include_in_score_stats']]
        stale = sum(connection.execute(f'SELECT COUNT(*) FROM {table} WHERE dataset_hash<>?',
                                      (self.dataset_hash,)).fetchone()[0]
                    for table in ('score_candidates', 'teacher_decisions'))
        snapshot = {'schema_version': 'MODEL_READY-1.0', 'rules_version': RULES_VERSION,
                    'dataset_hash': self.dataset_hash, 'batch_id': self.dataset_hash[:20],
                    'source_files': {name: {'sha256': item['sha256'], 'rows': item['rows']}
                                     for name, item in sorted(self.dataset['sources'].items())},
                    'as_of': self.dataset.get('as_of'), 'data_origin': 'SELF_CREATED' if self.is_self_created else 'SOURCE_PROVIDED',
                    'knowledge_hash': _hash({identity: item for identity, item in self._entities.items()
                                             if item['type'] not in {'STUDENT_RESPONSE', 'STUDENT_STEP'}}),
                    'records': records, 'history': deepcopy(self._history),
                    'unresolved_issues': deepcopy(self.dataset['issues']) + self._revision_issues,
                    'stale_event_count': stale,
                    'stale_passage_count': self._stale_passage_count,
                    'summary': {'raw_records': len(self._history), 'current_events': len(records),
                                'scored_events': len(scored), 'score_numerator': sum(row['effective_score'] for row in scored),
                                'score_denominator': sum(row['max_score'] for row in scored),
                                'pending_events': sum(row['status'] == '待评分' for row in records),
                                'unanswered_events': sum(row['status'] == '未作答' for row in records),
                                'semantic_status': 'NOT_RUN' if not candidates else 'PARTIAL',
                                'candidate_count': len(candidates), 'teacher_decision_count': len(decisions)},
                    'field_mapping': self.field_mapping}
        snapshot['snapshot_hash'] = _hash(snapshot)
        return snapshot

    def refresh(self):
        self._assert_sources()
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._load_imported_passages(connection)
            self._load_practices(connection)
            self._snapshot = self._build_snapshot(connection)
            connection.execute('INSERT OR IGNORE INTO model_ready_snapshots VALUES(?,?,?)',
                               (self._snapshot['snapshot_hash'], self.dataset_hash, _json(self._snapshot)))
        return deepcopy(self._snapshot)

    def _load_imported_passages(self, connection):
        verified = {}
        previous_ids = {item['id'] for item in self._imported_passages}
        for identity in previous_ids:
            self._entities.pop(identity, None)
        self._imported_passages = []
        self._stale_passage_count = 0
        for (encoded,) in connection.execute('SELECT payload FROM imported_passages WHERE dataset_hash=? ORDER BY passage_id',
                                             (self.dataset_hash,)):
            item = json.loads(encoded)
            if not self._passage_sources_valid(item['source_ref'], verified):
                self._stale_passage_count += 1
                continue
            self._entities[item['id']] = item
            self._imported_passages.append(item)

    def import_passages(self, passages, *, reviewer_id, expected_snapshot_hash=None, review_context='AUTOMATED'):
        """Import reviewed source slices after checking the actual file and text.

        UTF-8 text/Markdown is read directly. PDF import uses pypdf when installed
        and checks the requested physical page. It never reconstructs absent text.
        """
        if public_demo_mode():
            raise ValueError('PUBLIC_DEMO_IMPORT_DISABLED')
        self._assert_sources()
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise ValueError('REVIEWER_REQUIRED')
        if review_context not in {'AUTOMATED', 'DEMO_TEACHER'}:
            raise ValueError('INVALID_REVIEW_CONTEXT')
        if not isinstance(passages, list) or not passages:
            raise ValueError('PASSAGES_REQUIRED')
        checked = []
        for value in passages:
            required = {'passage_id', 'exact_text', 'source_path', 'source_hash', 'resource_title', 'edition', 'node_ids'}
            if not isinstance(value, dict) or not required <= set(value):
                raise ValueError('INVALID_PASSAGE')
            if any(not isinstance(value[field], str) or not value[field].strip()
                   for field in ('passage_id', 'exact_text', 'source_path', 'source_hash', 'resource_title', 'edition')):
                raise ValueError('INVALID_PASSAGE')
            if (not isinstance(value['node_ids'], list) or not value['node_ids'] or
                    any(identity not in self._nodes for identity in value['node_ids'])):
                raise ValueError('UNKNOWN_KNOWLEDGE_NODE')
            path = Path(value['source_path']).resolve()
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != value['source_hash']:
                raise ValueError('SOURCE_HASH_MISMATCH')
            physical = value.get('physical_page')
            ocr = None
            if path.suffix.casefold() == '.pdf':
                try:
                    from pypdf import PdfReader
                except ImportError as error:
                    raise ValueError('PDF_EXTRACTOR_REQUIRED') from error
                reader = PdfReader(path)
                if type(physical) is not int or not 1 <= physical <= len(reader.pages):
                    raise ValueError('INVALID_PHYSICAL_PAGE')
                source_text = reader.pages[physical - 1].extract_text() or ''
                if not source_text.strip() and value.get('ocr_evidence'):
                    source_text, ocr = self._verified_ocr_text(value, path, physical)
            elif path.suffix.casefold() in {'.txt', '.md'}:
                source_text = path.read_text(encoding='utf-8-sig')
            else:
                raise ValueError('UNSUPPORTED_SOURCE_FORMAT')
            if value['exact_text'] not in source_text:
                raise ValueError('EVIDENCE_MISMATCH')
            source = {'source_file_id': path.name, 'source_path': str(path), 'source_hash': value['source_hash'],
                      'source_version': value['source_hash'], 'physical_page': physical,
                      'printed_page': value.get('printed_page'), 'edition': value['edition'],
                      'resource_title': value['resource_title']}
            source['extraction_method'] = 'PDF_RENDER_OCR' if ocr else 'PDF_TEXT_LAYER' if path.suffix.casefold() == '.pdf' else 'UTF8_TEXT'
            if ocr:
                source['ocr'] = ocr
            human_reviewed = review_context == 'DEMO_TEACHER' and (not ocr or ocr['visual_review']['actor_type'] == 'DEMO_TEACHER')
            checked.append({'id': value['passage_id'], 'type': 'SOURCE_PASSAGE', 'exact_text': value['exact_text'],
                            'source_ref': source, 'content_hash': hashlib.sha256(value['exact_text'].encode('utf-8')).hexdigest(),
                            'knowledge_refs': value['node_ids'][:],
                            'review_status': 'HUMAN_VERIFIED' if human_reviewed else 'AI_CANDIDATE',
                            'source_validation': 'PASS', 'owner_accepted': False,
                            'source_authority': 'SOURCE_PROVIDED', 'reviewer_id': reviewer_id,
                            'origin': 'SOURCE_PROVIDED', 'external_authorized': value.get('external_authorized') is True,
                            'reviewer_role': review_context})
        if len({item['id'] for item in checked}) != len(checked):
            raise ValueError('DUPLICATE_PASSAGE_ID')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._load_imported_passages(connection)
            snapshot = self._build_snapshot(connection)
            if expected_snapshot_hash is not None and expected_snapshot_hash != snapshot['snapshot_hash']:
                raise ValueError('STALE_SNAPSHOT')
            for item in checked:
                existing = self._entities.get(item['id'])
                stored = connection.execute('SELECT payload FROM imported_passages WHERE dataset_hash=? AND passage_id=?',
                                            (self.dataset_hash, item['id'])).fetchone()
                if existing is not None and _json(existing) != _json(item) or stored is not None and stored[0] != _json(item):
                    raise ValueError('IMMUTABLE_PASSAGE_ID：修订内容请使用新版本ID')
                connection.execute('INSERT OR IGNORE INTO imported_passages VALUES(?,?,?)',
                                   (self.dataset_hash, item['id'], _json(item)))
                connection.execute('INSERT OR IGNORE INTO knowledge_entities VALUES(?,?,?)',
                                   (self.dataset_hash, item['id'], _json(item)))
        self.refresh()
        return {'imported_ids': [item['id'] for item in checked], 'snapshot_hash': self.snapshot_hash}

    def get(self, entity_id, audience='TEACHER', *, student_id=None):
        self.refresh()
        if audience not in {'TEACHER', 'STUDENT'}:
            raise ValueError('ACCESS_DENIED')
        item = self._entities.get(entity_id)
        if item is None:
            raise ValueError('EVIDENCE_NOT_FOUND')
        if audience == 'STUDENT' and (item['type'] == 'REFERENCE_ANSWER' or
                item.get('student_id') is not None and item['student_id'] != student_id):
            raise ValueError('ACCESS_DENIED')
        return {**deepcopy(item), 'snapshot_hash': self._snapshot['snapshot_hash']}

    def search(self, query='', node_ids=(), limit=20):
        self._assert_sources()
        if not isinstance(query, str) or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('INVALID_ARGUMENT')
        selected = set(node_ids)
        results = []
        for item in self._entities.values():
            if item['type'] in {'STUDENT_RESPONSE', 'STUDENT_STEP', 'REFERENCE_ANSWER'}:
                continue
            if selected and not selected.intersection(item.get('knowledge_refs', [])):
                continue
            if query and query.casefold() not in (item['exact_text'] + item['id']).casefold():
                continue
            results.append({'id': item['id'], 'entity_type': item['type'],
                            'display_name': item.get('name') or item.get('display_name') or
                                            item['source_ref'].get('resource_title', item['source_ref']['source_file_id']),
                            'review_status': item['review_status'], 'knowledge_refs': item.get('knowledge_refs', []),
                            'source_ref': deepcopy(item['source_ref'])})
        return results[:limit]

    def practices(self, node_ids=(), item_count=3):
        self._assert_sources()
        if type(item_count) is not int or not 1 <= item_count <= 30:
            raise ValueError('INVALID_ARGUMENT')
        wanted = set(node_ids)
        return [deepcopy(item) for item in self.practice_items.values()
                if item['source_status'] == 'APPROVED_PRACTICE' and
                (not wanted or wanted.intersection(item['knowledge_refs']))][:item_count]

    def get_practice(self, practice_id):
        items = self.practice_items
        if practice_id not in items:
            raise ValueError('SOURCE_NOT_APPROVED')
        return deepcopy(items[practice_id])

    def get_practice_for_review(self, practice_id, audience='TEACHER'):
        if audience != 'TEACHER':
            raise ValueError('ACCESS_DENIED')
        self.refresh()
        if practice_id not in self._registered_practices:
            raise ValueError('EVIDENCE_NOT_FOUND')
        return deepcopy(self._registered_practices[practice_id])

    def review_practice(self, practice_id, *, reviewer_id, approved, authorized_use=None,
                        expected_snapshot_hash=None, review_context='DEMO_TEACHER'):
        self._assert_sources()
        if not isinstance(reviewer_id, str) or not reviewer_id.strip() or review_context not in {'AUTOMATED', 'DEMO_TEACHER'}:
            raise ValueError('REVIEWER_REQUIRED')
        if type(approved) is not bool or authorized_use is not None and type(authorized_use) is not bool:
            raise ValueError('INVALID_PRACTICE_INPUT')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._load_imported_passages(connection)
            self._load_practices(connection)
            snapshot = self._build_snapshot(connection)
            if expected_snapshot_hash is not None and snapshot['snapshot_hash'] != expected_snapshot_hash:
                raise ValueError('STALE_SNAPSHOT')
            if practice_id not in self._registered_practices:
                raise ValueError('EVIDENCE_NOT_FOUND')
            item = self._registered_practices[practice_id]
            authorized = item['authorized_use'] if authorized_use is None else authorized_use
            accepted = approved and authorized and (item.get('math_validation') == 'VALID' or review_context == 'DEMO_TEACHER')
            review = {'practice_id': practice_id, 'approved': accepted, 'authorized_use': authorized,
                      'reviewer_id': reviewer_id, 'reviewer_role': review_context, 'owner_accepted': False,
                      'review_status': 'HUMAN_VERIFIED' if review_context == 'DEMO_TEACHER' else 'AI_CANDIDATE',
                      'math_validation': item.get('math_validation', 'UNSUPPORTED'),
                      'source_payload_hash': item['source_ref']['source_hash'],
                      'recorded_at': datetime.now(timezone.utc).isoformat()}
            connection.execute('INSERT INTO practice_reviews VALUES(?,?,?,?)',
                               (_hash(review), self.dataset_hash, practice_id, _json(review)))
        self.refresh()
        return {'practice_id': practice_id, 'source_status': 'APPROVED_PRACTICE' if accepted else 'GENERATED_CANDIDATE',
                'snapshot_hash': self.snapshot_hash}

    def _load_practices(self, connection):
        for identity in self._registered_practices:
            self._entities.pop(identity, None)
        self._registered_practices = {}
        rows = connection.execute('SELECT practice_id,payload FROM independent_practices WHERE dataset_hash=? ORDER BY rowid',
                                  (self.dataset_hash,)).fetchall()
        for identity, encoded in rows:
            item = json.loads(encoded)
            reviewed = connection.execute('SELECT payload FROM practice_reviews WHERE dataset_hash=? AND practice_id=? ORDER BY rowid DESC LIMIT 1',
                                          (self.dataset_hash, identity)).fetchone()
            review = json.loads(reviewed[0]) if reviewed else {}
            item.update(review)
            item['source_status'] = 'APPROVED_PRACTICE' if review.get('approved') else 'GENERATED_CANDIDATE'
            self._registered_practices[identity] = item
            self._entity(identity, 'PRACTICE_ITEM', item['exact_text'], item['source_ref'],
                         practice_id=identity, question_id=identity, knowledge_refs=item['knowledge_refs'],
                         review_status=item.get('review_status', 'AI_CANDIDATE'), source_status=item['source_status'])

    def import_practice_items(self, items, *, reviewer_id, expected_snapshot_hash=None, approved=False, review_context='AUTOMATED'):
        self._assert_sources()
        if not isinstance(reviewer_id, str) or not reviewer_id.strip() or review_context not in {'AUTOMATED', 'DEMO_TEACHER'}:
            raise ValueError('REVIEWER_REQUIRED')
        if type(approved) is not bool or not isinstance(items, list) or not items:
            raise ValueError('INVALID_PRACTICE_INPUT')
        checked = []
        for value in items:
            fields = ('practice_id', 'exact_text', 'reference_answer', 'reference_explanation', 'source_description', 'author')
            if not isinstance(value, dict) or any(not isinstance(value.get(field), str) or not value[field].strip() for field in fields):
                raise ValueError('INVALID_PRACTICE_INPUT')
            nodes = value.get('knowledge_refs')
            if (not isinstance(nodes, list) or not nodes or not set(nodes) <= set(self._nodes)
                    or type(value.get('authorized_use')) is not bool):
                raise ValueError('INVALID_PRACTICE_AUTHORIZATION')
            identity = value['practice_id']
            if identity in self._questions or identity in self._nodes:
                raise ValueError('PRACTICE_ID_COLLISION')
            item = {field: value[field] for field in fields}
            item.update(id=identity, question_id=identity, knowledge_refs=nodes[:], authorized_use=value['authorized_use'],
                        source_question_id=value.get('source_question_id'), origin='PROJECT_DEFINED')
            payload_hash = _hash(item)
            item['source_ref'] = {'source_file_id': 'project_practice_registry', 'source_hash': payload_hash,
                                  'source_version': payload_hash, 'source_description': value['source_description']}
            math = response_feedback(item['exact_text'], item['reference_answer'], item['reference_answer'], '')
            accepted = approved and value['authorized_use'] and (math['answer_validity'] == 'VALID' or review_context == 'DEMO_TEACHER')
            review = {'practice_id': identity, 'approved': accepted, 'reviewer_id': reviewer_id,
                      'reviewer_role': review_context, 'owner_accepted': False,
                      'review_status': 'HUMAN_VERIFIED' if review_context == 'DEMO_TEACHER' else 'AI_CANDIDATE',
                      'math_validation': math['answer_validity'], 'source_payload_hash': payload_hash}
            checked.append((item, review))
        if len({item['practice_id'] for item, _ in checked}) != len(checked):
            raise ValueError('DUPLICATE_PRACTICE_ID')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._load_imported_passages(connection)
            self._load_practices(connection)
            snapshot = self._build_snapshot(connection)
            if expected_snapshot_hash is not None and expected_snapshot_hash != snapshot['snapshot_hash']:
                raise ValueError('STALE_SNAPSHOT')
            for item, review in checked:
                identity = item['practice_id']
                stored = connection.execute('SELECT payload FROM independent_practices WHERE dataset_hash=? AND practice_id=?',
                                            (self.dataset_hash, identity)).fetchone()
                if stored is not None and stored[0] != _json(item):
                    raise ValueError('IMMUTABLE_PRACTICE_ID')
                connection.execute('INSERT OR IGNORE INTO independent_practices VALUES(?,?,?)',
                                   (self.dataset_hash, identity, _json(item)))
                connection.execute('INSERT OR IGNORE INTO practice_reviews VALUES(?,?,?,?)',
                                   (_hash(review), self.dataset_hash, identity, _json(review)))
        self.refresh()
        return {'practice_ids': [item['practice_id'] for item, _ in checked],
                'source_status': {item['practice_id']: 'APPROVED_PRACTICE' if review['approved'] else 'GENERATED_CANDIDATE'
                                  for item, review in checked}, 'snapshot_hash': self.snapshot_hash}

    def evidence_bundle(self, record_id, audience='TEACHER', *, student_id=None):
        self.get(record_id, audience, student_id=student_id)
        snapshot = self.snapshot
        row = next((item for item in snapshot['records'] if item['record_id'] == record_id), None)
        if row is None:
            raise ValueError('RESPONSE_NOT_CURRENT')
        question = deepcopy(self._questions[row['question_id']])
        if audience == 'STUDENT':
            question.pop('reference_answer', None)
            question.pop('reference_explanation', None)
        steps = [deepcopy(self._entities[identity]) for identity in row['step_refs']]
        links = [deepcopy(item) for item in self.relations if item['question_id'] == row['question_id']]
        passages = [deepcopy(item) for item in self._entities.values() if item['type'] == 'SOURCE_PASSAGE'
                    and set(row['knowledge_refs']).intersection(item.get('knowledge_refs', []))]
        refs = [record_id, row['question_id'], *row['rubric_refs'], *row['knowledge_refs'],
                *row['step_refs'], *(passage['id'] for passage in passages)]
        return {'schema_version': '1.1', 'bundle_id': 'EB:' + record_id, 'snapshot_hash': snapshot['snapshot_hash'],
                'question': question, 'response': deepcopy(row), 'rubric_points': question['rubric_points'],
                'student_steps': steps, 'knowledge_links': links, 'source_passages': passages,
                'evidence_refs': [{'id': identity, 'evidence_id': identity, 'type': self._entities[identity]['type'],
                                   'source_hash': self._entities[identity]['source_ref']['source_hash']} for identity in refs],
                'verification_state': deepcopy(row['validation'])}

    def _score_bounds(self, row, score, points):
        limits = self.dataset['question_points'][row['question_id']]
        if (type(score) is not int or not isinstance(points, dict) or set(points) != set(limits)
                or any(type(value) is not int or not 0 <= value <= limits[key] for key, value in points.items())
                or sum(points.values()) != score or not 0 <= score <= row['max_score']):
            raise ValueError('INVALID_SCORE：总分与真实评分点范围必须一致')

    def _rubric_facts(self, row):
        if not self.is_self_created:
            return None, None
        question = self._questions[row['question_id']]
        feedback = response_feedback(question['exact_text'], question['reference_answer'],
                                     row['student_answer'], row['student_steps'])
        if feedback['answer_validity'] == 'UNRESOLVED' or any(step['local_validity'] == 'UNRESOLVED' for step in feedback['steps']):
            return None, feedback
        scores = {}
        for point in question['rubric_points']:
            text = point['exact_text']
            negative = [step for step in feedback['steps'] if step['rule']['id'] == 'NEGATIVE_SCALING']
            valid = [step for step in feedback['steps'] if step['local_validity'] == 'VALID']
            if text == '同除以负数并计算边界':
                fulfilled = bool(negative)
            elif text in {'正确改变不等号方向', '同除负数并改变方向'}:
                fulfilled = any(step['local_validity'] == 'VALID' for step in negative)
            elif text.startswith('移项得到'):
                expected = clean_expression(text.removeprefix('移项得到'))
                fulfilled = any(clean_expression(step['target']) == expected for step in valid)
            elif text == '正确填写最终化简结果':
                fulfilled = feedback['answer_validity'] == 'VALID'
            elif text in {'识别同类项', '正确合并同类项'}:
                fulfilled = any(step['source'].count('x') >= 2 and step['target'].count('x') == 1
                                and '(' not in step['source'] and '(' not in step['target'] for step in valid)
            elif text == '正确去括号':
                fulfilled = any('(' in step['source'] and '(' not in step['target'] for step in valid)
            else:
                return None, feedback
            scores[point['id']] = point['max_score'] if fulfilled else 0
        return scores, feedback

    def _cross_checked(self, proposal):
        paths = [proposal.get('path_a'), proposal.get('path_b')]
        for item in paths:
            if not isinstance(item, dict):
                return False
            context_hash = item.get('locked_context_hash')
            if not isinstance(context_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', context_hash):
                return False
            if item.get('output_hash') != _hash({key: value for key, value in item.items() if key != 'output_hash'}):
                return False
            if item.get('candidate_score') != proposal['candidate_score'] or item.get('candidate_point_scores') != proposal['candidate_point_scores']:
                return False
        return paths[0]['locked_context_hash'] != paths[1]['locked_context_hash']

    def _existing_request(self, connection, table, request):
        if not isinstance(request, dict) or not isinstance(request.get('request_id'), str) or not request['request_id'].strip():
            raise ValueError('INVALID_REQUEST_ID')
        found = connection.execute(f'SELECT request_hash,payload FROM {table} WHERE request_id=?',
                                   (request['request_id'],)).fetchone()
        if found is None:
            return None
        if found[0] != _hash(request):
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return json.loads(found[1])

    def _bound_record(self, request, snapshot):
        row = next((item for item in snapshot['records'] if item['record_id'] == request.get('record_id')), None)
        if (row is None or request.get('source_dataset_hash') != self.dataset_hash
                or request.get('source_revision') != row['selected_revision']):
            raise ValueError('STALE_SOURCE：记录、修订或数据集哈希不匹配')
        return row

    def add_candidate(self, proposal):
        self._assert_sources()
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = self._existing_request(connection, 'score_candidates', proposal)
            if existing is not None:
                return existing
            self._load_imported_passages(connection)
            snapshot = self._build_snapshot(connection)
            row = self._bound_record(proposal, snapshot)
            if proposal.get('event_id') != row['event_id']:
                raise ValueError('INVALID_EVENT')
            if row['status'] != '已评分':
                raise ValueError('SCORING_STATE_NOT_READY')
            self._score_bounds(row, proposal.get('candidate_score'), proposal.get('candidate_point_scores'))
            quotes = proposal.get('evidence_quotes')
            allowed = {row['record_id'], row['question_id'], *row['rubric_refs'], *row['step_refs']}
            if not isinstance(quotes, list) or not quotes:
                raise ValueError('EVIDENCE_REQUIRED')
            for quote in quotes:
                if (not isinstance(quote, dict) or quote.get('id') not in allowed or
                        not isinstance(quote.get('quote'), str) or not quote['quote'].strip() or
                        quote['quote'] not in self._entities[quote['id']]['exact_text']):
                    raise ValueError('EVIDENCE_MISMATCH')
            rubric_scores, feedback = self._rubric_facts(row)
            validation = {'schema_validation': True, 'evidence_validation': True,
                          'math_validation': feedback is not None and feedback['answer_validity'] != 'UNRESOLVED',
                          'rubric_validation': rubric_scores is not None and rubric_scores == proposal['candidate_point_scores'],
                          'cross_check_status': 'LOCKED_OUTPUTS_MATCH' if self._cross_checked(proposal) else 'UNRESOLVED'}
            arbitration = proposal.get('semantic_arbitration', {'required': False, 'status': 'NOT_REQUIRED'})
            arbitration_ok = isinstance(arbitration, dict) and (arbitration.get('required') is False or
                                                               arbitration.get('required') is True and arbitration.get('status') == 'ALIGNED')
            validation['semantic_arbitration'] = arbitration.get('status', 'UNRESOLVED') if isinstance(arbitration, dict) else 'UNRESOLVED'
            validated = all(validation[key] for key in ('schema_validation', 'evidence_validation', 'math_validation', 'rubric_validation')) and validation['cross_check_status'] == 'LOCKED_OUTPUTS_MATCH' and arbitration_ok
            candidate = {**deepcopy(proposal), 'candidate_id': 'CAND:' + _hash(proposal)[:24],
                         'candidate_status': 'VALIDATED' if validated else 'UNRESOLVED', 'validation': validation,
                         'verified_rubric_scores': rubric_scores, 'created_at': datetime.now(timezone.utc).isoformat()}
            connection.execute('INSERT INTO score_candidates VALUES(?,?,?,?,?)',
                               (candidate['candidate_id'], proposal['request_id'], self.dataset_hash, _hash(proposal), _json(candidate)))
        self.refresh()
        return deepcopy(candidate)

    def teacher_decision(self, decision):
        self._assert_sources()
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = self._existing_request(connection, 'teacher_decisions', decision)
            if existing is not None:
                return existing
            self._load_imported_passages(connection)
            snapshot = self._build_snapshot(connection)
            row = self._bound_record(decision, snapshot)
            if decision.get('previous_snapshot_hash') != snapshot['snapshot_hash']:
                raise ValueError('STALE_SNAPSHOT')
            action = decision.get('teacher_action')
            if action not in {'ACCEPT_AI', 'MODIFY_VALUE', 'REVERT_TO_OFFICIAL', 'EXCLUDE_SCOPE', 'FLAG_FOR_LATER'}:
                raise ValueError('INVALID_TEACHER_ACTION')
            if any(not isinstance(decision.get(field), str) or not decision[field].strip()
                   for field in ('decision_reason', 'reviewer_id')):
                raise ValueError('REASON_AND_REVIEWER_REQUIRED')
            if action == 'MODIFY_VALUE':
                if decision.get('confirmed') is not True:
                    raise ValueError('CONFIRMATION_REQUIRED')
                self._score_bounds(row, decision.get('effective_score'), decision.get('effective_point_scores'))
            if action == 'ACCEPT_AI' and not row.get('effective_candidate_id'):
                raise ValueError('VALIDATED_AI_CANDIDATE_REQUIRED')
            if action == 'EXCLUDE_SCOPE':
                scopes = decision.get('scopes')
                if not isinstance(scopes, list) or not scopes or not set(scopes) <= set(PURPOSES):
                    raise ValueError('INVALID_EXCLUSION_SCOPE')
            event = {**deepcopy(decision), 'decision_audit_id': 'TD:' + _hash(decision)[:24],
                     'event_id': row['event_id'], 'official_score': row['official_score'],
                     'candidate_score': row['candidate_score'], 'previous_effective_score': row['effective_score'],
                     'reviewer_role': 'DEMO_TEACHER', 'review_policy_version': RULES_VERSION,
                     'reviewed_at': datetime.now(timezone.utc).isoformat()}
            if action == 'ACCEPT_AI':
                event['accepted_candidate_id'] = row['effective_candidate_id']
            events = [*self._load_events(connection, 'teacher_decisions'), event]
            updated = self._build_snapshot(connection, decisions=events)
            event['new_snapshot_hash'] = updated['snapshot_hash']
            event['new_effective_score'] = next(item['effective_score'] for item in updated['records'] if item['record_id'] == row['record_id'])
            connection.execute('INSERT INTO teacher_decisions VALUES(?,?,?,?,?)',
                               (event['decision_audit_id'], decision['request_id'], self.dataset_hash, _hash(decision), _json(event)))
        self.refresh()
        return deepcopy(event)
