"""Course relation projections and links derived from observed operations."""
from collections import defaultdict
from itertools import combinations


MAPPING_VERSION = 'Knowledge-Mapping-1'
STEP_MAPPING_VERSION = 'Step-Operation-Mapping-1'


def course_link(question_id, node_id, source, *, official, rubric_id=None):
    kind = 'RUBRIC_NODE' if rubric_id else 'QUESTION_NODE'
    origin = rubric_id or question_id
    return {'relation_id': f'{kind}:{origin}:{node_id}', 'relation_kind': kind,
            'relation_type': 'SUPPORTING' if rubric_id else 'DIRECT_ASSESSED',
            'from_id': origin, 'to_id': node_id, 'question_id': question_id, 'node_id': node_id,
            **({'rubric_id': rubric_id} if rubric_id else {}),
            'mapping_method': 'QUESTION_INDICATOR_CONTEXT' if rubric_id else 'SOURCE_INDICATOR_FIELD',
            'review_status': 'OFFICIAL' if official and not rubric_id else 'AI_CANDIDATE',
            'mapping_version': MAPPING_VERSION, 'evidence_refs': list(dict.fromkeys((question_id, origin, node_id))),
            'source_ref': source}


def joint_assessment_edges(question_links):
    """Project only two active nodes actually attached to the same question."""
    grouped, edges = defaultdict(dict), {}
    for link in question_links:
        if link['review_status'] not in {'UNRESOLVED', 'REJECTED', 'DISPUTED'} and link['relation_type'] == 'DIRECT_ASSESSED':
            grouped[link['question_id']][link['node_id']] = link
    for question, by_node in sorted(grouped.items()):
        for start, end in combinations(sorted(by_node), 2):
            edge = edges.setdefault((start, end), {'from_id': start, 'to_id': end, 'relation_type': 'COMPOSITE',
                                                   'mapping_method': 'SHARED_QUESTION', 'mapping_version': MAPPING_VERSION,
                                                   'review_status': 'AI_CANDIDATE', 'question_ids': [], 'evidence_refs': []})
            edge['question_ids'].append(question)
            edge['evidence_refs'] = list(dict.fromkeys([*edge['evidence_refs'], *by_node[start]['evidence_refs'],
                                                       *by_node[end]['evidence_refs']]))
    return list(edges.values())


def recommended_practice_edges(approved_practices):
    edges = defaultdict(list)
    for practice in approved_practices.values():
        for pair in combinations(sorted(set(practice['knowledge_refs'])), 2):
            edges[pair].append(practice['practice_id'])
    return [
        {'from_id':start,'to_id':end,'relation_type':'RECOMMENDED',
         'mapping_method':'SHARED_APPROVED_PRACTICE','mapping_version':MAPPING_VERSION,
         'review_status':'DERIVED_FROM_APPROVED_PRACTICE','question_ids':sorted(refs),
         'evidence_refs':sorted(refs)}
        for (start,end),refs in sorted(edges.items())
    ]


def step_links(steps, points, rubric_node_links):
    by_rubric, by_step, node_pairs = defaultdict(list), defaultdict(list), {}
    for link in rubric_node_links:
        if link['review_status'] not in {'UNRESOLVED', 'REJECTED', 'DISPUTED'}:
            by_rubric[link['rubric_id']].append(link)
    rubric_links = []
    for point in points:
        step_id = point.get('step_id')
        if step_id is None:
            continue
        rubric_id = point['rubric_id']
        evidence = point['evidence_refs']
        link = {'step_id': step_id, 'rubric_id': rubric_id, 'operation_type': point['operation_type'],
                'operation_match': point['operation_match'], 'evidence_refs': evidence,
                'review_status': 'AI_CANDIDATE', 'mapping_version': STEP_MAPPING_VERSION}
        rubric_links.append(link)
        by_step[step_id].append(link)
        for parent in by_rubric[rubric_id]:
            node_id = parent['node_id']
            pair = node_pairs.setdefault((step_id, node_id), {
                'step_id': step_id, 'node_id': node_id, 'evidence_role': 'OBSERVED_RUBRIC_OPERATION',
                'evidence_refs': [], 'rubric_ids': [], 'review_status': 'AI_CANDIDATE',
                'mapping_version': STEP_MAPPING_VERSION})
            pair['evidence_refs'] = list(dict.fromkeys([*pair['evidence_refs'], *evidence, node_id]))
            pair['rubric_ids'].append(rubric_id)
    alignment = []
    for step in steps:
        identity = step['step_id']
        matches = by_step[identity]
        operations = list(dict.fromkeys(match['operation_type'] for match in matches))
        alignment.append({'step_id': identity, 'alignment_status': 'ALIGNED' if matches else 'UNRESOLVED',
                          'operation_types': operations, 'rubric_ids': [match['rubric_id'] for match in matches],
                          'review_status': 'AI_CANDIDATE' if matches else 'UNRESOLVED',
                          'evidence_refs': list(dict.fromkeys([identity, *(item for match in matches
                                                                          for item in match['evidence_refs'])])),
                          'mapping_version': STEP_MAPPING_VERSION})
        step['operation_type'] = operations[0] if len(operations) == 1 else None
    return {'step_rubric_links': rubric_links, 'step_node_links': list(node_pairs.values()), 'step_alignment': alignment}
