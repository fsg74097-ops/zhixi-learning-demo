"""G2 独立请求协议、严格响应校验及工具优先仲裁。无网络调用。"""
import json

from step_diagnosis import validate_case, _student_lines, diagnose_case, SYSTEM_RULE


PROMPT_VERSION = 'DS-BLIND-2026-11-15.1'
VALIDITIES = frozenset({'VALID', 'INVALID', 'UNRESOLVED'})
ROLES = frozenset({'NONE', 'FIRST_ERROR', 'PROPAGATION', 'INDEPENDENT_ERROR',
                   'RETURN_TO_CORRECT_RESULT', 'UNRESOLVED'})
STEP_FIELDS = frozenset({'step_id', 'observation', 'local_validity', 'original_validity',
                         'error_role', 'math_basis', 'preconditions', 'evidence_refs',
                         'evidence_quotes', 'uncertainties'})
OUTPUT_EXAMPLE = {
    'case_id': '复制输入case_id', 'steps': [{
        'step_id': 'S1', 'observation': '可核对的观察', 'local_validity': 'UNRESOLVED',
        'original_validity': 'UNRESOLVED', 'error_role': 'UNRESOLVED',
        'math_basis': '规则及适用条件的可检查论证', 'preconditions': ['适用条件'],
        'evidence_refs': ['输入证据ID'], 'evidence_quotes': [{'id': '输入证据ID', 'quote': '逐字摘录'}],
        'uncertainties': ['未知项']}], 'first_error_step': None,
    'uncertainties': [], 'suggestions': [{'text': '仅供复核的建议候选', 'evidence_refs': ['输入证据ID']}],
}


def build_messages(value, role):
    validated = validate_case(value)
    if role not in ('generator', 'reviewer'):
        raise ValueError('仅允许DeepSeek主诊断或独立盲审任务')
    original = validated.as_dict()
    evidence = [{key: item[key] for key in ('id', 'kind', 'text')} for item in original['evidence']]
    payload = {**{key: item for key, item in original.items() if key != 'evidence'},
               'evidence': evidence,
               'step_catalog': [{'step_id': f'S{index}', **item}
                                for index, item in enumerate(_student_lines(original), 1)]}
    task = '独立提出数学诊断。' if role == 'generator' else '独立审核原始作答，自行定位问题。'
    rules = ('按step_catalog原顺序逐步分析，不修改原分和原文。local_validity比较相邻行，'
             'original_validity比较原题。结果恢复正确但相邻不等价使用RETURN_TO_CORRECT_RESULT，'
             '不据此断言主动纠错。未知过程用UNRESOLVED，不将后续合法传播当新增错误。'
             '首错与FIRST_ERROR标签一致。输出每步非空数学依据和逐字证据摘录，引用仅限输入ID。'
             '仅返回以下字段的JSON，不添加字段，不输出内部完整思维链。')
    system = f'{PROMPT_VERSION}\n{SYSTEM_RULE}\n{task}{rules}\nJSON格式：'
    return [{'role': 'system', 'content': system + json.dumps(OUTPUT_EXAMPLE, ensure_ascii=False)},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]


def _object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('JSON字段重复')
    return result


def _bad_constant(value):
    raise ValueError('JSON包含非有限常量')


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError('文本字段缺失、过长或类型错误')


def _texts(values, maximum=12):
    if not isinstance(values, list) or len(values) > maximum:
        raise ValueError('列表字段类型错误或过长')
    for item in values:
        _text(item)


def _refs(values, allowed):
    _texts(values, 64)
    if not values or len(set(values)) != len(values) or not set(values) <= allowed:
        raise ValueError('证据引用为空、重复或超出允许集合')


