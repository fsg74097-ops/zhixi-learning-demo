"""Read-only peer-group candidates based on the current MODEL_READY snapshot."""
from collections import Counter, defaultdict
from hashlib import sha256
from itertools import combinations
from json import dumps
from math import isfinite


def _valid_id(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _numeric(value):
    return type(value) is int or (type(value) is float and isfinite(value))


def _usable_score(row):
    maximum, score = row.get('max_score'), row.get('effective_score')
    return (row.get('include_in_score_stats') is True
            and row.get('status') == '已评分'
            and not row.get('flagged_for_later')
            and not row.get('issue_codes')
            and row.get('effective_value_status') != 'UNRESOLVED'
            and _numeric(maximum) and _numeric(score) and maximum > 0 and 0 <= score <= maximum)


def _rubric(row):
    refs = row.get('rubric_refs', ())
    if isinstance(refs, (list, tuple)) and all(_valid_id(ref) for ref in refs):
        return frozenset(refs)
    return None


def _comparison(left, right, policy):
    questions = left.keys() | right.keys()
    common = []
    for question_id in sorted(left.keys() & right.keys()):
        left_row, right_row = left[question_id], right[question_id]
        if left_row is None or right_row is None:
            continue
        left_rubric = _rubric(left_row)
        if (left_row['max_score'] == right_row['max_score']
                and left_rubric is not None and left_rubric == _rubric(right_row)):
            common.append((question_id, left_row, right_row))
    coverage = len(common) / len(questions) if questions else 0.0
    if len(common) < policy['min_common_questions'] or coverage < policy['min_coverage']:
        return None
    return common, len(questions), coverage


def _direction(indicator_id, comparison, policy):
    common, question_union_count, coverage = comparison
    maximum = sum(left['max_score'] for _, left, _ in common)
    left_score = sum(left['effective_score'] for _, left, _ in common)
    right_score = sum(right['effective_score'] for _, _, right in common)
    left_rate, right_rate = left_score / maximum, right_score / maximum
    explain_min, review_max = policy['min_explain_rate'], policy['max_review_rate']
    if left_rate >= explain_min and right_rate <= review_max:
        explain_score, review_score = left_score, right_score
    elif right_rate >= explain_min and left_rate <= review_max:
        common = [(question, right, left) for question, left, right in common]
        explain_score, review_score = right_score, left_score
    else:
        return None

    question_evidence = [{'question_id': question_id, 'explain_record_id': explain['record_id'],
                          'review_record_id': review['record_id'], 'explain_score': explain['effective_score'],
                          'review_score': review['effective_score'], 'max_score': explain['max_score']}
                         for question_id, explain, review in common]
    return {'indicator_id': indicator_id, 'explain_student_id': common[0][1]['student_id'],
            'review_student_id': common[0][2]['student_id'],
            'explain_score': explain_score, 'review_score': review_score, 'max_score': maximum,
            'explain_rate': explain_score / maximum, 'review_rate': review_score / maximum,
            'common_question_count': len(common), 'question_union_count': question_union_count,
            'coverage': coverage, 'question_evidence': question_evidence,
            'evidence_refs': [item[field] for item in question_evidence
                              for field in ('explain_record_id', 'review_record_id')]}


def suggest_complementary_groups(snapshot, *, class_id, node_id, indicator_ids, min_common_questions=2,
                                 min_coverage=0.6, min_explain_rate=0.75, max_review_rate=0.5,
                                 max_candidates=20):
    """Return temporary two-student candidates for teacher review.

    Score rates share the same set of uniquely scored questions and the same
    possible points. Coverage uses the union of both students' observed current
    questions for each single-indicator comparison, including unscored records.
    """
    if (not _valid_id(class_id) or not _valid_id(node_id)
            or not isinstance(indicator_ids, (list, tuple))
            or any(not _valid_id(indicator) for indicator in indicator_ids)
            or len(set(indicator_ids)) < 2 or len(set(indicator_ids)) != len(indicator_ids)):
        raise ValueError('INVALID_GROUP_SCOPE')
    if (type(min_common_questions) is not int or min_common_questions < 2
            or not _numeric(min_coverage) or not 0 < min_coverage <= 1
            or not _numeric(min_explain_rate) or not _numeric(max_review_rate)
            or not 0 <= max_review_rate < min_explain_rate <= 1
            or type(max_candidates) is not int or max_candidates < 1):
        raise ValueError('INVALID_GROUP_POLICY')
    if (not isinstance(snapshot, dict) or not _valid_id(snapshot.get('snapshot_hash'))
            or not isinstance(snapshot.get('records'), list)
            or any(not isinstance(row, dict) for row in snapshot['records'])):
        raise ValueError('INVALID_GROUP_SNAPSHOT')

    snapshot_hash = snapshot['snapshot_hash']
    indicators = sorted(indicator_ids)
    policy = {'min_common_questions': min_common_questions, 'min_coverage': min_coverage,
              'min_explain_rate': min_explain_rate, 'max_review_rate': max_review_rate,
              'max_candidates': max_candidates}
    scoped = [row for row in snapshot['records']
              if row.get('class_id') == class_id and row.get('node_id') == node_id and row.get('selected') is True
              and all(_valid_id(row.get(key)) for key in ('record_id', 'student_id', 'question_id'))]
    student_questions = Counter((row['student_id'], row['question_id']) for row in scoped)
    record_ids = Counter(row['record_id'] for row in snapshot['records'] if _valid_id(row.get('record_id')))
    evidence = defaultdict(lambda: defaultdict(dict))
    for row in scoped:
        refs = row.get('knowledge_refs')
        if not isinstance(refs, (list, tuple)) or len(refs) != 1 or refs[0] not in indicators:
            continue
        student_id, question_id = row['student_id'], row['question_id']
        unique = student_questions[student_id, question_id] == 1 and record_ids[row['record_id']] == 1
        evidence[student_id][refs[0]][question_id] = row if unique and _usable_score(row) else None

    candidates = []
    comparable_pair_exists = False
    eligible_count = 0
    for left, right in combinations(sorted(evidence), 2):
        directions = []
        comparable_indicators = 0
        for indicator_id in indicators:
            comparison = _comparison(evidence[left].get(indicator_id, {}), evidence[right].get(indicator_id, {}), policy)
            if comparison is None:
                continue
            comparable_indicators += 1
            direction = _direction(indicator_id, comparison, policy)
            if direction is not None:
                directions.append(direction)
        comparable_pair_exists |= comparable_indicators >= 2
        if {item['explain_student_id'] for item in directions} != {left, right}:
            continue
        eligible_count += 1
        if len(candidates) < max_candidates:
            identity = dumps([snapshot_hash, class_id, node_id, left, right, indicators], ensure_ascii=False)
            candidate_id = 'GROUP:' + sha256(identity.encode('utf-8')).hexdigest()[:24]
            candidates.append({'candidate_id': candidate_id, 'status': 'CANDIDATE',
                               'student_ids': [left, right], 'directions': directions})

    if candidates:
        status = 'CANDIDATES_AVAILABLE'
    elif comparable_pair_exists:
        status = 'NO_COMPLEMENTARY_PAIRS'
    else:
        status = 'INSUFFICIENT_COMPARABLE_EVIDENCE'
    return {'status': status, 'snapshot_hash': snapshot_hash, 'class_id': class_id, 'node_id': node_id,
            'indicator_ids': indicators, 'policy': policy, 'scoped_student_count': len({row['student_id'] for row in scoped}),
            'denominator_policy': 'scored_only', 'comparison_basis': 'SAME_NODE_QUESTION_AND_RUBRIC',
            'eligible_candidate_count': eligible_count, 'candidates': candidates}
