"""六类用途视图与明确分母的口径敏感性分析。"""
from collections import Counter, defaultdict
import itertools
import json


SCORING_ERRORS = frozenset({'score_out_of_range', 'point_total_mismatch', 'point_out_of_range',
                            'missing_score', 'invalid_score', 'invalid_point_scores', 'point_id_mismatch'})


def _keys(row, dataset):
    student, node, question = row['student_id'], row['node_id'], row['question_id']
    classroom = dataset['students'].get(student, {}).get('模拟班级编号', 'UNKNOWN')
    keys = [('ALL', 'ALL', 'ALL'), ('NODE', node, node), ('STUDENT_NODE', student, node),
            ('CLASS_NODE', classroom, node), ('QUESTION_NODE', question, node),
            ('QUESTION_CLASS_NODE', json.dumps([classroom, question], ensure_ascii=False), node)]
    if row.get('indicator_id'):
        keys += [('INDICATOR_NODE', row['indicator_id'], node)]
    if row.get('normalized_topic_id'):
        keys += [('TOPIC_NODE', row['normalized_topic_id'], node)]
    return keys


def score_rows(rows, dataset, policy, *, all_rows=(), reviewed=False):
    groups = defaultdict(list)
    groups[('ALL', 'ALL', 'ALL')] = []
    for sid in dataset['students']:
        for nid in dataset['nodes']:
            groups[('STUDENT_NODE', sid, nid)] = []
    for row in rows:
        for key in _keys(row, dataset):
            groups[key].append(row)
    context = defaultdict(set)
    for row in all_rows:
        if row['selected'] and row['status'] == '已评分':
            for key in _keys(row, dataset):
                context[key].add(row['record_id'])
                groups[key]
    result = []
    for (scope, group, node), values in sorted(groups.items()):
        scores = [r['reviewed_score'] if reviewed and r['review_status'] == 'CONFIRMED' else r['normalized_score'] for r in values]
        maximum = sum(r['normalized_max_score'] for r in values)
        ids = [r['record_id'] for r in values]
        result.append({'policy': policy, 'scope': scope, 'group_id': group, 'node_id': node,
                       'score': sum(scores), 'max_score': maximum, 'score_rate': sum(scores)/maximum if maximum else None,
                       'events_in_denominator': len(values), 'scored_events': sum(r['status'] == '已评分' for r in values),
                       'student_count': len({r['student_id'] for r in values}),
                       'question_ids': sorted({r['question_id'] for r in values}),
                       'indicator_ids': sorted({r['indicator_id'] for r in values if r.get('indicator_id')}),
                       'evidence_ids': ids, 'excluded_scored_events': len(context[(scope, group, node)]-set(ids))})
    return result + _point_rows(rows, dataset, policy, reviewed, all_rows)


def _point_rows(rows, dataset, policy, reviewed, all_rows):
    groups = defaultdict(list)
    context = defaultdict(set)
    for row in all_rows:
        if row['selected'] and row['status'] == '已评分':
            classroom = dataset['students'].get(row['student_id'], {}).get('模拟班级编号', 'UNKNOWN')
            for point in dataset['question_points'].get(row['question_id'], {}):
                for scope, group in [('POINT_NODE', point), ('POINT_CLASS_NODE', json.dumps([classroom, point], ensure_ascii=False))]:
                    key = (scope, group, row['node_id'])
                    context[key].add(row['record_id'])
                    groups[key]
    for row in rows:
        points = row['reviewed_point_scores'] if reviewed and row['review_status'] == 'CONFIRMED' else row['normalized_point_scores']
        limits = dataset['question_points'].get(row['question_id'], {})
        classroom = dataset['students'].get(row['student_id'], {}).get('模拟班级编号', 'UNKNOWN')
        for point, value in points.items():
            if point in limits:
                for scope, group in [('POINT_NODE', point), ('POINT_CLASS_NODE', json.dumps([classroom, point], ensure_ascii=False))]:
                    groups[(scope, group, row['node_id'])].append((value, limits[point], row))
    result = []
    for (scope, group, node), cells in sorted(groups.items()):
        score, maximum = sum(c[0] for c in cells), sum(c[1] for c in cells)
        result.append({'policy': policy, 'scope': scope, 'group_id': group, 'node_id': node,
                       'score': score, 'max_score': maximum, 'score_rate': score/maximum if maximum else None,
                       'events_in_denominator': len(cells), 'scored_events': len(cells),
                       'student_count': len({c[2]['student_id'] for c in cells}),
                       'question_ids': sorted({c[2]['question_id'] for c in cells}),
                       'indicator_ids': sorted({c[2]['indicator_id'] for c in cells}),
                       'evidence_ids': [c[2]['record_id'] for c in cells],
                       'excluded_scored_events': len(context[(scope, group, node)] - {c[2]['record_id'] for c in cells})})
    return result