def _check_step(step, step_id, evidence):
    if not isinstance(step, dict) or set(step) != STEP_FIELDS or step['step_id'] != step_id:
        raise ValueError('步骤字段或ID顺序错误')
    if step['local_validity'] not in VALIDITIES or step['original_validity'] not in VALIDITIES:
        raise ValueError('有效性标签无效')
    if step['error_role'] not in ROLES:
        raise ValueError('错误分类标签无效')
    pairs = {'NONE': ('VALID', 'VALID'), 'FIRST_ERROR': ('INVALID', 'INVALID'),
             'PROPAGATION': ('VALID', 'INVALID'), 'INDEPENDENT_ERROR': ('INVALID', 'INVALID'),
             'RETURN_TO_CORRECT_RESULT': ('INVALID', 'VALID')}
    if step['error_role'] in pairs and (step['local_validity'], step['original_validity']) != pairs[step['error_role']]:
        raise ValueError('错误分类与局部/原题有效性标签矛盾')
    _text(step['observation'])
    _text(step['math_basis'])
    _texts(step['preconditions'])
    _texts(step['uncertainties'])
    _refs(step['evidence_refs'], set(evidence))
    quotes = step['evidence_quotes']
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= 12:
        raise ValueError('步骤需要有限数量的逐字证据摘录')
    for item in quotes:
        if not isinstance(item, dict) or set(item) != {'id', 'quote'}:
            raise ValueError('证据摘录结构无效')
        _text(item['quote'])
        if item['id'] not in step['evidence_refs'] or item['quote'] not in evidence[item['id']]:
            raise ValueError('证据摘录与原文不符')


def _check_timeline(steps):
    seen_first, unknown, previous = False, False, None
    for step in steps:
        role = step['error_role']
        if role == 'FIRST_ERROR' and unknown:
            raise ValueError('前序未知时不能确定最早错误')
        if role == 'RETURN_TO_CORRECT_RESULT' and (not seen_first or unknown):
            raise ValueError('结果恢复分类缺少明确前序证据')
        if role == 'INDEPENDENT_ERROR' and not seen_first:
            raise ValueError('新增错误分类缺少已知首错')
        if role == 'PROPAGATION' and previous not in ('FIRST_ERROR', 'INDEPENDENT_ERROR', 'PROPAGATION'):
            raise ValueError('传播分类缺少连续的已知错误来源')
        seen_first = seen_first or role == 'FIRST_ERROR'
        unknown = unknown or role == 'UNRESOLVED'
        previous = role


def _validate_claim(claim, value):
    fields = {'case_id', 'steps', 'first_error_step', 'uncertainties', 'suggestions'}
    if not isinstance(claim, dict) or set(claim) != fields or claim['case_id'] != value['case_id']:
        raise ValueError('响应根字段或案例ID错误')
    expected_steps = _student_lines(value)
    steps = claim['steps']
    if not isinstance(steps, list) or len(steps) != len(expected_steps):
        raise ValueError('步骤数量不匹配')
    evidence = {item['id']: item['text'] for item in value['evidence']}
    for index, step in enumerate(steps, 1):
        _check_step(step, f'S{index}', evidence)
    _check_timeline(steps)
    first_steps = [step['step_id'] for step in steps if step['error_role'] == 'FIRST_ERROR']
    expected_first = [] if claim['first_error_step'] is None else [claim['first_error_step']]
    if first_steps != expected_first or len(first_steps) > 1:
        raise ValueError('首错编号与步骤标签不一致')
    _texts(claim['uncertainties'])
    if not isinstance(claim['suggestions'], list) or len(claim['suggestions']) > 4:
        raise ValueError('建议候选数量无效')
    for item in claim['suggestions']:
        if not isinstance(item, dict) or set(item) != {'text', 'evidence_refs'}:
            raise ValueError('建议候选字段无效')
        _text(item['text'])
        _refs(item['evidence_refs'], set(evidence))


def parse_claim(raw, value):
    validate_case(value)
    try:
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 65536:
            raise ValueError('响应为空、过长或不是文本')
        claim = json.loads(raw, object_pairs_hook=_object, parse_constant=_bad_constant)
        _validate_claim(claim, value)
    except (ValueError, TypeError, KeyError, RecursionError):
        return {'status': 'INVALID_RESPONSE', 'raw_text': raw if isinstance(raw, str) and len(raw) <= 65536 else None,
                'parsed': None, 'error': 'JSON结构、字段、步骤或引用校验失败',
                'quote_presence_validated': False, 'semantic_support': 'NOT_ESTABLISHED'}
    return {'status': 'OK', 'raw_text': raw, 'parsed': claim, 'error': None,
            'quote_presence_validated': True, 'semantic_support': 'REQUIRES_HUMAN_REVIEW'}


def _model_step(response, index):
    return response['parsed']['steps'][index] if response.get('status') == 'OK' else None


