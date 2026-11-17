"""v1.1治理层，复用旧Schema/校验器，原分与复核分分轨。"""
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from official_data import load_dataset
from governance_io import read_official, stage_normalized, normalize_text, source_identity


SCORE_ERRORS = frozenset({'score_out_of_range', 'point_total_mismatch', 'point_out_of_range',
                          'missing_score', 'invalid_score', 'invalid_point_scores', 'point_id_mismatch'})


def _events(rows, meta):
    groups = defaultdict(list)
    for row in rows:
        groups[row['作答事件编号']].append(row)
    events, selected, extras = (), frozenset(), ()
    for event_id, versions in groups.items():
        revisions = [meta[row['记录编号']]['revision'] for row in versions]
        maximum = max(revisions) if all(value is not None for value in revisions) else None
        highest = [row for row in versions if meta[row['记录编号']]['revision'] == maximum]
        conflict = maximum is None or len(highest) != 1
        record = None if conflict else highest[0]
        events = (*events, {'event_id': event_id, 'record_id': record['记录编号'] if record else None,
                           'revision': maximum, 'resolution': 'CONFLICT' if conflict else 'SELECTED',
                           'candidate_record_ids': [row['记录编号'] for row in highest],
                           'version_count': len(versions)})
        if record:
            selected = selected | {record['记录编号']}
        if conflict or len(revisions) != len(set(revisions)):
            extras = (*extras, {'record_id': record['记录编号'] if record else None,
                               'event_id': event_id, 'code': 'highest_revision_conflict' if conflict else 'duplicate_historical_revision',
                               'severity': 'BLOCKER' if conflict else 'WARNING',
                               'evidence_ids': [row['记录编号'] for row in versions]})
        dated = []
        for row in versions:
            if 'invalid_timestamp' in meta[row['记录编号']]['issue_codes']:
                continue
            try:
                dated.append((meta[row['记录编号']]['revision'], datetime.fromisoformat(row['模拟提交时间'])))
            except (ValueError, TypeError):
                continue
        reversed_time = any(a[0] is not None and b[0] is not None and a[0] < b[0]
                            and a[1].utcoffset() is not None and b[1].utcoffset() is not None and a[1] > b[1]
                            for a in dated for b in dated)
        if reversed_time:
            extras = (*extras, *({'record_id': row['记录编号'], 'event_id': event_id,
                                  'code': 'revision_time_conflict', 'severity': 'ISOLATED_ERROR'} for row in versions))
    return list(events), selected, list(extras)


def _candidate(row, normalized, meta, question, point_limits):
    reasons, proposed, proposed_points = (), None, None
    points = meta['point_scores']
    points_valid = set(points) == set(point_limits) and all(0 <= score <= point_limits[key] for key, score in points.items())
    if set(meta['issue_codes']) & SCORE_ERRORS:
        reasons = (*reasons, 'SCORING_CONFLICT')
        if points_valid:
            proposed, proposed_points = sum(points.values()), dict(points)
    answer = normalize_text(normalized['学生最终作答'])
    reference = normalize_text(question.get('参考答案', ''))
    if normalized['评分状态'] == '已评分' and answer and answer == reference and meta['score'] == 0:
        reasons = (*reasons, 'ZERO_SCORE_REFERENCE_TEXT_MATCH')
        if len(point_limits) == 1:
            proposed, proposed_points = sum(point_limits.values()), dict(point_limits)
    process = normalize_text(normalized['解答过程'])
    if reference and answer != reference and reference in process:
        reasons = (*reasons, 'PROCESS_CONTAINS_REFERENCE_FINAL_TEXT_DIFFERS')
    if answer and reference and answer != reference and meta['score'] == meta['max_score']:
        reasons = (*reasons, 'FULL_SCORE_DIFFERENT_ANSWER_TEXT_NEEDS_SEMANTICS')
    return {'reviewed_score': proposed, 'reviewed_point_scores': proposed_points,
            'review_status': 'PENDING' if reasons else 'UNREVIEWED', 'review_reasons': list(reasons),
            'review_evidence': [normalized['记录编号'], normalized['材料编号']] if reasons else [],
            'reviewer': '', 'reviewed_at': '', 'candidate_scope': 'CANDIDATE_ONLY_NOT_ADJUDICATED'}