def _assigned_unanswered(rows, dataset, assignments):
    if assignments is None:
        return None
    if not isinstance(assignments, list):
        raise ValueError('发题确认必须为对象列表')
    confirmed = set()
    for item in assignments:
        if (not isinstance(item, dict) or set(item) != {'student_id', 'node_id', 'question_id'}
                or any(not isinstance(value, str) for value in item.values())):
            raise ValueError('发题确认字段不符')
        key = (item['student_id'], item['node_id'], item['question_id'])
        if key in confirmed or key[0] not in dataset['students'] or key[1] not in dataset['nodes']:
            raise ValueError('发题确认重复或编号无效')
        if key[2] not in dataset['nodes'][key[1]]['可用题目编号'].split('、'):
            raise ValueError('发题确认题目超出已知节点范围')
        confirmed.add(key)
    return [{**row, 'normalized_score': 0} for row in rows
            if row['selected'] and row['status'] == '未作答' and not row['main_error_codes']
            and (row['student_id'], row['node_id'], row['question_id']) in confirmed]


def _participation(rows, dataset):
    groups = defaultdict(list)
    for row in rows:
        if row['selected']:
            groups[(row['student_id'], row['node_id'])].append(row)
    result = []
    for student in dataset['students']:
        for node in dataset['nodes']:
            values = groups[(student, node)]
            states = Counter(row['status'] for row in values)
            originals = [row for row in rows if row['student_id'] == student and row['node_id'] == node]
            conflicts = {row['event_id'] for row in originals} - {row['event_id'] for row in values}
            result.append({'student_id': student, 'node_id': node, 'observed_events': len(values) + len(conflicts),
                           'scored': states['已评分'], 'pending': states['待评分'], 'unanswered': states['未作答'],
                           'conflicting_events': len(conflicts),
                           'out_of_node_events': sum(row['diagnosis_scope'] == 'OUT_OF_NODE' for row in values),
                           'no_observed_records': not bool(originals), 'reason_for_no_record': 'UNKNOWN' if not originals else None,
                           'expected_assigned_count': None, 'response_rate': None,
                           'evidence_ids': [row['record_id'] for row in values],
                           'conflict_evidence_ids': [row['record_id'] for row in originals if row['event_id'] in conflicts]})
    return result


def _comparability(rows, dataset):
    groups = defaultdict(list)
    for row in rows:
        if row['score_eligible']:
            classroom = dataset['students'][row['student_id']]['模拟班级编号']
            groups[(classroom, row['node_id'])].append(row)
    result = []
    for left, right in itertools.combinations(sorted(groups), 2):
        a, b = groups[left], groups[right]
        qa, qb = {r['question_id'] for r in a}, {r['question_id'] for r in b}
        shared = sorted(qa & qb)
        students_a, students_b = {r['student_id'] for r in a}, {r['student_id'] for r in b}
        indicators_a, indicators_b = {r['indicator_id'] for r in a}, {r['indicator_id'] for r in b}
        result.append({'left_class': left[0], 'left_node': left[1], 'right_class': right[0], 'right_node': right[1],
                       'same_question_set': qa == qb, 'shared_questions': shared,
                       'left_question_count': len(qa), 'right_question_count': len(qb),
                       'left_events': len(a), 'right_events': len(b),
                       'same_students': students_a == students_b,
                       'left_student_count': len(students_a), 'right_student_count': len(students_b),
                       'shared_student_count': len(students_a & students_b),
                       'left_indicator_ids': sorted(indicators_a), 'right_indicator_ids': sorted(indicators_b),
                       'same_indicator_set': indicators_a == indicators_b,
                       'shared_question_rubrics_identical': True,
                       'rubric_basis': '本批同题使用同一题目主表及评分点定义',
                       'left_excluded_scored_events': sum(r['selected'] and r['status'] == '已评分' and not r['score_eligible']
                            and r['node_id'] == left[1] and dataset['students'].get(r['student_id'], {}).get('模拟班级编号') == left[0] for r in rows),
                       'right_excluded_scored_events': sum(r['selected'] and r['status'] == '已评分' and not r['score_eligible']
                            and r['node_id'] == right[1] and dataset['students'].get(r['student_id'], {}).get('模拟班级编号') == right[0] for r in rows),
                       'shared_question_point_ids': sorted({point for q in shared for point in dataset['question_points'][q]}),
                       'can_describe_shared_questions': bool(shared), 'can_infer_ability_rank_or_growth': False,
                       'limitations': '题目覆盖、样本及分母见同题/同点统计；仅作描述，不推断能力排名或教学因果。'})
    return result