def _facts(step):
    return {field: step[field] for field in ('local_validity', 'original_validity', 'error_role')}


def _arbitrate_step(tool, proposed, reviewed, is_mock):
    facts = _facts(tool)
    common = {'step_id': tool['step_id'], 'tool': facts,
              'generator': _facts(proposed) if proposed else None,
              'reviewer': _facts(reviewed) if reviewed else None}
    known = {key: val for key, val in facts.items() if val != 'UNRESOLVED'}
    conflict = any(item is not None and any(item[k] not in (v, 'UNRESOLVED') for k, v in known.items())
                   for item in (proposed, reviewed))
    if conflict or tool['verification_level'] == 'FORMAL_VERIFIED':
        return {**common, 'status': 'MODEL_TOOL_CONFLICT' if conflict else 'TOOL_CHECKED',
                'verification_level': 'FORMAL_VERIFIED', 'verified_fields': list(known),
                'published_conclusion': facts, 'release_status': 'TOOL_FACTS',
                'requires_human_review': conflict}
    if not proposed or not reviewed:
        status = 'MODEL_FAILURE'
    elif _facts(proposed) != _facts(reviewed):
        status = 'MODEL_DISAGREEMENT'
    elif 'UNRESOLVED' in _facts(proposed).values():
        status = 'MODEL_UNRESOLVED'
    elif is_mock:
        status = 'MOCK_AGREEMENT'
    else:
        return {**common, 'status': 'MODEL_AGREEMENT', 'verification_level': 'CROSS_CHECKED',
                'verified_fields': list(known), 'published_conclusion': {**_facts(proposed), **known},
                'release_status': 'CANDIDATE_REQUIRES_HUMAN', 'requires_human_review': True}
    return {**common, 'status': status, 'verification_level': 'FORMAL_VERIFIED' if known else 'UNRESOLVED',
            'verified_fields': list(known), 'published_conclusion': facts if known else None,
            'release_status': 'PARTIAL_TOOL_FACTS' if known else 'AWAITING_HUMAN', 'requires_human_review': True}


def arbitrate(value, tool, generator, reviewer):
    if tool['input'] != value:
        raise ValueError('数学工具结果与当前原始案例不一致')
    is_mock = generator.get('is_mock', True) or reviewer.get('is_mock', True)
    steps = [_arbitrate_step(step, _model_step(generator, index), _model_step(reviewer, index), is_mock)
             for index, step in enumerate(tool['steps'])]
    return {'independence_scope': 'SAME_PROVIDER_SEPARATE_REQUESTS', 'provider': 'DeepSeek',
            'first_error_step_from_tool': tool['first_error_step'], 'steps': steps,
            'semantic_support': '引用ID与逐字摘录存在性已检查，解释相关性和教学适切性仍需人工复核。',
            'model_agreement_is_proof': False, 'knowledge_mastery_inference': None}


def _safe_call(sender, value, role):
    try:
        result = sender(validate_case(value).as_dict(), role)
        if not isinstance(result, dict) or result.get('status') not in ('OK', 'INVALID_RESPONSE', 'CALL_FAILED'):
            raise ValueError('接口返回结构不符')
        if result['status'] == 'OK':
            checked = parse_claim(result.get('raw_text'), value)
            if checked['status'] != 'OK':
                return {**result, **checked}
            if result.get('parsed') != checked['parsed']:
                raise ValueError('接口的原文与解析对象不一致')
        return result
    except Exception:
        return {'status': 'CALL_FAILED', 'raw_text': None, 'parsed': None,
                'error': '接口失败或超时，未重试', 'is_mock': True}


def run_blind_case(value, sender):
    validate_case(value)
    generator = _safe_call(sender, value, 'generator')
    if generator['status'] != 'OK':
        reviewer = {'status': 'NOT_RUN', 'parsed': None, 'raw_text': None, 'is_mock': True,
                    'error': '前一路失败，按停止条件结束该案例'}
    else:
        reviewer = _safe_call(sender, value, 'reviewer')
    tool = diagnose_case(value)
    return {'case_id': value['case_id'], 'prompt_version': PROMPT_VERSION,
            'generator': generator, 'reviewer': reviewer, 'tool': tool,
            'arbitration': arbitrate(value, tool, generator, reviewer)}
