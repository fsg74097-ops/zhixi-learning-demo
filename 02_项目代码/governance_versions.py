"""Canonical grading dependencies and immutable invalidation facts.

This module has no database, filesystem, UI, credentials or model side effects.
Callers persist invalidations separately from the original score/teacher event.
"""
from copy import deepcopy
import hashlib
import json

VERSION = 'Governance-Dependencies-1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def dependency_context(*, dataset_hash, rules_version, code_hashes, knowledge_hash,
                       schema_versions=None, prompt_versions=None):
    if (not isinstance(dataset_hash, str) or not dataset_hash or not isinstance(rules_version, str)
            or not rules_version or not isinstance(knowledge_hash, str) or not knowledge_hash
            or not isinstance(code_hashes, dict) or not code_hashes
            or any(not isinstance(key, str) or not key or not isinstance(value, str) or not value
                   for key, value in code_hashes.items())):
        raise ValueError('INVALID_DEPENDENCY_CONTEXT')
    payload = {'dependency_version': VERSION, 'dataset_hash': dataset_hash, 'rules_version': rules_version,
               'code_hashes': deepcopy(code_hashes), 'knowledge_hash': knowledge_hash,
               'schema_versions': deepcopy(schema_versions or {}), 'prompt_versions': deepcopy(prompt_versions or {})}
    return {**payload, 'fingerprint': fingerprint(payload)}


def classify_binding(event, context, record):
    reasons = []
    if event.get('dependency_fingerprint') is None:
        reasons.append('LEGACY_CONTEXT_MISSING')
    elif event['dependency_fingerprint'] != context['fingerprint']:
        reasons.append('DEPENDENCY_CHANGED')
    if event.get('source_dataset_hash') != context['dataset_hash']:
        reasons.append('DATASET_CHANGED')
    if record is None or event.get('record_id') != record['record_id']:
        reasons.append('RECORD_NOT_CURRENT')
    elif event.get('source_revision') != record['selected_revision']:
        reasons.append('REVISION_CHANGED')
    return reasons


def invalidation_record(event_table, event, context, reasons):
    target = event.get('candidate_id') or event.get('decision_audit_id')
    if event_table not in {'score_candidates', 'teacher_decisions'} or not target or not reasons:
        raise ValueError('INVALID_INVALIDATION_TARGET')
    payload = {'event_table': event_table, 'target_event_id': target, 'record_id': event['record_id'],
               'previous_dependency_fingerprint': event.get('dependency_fingerprint'),
               'current_dependency_fingerprint': context['fingerprint'],
               'invalidation_reasons': sorted(set(reasons)), 'status': 'STALE'}
    return {**payload, 'invalidation_id': 'INVALID:' + fingerprint(payload)}
