"""Pure adapters for the frozen evidence contract; no storage or model calls.

Legacy internal objects remain unchanged. Authority and origin stay in the
internal model; the contract's review enum is deliberately mapped explicitly.
"""
import csv
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from official_data import SCHEMA

CONTRACT_VERSION = 'Knowledge-Evidence-1.1-adapter-1'
SCHEMA_PATH = Path(__file__).with_name('knowledge_schemas') / 'evidence_bundle.schema.json'
REVIEW_STATES = {'OFFICIAL', 'AI_CANDIDATE', 'HUMAN_VERIFIED', 'DISPUTED', 'UNRESOLVED', 'REJECTED'}


def review_state(value, *, self_created=False):
    if value in REVIEW_STATES:
        return value
    if value == 'SOURCE_PROVIDED':
        return 'AI_CANDIDATE' if self_created else 'OFFICIAL'
    if value in {'PROJECT_DEFINED', 'STRUCTURE_CHECKED'}:
        return 'AI_CANDIDATE'
    if value == 'OBSERVATION_ONLY':
        return 'UNRESOLVED'
    raise ValueError('INVALID_REVIEW_STATUS')


def source_contract(value):
    """Preserve exact location and hashes, omit private filesystem/OCR data."""
    identity = value['source_file_id']
    parts = [identity]
    for field in ('row', 'column', 'source_span', 'physical_page', 'printed_page'):
        if value.get(field) is not None:
            parts.append(f'{field}={value[field]}')
    result = {'source_file_id': identity, 'source_hash': value['source_hash'], 'locator': '; '.join(parts)}
    for field in ('source_version', 'physical_page', 'printed_page', 'sheet', 'row'):
        if field in value:
            result[field] = value[field]
    return result


def verification_state(validation):
    if validation.get('schema_validation') is False:
        return 'UNRESOLVED'
    if validation.get('cross_check_status') in {'CONFLICT', 'DISPUTED'}:
        return 'DISPUTED'
    if all(validation.get(key) is True for key in ('schema_validation', 'evidence_validation',
                                                  'math_validation', 'rubric_validation')):
        return 'VERIFIED' if validation.get('cross_check_status') == 'LOCKED_OUTPUTS_MATCH' else 'PARTIAL'
    if validation.get('semantic_status') == 'NOT_RUN':
        return 'NOT_RUN'
    return 'UNRESOLVED' if validation.get('rubric_validation') is False else 'PARTIAL'


def validate_evidence_bundle(value):
    errors = list(Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))).iter_errors(value))
    if errors:
        raise ValueError('SCHEMA_INVALID: ' + '; '.join(error.message for error in errors[:3]))
    if not value['evidence_refs']:
        raise ValueError('EVIDENCE_REQUIRED')
    return value


def evidence_bundle_contract(legacy, *, batch_id, nodes, entities, self_created=False):
    question, response = legacy['question'], legacy['response']
    links = {}
    for link in legacy['knowledge_links']:
        key = (link['to_id'], link['relation_type'])
        if key not in links:
            links[key] = {'node_id': link['to_id'], 'node_name': nodes[link['to_id']]['name'],
                          'relation_type': link['relation_type'],
                          'review_status': review_state(link['review_status'], self_created=self_created), 'evidence_refs': []}
        links[key]['evidence_refs'].append(link['from_id'])
    result = {'schema_version': '1.1', 'bundle_id': legacy['bundle_id'], 'batch_id': batch_id,
              'snapshot_hash': legacy['snapshot_hash'],
              'question': {'question_id': question['question_id'], 'exact_text': question['exact_text'],
                           'source': source_contract(question['source_ref'])},
              'response': {'response_id': 'RESPONSE:' + response['record_id'], 'record_id': response['record_id'],
                           'question_id': response['question_id'], 'student_answer': response['student_answer'],
                           'selected_revision': response['selected_revision'], 'source_hash': response['source_ref']['source_hash']},
              'rubric_points': [{'rubric_id': point['rubric_id'], 'official_description': point['exact_text'],
                                 'is_official': point['is_official'], 'official_points': point['max_score'],
                                 'source': source_contract(point['source_ref'])} for point in legacy['rubric_points']],
              'student_steps': [{'step_id': step['step_id'], 'step_text': step['step_text'],
                                 'source_span': json.dumps(step['source_span'], separators=(',', ':')),
                                 'operation_type': step.get('operation_type'),
                                 'review_status': review_state(step['review_status'], self_created=self_created)}
                                for step in legacy['student_steps']],
              'knowledge_links': list(links.values()),
              'source_passages': [{'passage_id': passage['id'], 'exact_text': passage['exact_text'],
                                   'resource_title': passage['source_ref']['resource_title'],
                                   'content_hash': passage['content_hash'], 'source': source_contract(passage['source_ref']),
                                   'review_status': review_state(passage['review_status'], self_created=self_created)}
                                  for passage in legacy['source_passages']],
              'evidence_refs': [{'evidence_id': identity, 'entity_type': entities[identity]['type'], 'entity_id': identity,
                                 'source_hash': entities[identity]['source_ref']['source_hash'],
                                 'review_status': review_state(entities[identity]['review_status'], self_created=self_created)}
                                for identity in dict.fromkeys(ref['id'] for ref in legacy['evidence_refs'])],
              'verification_state': verification_state(legacy['verification_state'])}
    return validate_evidence_bundle(result)


FIELD_NAMES = {
    'students': 'student_id class_id grade',
    'materials': 'question_id topic_id material_type question_type exact_text options reference_answer reference_explanation max_score rubric_points knowledge_refs source_locator',
    'nodes': 'assessment_id assessment_date topic_ids description question_ids',
    'requirements': 'topic_id name objective node_id definition curriculum_id standard_page_range textbook_page_range',
    'records': 'record_id student_id question_id topic_id assessment_id event_id selected_revision submitted_at status official_score max_score student_answer student_steps official_point_scores',
    'field_definitions': 'source_table source_column source_type definition allowed_format example null_semantics',
}
NULLABLE = {'选项', '得分', '学生最终作答', '解答过程', '评分点得分', '来源或资料文件', '课标整理编号',
            '课标文件页／正文页', '教材正文页', '示例', '为空时', '说明'}


def field_mappings(dataset, *, self_created=False):
    mappings = []
    for table, (name, fields) in SCHEMA.items():
        known = dict(zip(fields.split(), FIELD_NAMES[table].split(), strict=True))
        source = dataset['sources'][name + '.csv']
        with Path(source['path']).open(encoding='utf-8-sig', newline='') as stream:
            columns = csv.DictReader(stream).fieldnames
        for column in columns:
            mappings.append({'source_name': name + '.csv', 'source_column': column,
                             'canonical_field': table + '.' + known.get(column, 'raw.' + column),
                             'nullable': column in NULLABLE, 'source_authority': 'PROJECT_DEFINED' if self_created else 'OFFICIAL',
                             'mapping_rule': '原字段只读保留；内部类型按official_data校验，教材范围与精确页定位分别保存。',
                             'review_status': 'AI_CANDIDATE' if self_created else 'OFFICIAL',
                             'source_hash': source['sha256']})
    return mappings