def _apply_reviews(rows, decisions, dataset, input_sha, dataset_sha):
    if not isinstance(decisions, list) or any(not isinstance(item, dict) or
            not isinstance(item.get('record_id'), str) for item in decisions):
        raise ValueError('审核输入必须为具有字符串记录ID的对象列表')
    if not decisions:
        return rows
    known = {row['record_id']: row for row in rows}
    if len({item.get('record_id') for item in decisions}) != len(decisions):
        raise ValueError('审核决定记录编号重复')
    checked = {}
    for item in decisions:
        rid = item.get('record_id')
        if (rid not in known or item.get('input_sha256') != input_sha
                or item.get('dataset_sha256') != dataset_sha):
            raise ValueError('审核来源或记录编号不符')
        if item.get('review_status') not in ('CONFIRMED', 'DISPUTED'):
            raise ValueError('审核状态无效')
        if not isinstance(item.get('reviewer'), str) or not item['reviewer'].strip():
            raise ValueError('审核人不能为空')
        try:
            timestamp = datetime.fromisoformat(item['reviewed_at'])
            if timestamp.utcoffset() is None:
                raise ValueError('缺时区')
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError('审核时间需要带时区') from error
        evidence = item.get('review_evidence')
        allowed = set(known) | set(dataset['materials']) | set(dataset['requirements'])
        if not isinstance(evidence, list) or not evidence or not all(isinstance(ref, str) and ref in allowed for ref in evidence):
            raise ValueError('审核依据缺失或超出允许集合')
        score, points = item.get('reviewed_score'), item.get('reviewed_point_scores')
        limits = dataset['question_points'].get(known[rid]['question_id'], {})
        if item['review_status'] == 'CONFIRMED':
            if (type(score) is not int or not isinstance(points, dict) or set(points) != set(limits)
                    or any(type(value) is not int or not 0 <= value <= limits[key] for key, value in points.items())
                    or sum(points.values()) != score):
                raise ValueError('审核评分点、范围及总分不一致')
        checked[rid] = {key: item[key] for key in ('review_status', 'reviewed_score', 'reviewed_point_scores',
                                                  'reviewer', 'reviewed_at', 'review_evidence')}
    return [{**row, **checked.get(row['record_id'], {})} for row in rows]


def _rows(tables, normalized, dataset, selected, extras):
    result = []
    for index, (raw, row) in enumerate(zip(tables['records'], normalized['records']), 2):
        rid = row['记录编号']
        meta = dataset['record_meta'][rid]
        question = dataset['questions'].get(row['材料编号'], {})
        issues = [code for code in meta['issue_codes'] if code != 'ambiguous_revision']
        issues = sorted(set(issues) | {item['code'] for item in extras if item['record_id'] == rid})
        issues = [code for code in issues if code != 'duplicate_historical_revision']
        errors = [code for code in issues if code != 'topic_mismatch']
        selected_now = rid in selected
        value = {**raw, 'record_id': rid, 'event_id': row['作答事件编号'],
                 'revision': meta['revision'], 'student_id': row['学生编号'], 'node_id': row['节点编号'],
                 'question_id': row['材料编号'], 'source_row': index, 'status': row['评分状态'],
                 'normalized_fields': row, 'normalized_topic_id': question.get('主题编号'),
                 'indicator_id': question.get('指标编号'), 'official_score': raw['得分'],
                 'official_point_scores': raw['评分点得分'], 'normalized_score': meta['score'],
                 'normalized_max_score': meta['max_score'], 'normalized_point_scores': dict(meta['point_scores']),
                 'selected': selected_now, 'issue_codes': issues, 'main_error_codes': errors,
                 'score_eligible': selected_now and row['评分状态'] == '已评分' and not errors,
                 'association_strict_eligible': selected_now and row['评分状态'] == '已评分' and not issues,
                 'diagnosis_available': bool(row['学生最终作答'] or row['解答过程']),
                 'diagnosis_scope': 'OUT_OF_NODE' if any(code.endswith('outside_node') for code in issues) else 'IN_NODE',
                 **_candidate(raw, row, meta, question, dataset['question_points'].get(row['材料编号'], {}))}
        result = [*result, value]
    return result


