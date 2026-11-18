"""Deterministic rubric checks for a versioned, source-supported subset."""
import re

from step_math import clean_expression, compare_steps


RULE_VERSION = 'Rubric-Math-1'
# An authored semantic extraction is used only for the exact source question and
# rubric wording. The reference and the student's result are checked separately.
FINAL_ONLY = {
    'Q004': ('化简5x-2x+2x-4。', '5x-2x+2x-4', 'EXPRESSION', 'x'),
    'Q005': ('去括号：3(a+2)=？', '3(a+2)', 'EXPRESSION', 'a'),
    'Q010': ('去括号：-2(2a-3)=？', '-2(2a-3)', 'EXPRESSION', 'a'),
    'Q012': ('化简3x+2-5x+7。', '3x+2-5x+7', 'EXPRESSION', 'x'),
    'Q019': ('解方程x+7=12，x=？', 'x+7=12', 'SCALAR', 'x'),
    'Q020': ('解方程3x=18，x=？', '3x=18', 'SCALAR', 'x'),
    'Q023': ('解方程2x-5=9，x=？', '2x-5=9', 'SCALAR', 'x'),
    'Q024': ('解方程x/3+2=5，x=？', 'x/3+2=5', 'SCALAR', 'x'),
    'Q037': ('解不等式x+4>9，写出解集。', 'x+4>9', 'INEQUALITY', 'x'),
    'Q038': ('解不等式2x≤10，写出解集。', '2x<=10', 'INEQUALITY', 'x'),
    'Q039': ('解不等式x-3<2，写出解集。', 'x-3<2', 'INEQUALITY', 'x'),
    'Q041': ('解不等式-2x>6，写出解集。', '-2x>6', 'INEQUALITY', 'x'),
}
Q009 = ('', '', {})
Q043 = ('', '', {})
NUM = r'[+-]?(?:\d+(?:\.\d+)?|\d+/[1-9]\d*)'


def _normalize(text):
    return re.sub(r'\s+', '', clean_expression(text))


def _step_text(text, expression):
    text = re.sub(r'^解[:：]', '', _normalize(text))
    return re.sub(r'^(?:原式)?=', '', text) if expression else text


def _answer_form(text, kind, variable):
    value = _normalize(text)
    if kind == 'SCALAR':
        value = value.removeprefix(variable + '=')
        return f'{variable}={value}' if re.fullmatch(NUM, value) else None
    if kind == 'INEQUALITY':
        inequality = r'(?:<=|>=|<|>)'
        shape = rf'(?:{variable}{inequality}{NUM}|{NUM}{inequality}{variable})'
        return value if re.fullmatch(shape, value) else None
    shape = rf'(?:{NUM}|[+-]?(?:\d+)?{variable}(?:[+-]\d+)?)'
    return value if re.fullmatch(shape, value) else None


def _write_check(checks, rubric_id, fulfilled, operation, *, step_id=None, prior_id=None):
    checks[rubric_id] = {'operation_match': 'FULFILLED' if fulfilled else 'CONTRADICTED',
                         'operation_type': operation, 'step_id': step_id, 'prior_id': prior_id}


def _transitions(initial, steps, *, expression=False):
    prior, prior_id = initial, None
    for step in steps:
        value = _step_text(step['step_text'], expression)
        yield prior, value, prior_id, step['step_id'], compare_steps(prior, value)
        prior, prior_id = value, step['step_id']


def _check_official_steps(identity, initial, steps, checks):
    for source, target, prior, step, math in _transitions(initial, steps, expression=identity == 'Q009'):
        valid = math['local_validity'] == 'VALID'
        if identity == 'Q009':
            expanded = '6x-2-x-4'
            grouped = '(6x-x)+(-2-4)'
            if source == initial and target == expanded and valid:
                _write_check(checks, 'Q009P1', True, 'EXPAND_BRACKETS', step_id=step, prior_id=prior)
            if source in {expanded, grouped} and target == '5x-6' and valid:
                _write_check(checks, 'Q009P2', True, 'COLLECT_LIKE_TERMS', step_id=step, prior_id=prior)
        else:
            if source == initial and target in {'-2x<11-3', '-2x<8'} and valid:
                _write_check(checks, 'Q043P1', True, 'TRANSPOSE_CONSTANT', step_id=step, prior_id=prior)
            if source in {'-2x<8', '-2x<11-3'} and target in {'x>-4', 'x<-4'}:
                if math['rule']['id'] == 'NEGATIVE_SCALING':
                    _write_check(checks, 'Q043P2', valid, 'NEGATIVE_DIVISION', step_id=step, prior_id=prior)