def make_views(rows, dataset, assignments):
    current = [row for row in rows if row['selected']]
    main = [row for row in rows if row['score_eligible']]
    confirmed = [row for row in current if row['status'] == '已评分' and not (set(row['main_error_codes'])-SCORING_ERRORS)
                 and (row['score_eligible'] or row['review_status'] == 'CONFIRMED')]
    policy_rows = {
        'MAIN_OFFICIAL_MAPPED': main,
        'ASSOCIATION_STRICT': [row for row in rows if row['association_strict_eligible']],
        'RAW_LATEST_SCORED_UNSAFE_AUDIT_ONLY': [row for row in current if row['status'] == '已评分'
                                               and row['normalized_score'] is not None and row['normalized_max_score']],
        'RAW_IN_SCOPE_SCORED_UNSAFE_AUDIT_ONLY': [row for row in current if row['status'] == '已评分'
                                                 and row['normalized_score'] is not None and row['normalized_max_score']
                                                 and row['diagnosis_scope'] == 'IN_NODE'],
        'CONFIRMED_REVIEW': confirmed,
    }
    sensitivity = []
    for policy, values in policy_rows.items():
        # Raw unsafe views use total-only arithmetic; invalid point values are never aggregated as valid points.
        score = sum(row['reviewed_score'] if policy == 'CONFIRMED_REVIEW' and row['review_status'] == 'CONFIRMED'
                    else row['normalized_score'] for row in values)
        maximum = sum(row['normalized_max_score'] for row in values)
        sensitivity.append({'policy': policy, 'available': True, 'score': score, 'max_score': maximum,
                            'score_rate': score/maximum if maximum else None, 'effective_events': len(values),
                            'record_ids': [row['record_id'] for row in values],
                            'excluded_scored_events': sum(row['status'] == '已评分' for row in current)-len(values)})
    unanswered = _assigned_unanswered(rows, dataset, assignments)
    optional = main + unanswered if unanswered is not None else None
    sensitivity.append({'policy': 'ASSIGNED_UNANSWERED_ZERO', 'available': optional is not None,
                        'score': sum(row['normalized_score'] for row in optional) if optional is not None else None,
                        'max_score': sum(row['normalized_max_score'] for row in optional) if optional is not None else None,
                        'score_rate': sum(row['normalized_score'] for row in optional)/sum(row['normalized_max_score'] for row in optional)
                        if optional and sum(row['normalized_max_score'] for row in optional) else None,
                        'effective_events': len(optional) if optional is not None else None,
                        'record_ids': [row['record_id'] for row in optional] if optional is not None else [],
                        'reason': '已确认发题的明确未作答另按0分' if optional is not None else '未提供发题确认清单，不启用该口径'})
    main_ids = {row['record_id'] for row in main}
    main_score = sum(row['normalized_score'] for row in main)
    main_maximum = sum(row['normalized_max_score'] for row in main)
    sensitivity = [{**view,
                    'score_delta_vs_main': view['score'] - main_score if view['available'] else None,
                    'max_score_delta_vs_main': view['max_score'] - main_maximum if view['available'] else None,
                    'events_delta_vs_main': view['effective_events'] - len(main) if view['available'] else None,
                    'added_record_ids_vs_main': sorted(set(view['record_ids']) - main_ids) if view['available'] else [],
                    'excluded_record_ids_vs_main': sorted(main_ids - set(view['record_ids'])) if view['available'] else []}
                   for view in sensitivity]
    diagnosis = [{key: row[key] for key in ('record_id', 'event_id', 'revision', 'selected', 'status',
                                         '学生最终作答', '解答过程', 'official_score', 'official_point_scores',
                                         'score_eligible', 'diagnosis_scope', 'issue_codes', 'review_reasons')}
                 for row in rows if row['diagnosis_available']]
    quality = [{'code': code, 'record_count': len(values),
                'current_event_count': sum(row['selected'] for row in values),
                'student_count': len({row['student_id'] for row in values}),
                'denominator_raw_records': len(rows), 'denominator_current_events': len(current),
                'record_ids': [row['record_id'] for row in values],
                'excluded_current_scored': sum(row['selected'] and row['status'] == '已评分'
                                               and not row['score_eligible'] for row in values)}
               for code in sorted({code for row in rows for code in row['issue_codes']})
               for values in [[row for row in rows if code in row['issue_codes']]]]
    return {'scores': score_rows(main, dataset, 'MAIN_OFFICIAL_MAPPED', all_rows=rows),
            'confirmed_scores': score_rows(confirmed, dataset, 'CONFIRMED_REVIEW', all_rows=rows, reviewed=True),
            'participation': _participation(rows, dataset), 'diagnosis': diagnosis,
            'comparability': _comparability(rows, dataset), 'quality': quality, 'sensitivity': sensitivity}