def build_governance(data, stage, *, reviews=None, assignments=None):
    folder, tables, sources = read_official(data)
    stage = Path(stage).resolve()
    if stage.is_relative_to(folder) or folder.is_relative_to(stage):
        raise ValueError('标准化输出不得覆盖或包围原始输入目录')
    normalized = stage_normalized(tables, stage)
    dataset = load_dataset(stage)
    events, selected, extras = _events(normalized['records'], dataset['record_meta'])
    rows = _rows(tables, normalized, dataset, selected, extras)
    rows = _apply_reviews(rows, [] if reviews is None else reviews, dataset, sources['作答记录.csv']['sha256'], source_identity(sources))
    blockers = {'unknown_student', 'unknown_question', 'unknown_node', 'event_identity_mismatch', 'invalid_revision'}
    exceptions = [{**item, 'severity': 'BLOCKER' if item['code'] in blockers else
                   'WARNING' if item['code'] == 'topic_mismatch' else 'ISOLATED_ERROR'}
                  for item in dataset['issues'] if item['code'] != 'ambiguous_revision'] + extras
    candidates = [row for row in rows if row['review_status'] != 'UNREVIEWED']
    history = []
    by_id = {row['record_id']: row for row in rows}
    event_map = {event['event_id']: event for event in events}
    for row in rows:
        event = event_map[row['event_id']]
        current = by_id.get(event['record_id'])
        changes = {key: {'historical': row[key], 'current': current[key]} for key in tables['records'][0]
                   if current and row[key] != current[key]}
        history = [*history, {'record_id': row['record_id'], 'event_id': row['event_id'], 'revision': row['revision'],
                             'selected': row['selected'], 'resolution': event['resolution'],
                             'is_historical': not row['selected'] and event['record_id'] is not None,
                             'current_record_id': event['record_id'], 'changes_to_current': changes}]
    from governance_views import make_views
    views = make_views(rows, dataset, assignments)
    main = [row for row in rows if row['score_eligible']]
    confirmed = [row for row in rows if row['selected'] and row['status'] == '已评分'
                 and not (set(row['main_error_codes'])-SCORE_ERRORS)
                 and (row['score_eligible'] or row['review_status'] == 'CONFIRMED')]
    gate = 'BLOCKED' if any(item['severity'] == 'BLOCKER' for item in exceptions) else 'PASS_WITH_WARNINGS' if exceptions or candidates else 'PASS'
    summary = {'raw_records': len(rows), 'unique_events': len(events),
               'multiple_revision_events': sum(event['version_count'] > 1 for event in events),
               'historical_records': sum(row['is_historical'] for row in history),
               'unresolved_revision_records': sum(row['resolution'] == 'CONFLICT' for row in history),
               'raw_statuses': dict(Counter(row['status'] for row in rows)),
               'snapshot_statuses': dict(Counter(by_id[e['record_id']]['status'] if e['record_id'] else 'CONFLICT' for e in events)),
               'main_scored_events': len(main), 'association_strict_scored_events': sum(row['association_strict_eligible'] for row in rows),
               'confirmed_scored_events': len(confirmed), 'main_score': sum(row['normalized_score'] for row in main),
               'main_max_score': sum(row['normalized_max_score'] for row in main),
               'exception_record_count': len({item['record_id'] for item in exceptions if item.get('record_id')}),
               'review_candidates': len(candidates),
               'pending_current_candidates': sum(row['selected'] and row['review_status'] == 'PENDING' for row in rows),
               'human_confirmed_decisions': sum(row['review_status'] == 'CONFIRMED' for row in rows)}
    return {'rules_version': 'v1.1', 'gate': gate, 'tables': tables, 'dataset': dataset, 'sources': sources,
            'input_identity': source_identity(sources), 'normalized': rows, 'events': events, 'revisions': history, 'exceptions': exceptions,
            'reviews': candidates, 'views': views, 'summary': summary}