def _check_demo_steps(question, initial, steps, answer_validity, checks):
    expression = question['exact_text'].lstrip().startswith('化简')
    for source, target, prior, step, math in _transitions(initial, steps, expression=expression):
        valid = math['local_validity'] == 'VALID'
        # Only infer this operation from a numeric bound and an isolated
        # unknown. A sign change in the G1 normal form alone can be a jump.
        negative_division = (
            math['rule']['id'] == 'NEGATIVE_SCALING'
            and re.fullmatch(rf'-\d+x(?:<=|>=|<|>){NUM}', source)
            and _answer_form(target, 'INEQUALITY', 'x') and target.startswith('x'))
        for point in question['rubric_points']:
            point_id, text = point['rubric_id'], point['exact_text']
            if negative_division and text in {'同除以负数并计算边界', '正确改变不等号方向', '同除负数并改变方向'}:
                _write_check(checks, point_id, text == '同除以负数并计算边界' or valid,
                             'NEGATIVE_DIVISION', step_id=step, prior_id=prior)
            if not valid:
                continue
            if text.startswith('移项得到') and source != target and target == _normalize(text.removeprefix('移项得到')):
                _write_check(checks, point_id, True, 'TRANSPOSE_CONSTANT', step_id=step, prior_id=prior)
            if (text in {'识别同类项', '正确合并同类项'} and source.count('x') >= 2 and target.count('x') == 1
                    and not any(char in source + target for char in '()')):
                _write_check(checks, point_id, True, 'COLLECT_LIKE_TERMS', step_id=step, prior_id=prior)
            if (text == '正确去括号' and '(' in source and '(' not in target and source.count('x') == target.count('x')):
                _write_check(checks, point_id, True, 'EXPAND_BRACKETS', step_id=step, prior_id=prior)
    for point in question['rubric_points']:
        if point['exact_text'] == '正确填写最终化简结果' and answer_validity != 'UNRESOLVED':
            _write_check(checks, point['rubric_id'], answer_validity == 'VALID', 'FINAL_ANSWER')


def verify_rubric(question, row, steps, *, self_created):
    """Use the question, reference and written answer, independent of every score track."""
    identity = question['question_id']
    descriptions = {point['rubric_id']: point['exact_text'] for point in question['rubric_points']}
    unit_rubric = all(point['is_official'] and point['max_score'] == 1 for point in question['rubric_points'])
    initial, final_point, kind, variable = None, None, None, 'x'
    if self_created:
        match = re.fullmatch(r'\s*(解不等式|解方程|化简)[:：]\s*(.+?)\s*[。]?\s*', question['exact_text'])
        if match:
            initial = _normalize(match[2])
            kind = {'解不等式': 'INEQUALITY', '解方程': 'SCALAR', '化简': 'EXPRESSION'}[match[1]]
    elif unit_rubric and identity in FINAL_ONLY:
        stem, original, kind, variable = FINAL_ONLY[identity]
        if question['exact_text'] == stem and descriptions == {identity + 'P1': '答案正确'}:
            initial, final_point = original, identity + 'P1'
    elif unit_rubric and identity in {'Q009', 'Q043'}:
        stem, original, expected = Q009 if identity == 'Q009' else Q043
        if question['exact_text'] == stem and descriptions == expected:
            initial = original
            kind = 'EXPRESSION' if identity == 'Q009' else 'INEQUALITY'
            final_point = 'Q009P3' if identity == 'Q009' else None
    checks, answer_validity = {}, 'UNRESOLVED'
    reason = '此题或评分意图暂无可复算的独立规则。'
    if initial is not None:
        reference = _answer_form(question['reference_answer'], kind, variable)
        if reference is not None and compare_steps(initial, reference, variable)['local_validity'] == 'VALID':
            reason = '未观察到足以确定该评分点的单独操作或核验过程。'
            answer = _answer_form(row['student_answer'], kind, variable)
            if answer is not None:
                answer_validity = compare_steps(initial, answer, variable)['local_validity']
            if final_point is not None and answer_validity != 'UNRESOLVED':
                _write_check(checks, final_point, answer_validity == 'VALID', 'FINAL_ANSWER')
            if len(steps) <= 32:
                if self_created:
                    _check_demo_steps(question, initial, steps, answer_validity, checks)
                elif identity in {'Q009', 'Q043'}:
                    _check_official_steps(identity, initial, steps, checks)
        else:
            reason = '题意和参考答案未通过独立数学核验。'
    points = []
    for point in question['rubric_points']:
        rubric_id = point['rubric_id']
        evidence = [identity, rubric_id]
        check = checks.get(rubric_id)
        if check is None:
            points.append({'rubric_id': rubric_id, 'status': 'UNRESOLVED', 'score': None,
                           'reason': reason, 'evidence_refs': evidence})
            continue
        evidence.extend(item for item in (check['prior_id'], check['step_id']) if item)
        if not check['step_id']:
            evidence.append(row['record_id'])
        points.append({'rubric_id': rubric_id, 'status': 'FORMAL_VERIFIED', 'evidence_refs': evidence,
                       'score': point['max_score'] if check['operation_match'] == 'FULFILLED' else 0, **check})
    full = bool(points) and all(point['status'] == 'FORMAL_VERIFIED' for point in points)
    return {'rule_version': RULE_VERSION, 'status': 'VERIFIED' if full else 'UNRESOLVED',
            'answer_validity': answer_validity, 'points': points,
            'verified_point_scores': {point['rubric_id']: point['score'] for point in points} if full else None}
