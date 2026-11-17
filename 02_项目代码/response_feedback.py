"""Deterministic feedback for explicit, supported one-variable math questions."""
import re

from step_math import compare_steps


def response_feedback(question, reference, answer, process):
    result = {
        'engine': 'DETERMINISTIC_G1',
        'answer_validity': 'UNRESOLVED',
        'first_error_step': None,
        'process_answer_conflict': False,
        'steps': [],
        'next_action': '这道题超出当前自动核验范围，请保留过程交教师核对。',
        'limitations': ['核验范围为有理系数一元一次式、方程和单个不等式。',
                        '反馈只针对本次书面作答，评分按题目评分标准由教师复核。'],
    }
    match = re.fullmatch(r'\s*(?:解不等式|解方程|化简)[:：]\s*(.+?)\s*[。]?\s*', question)
    if not match:
        return result
    initial = match[1].rstrip('。').strip()
    reference_check = compare_steps(initial, reference)
    if reference_check['local_validity'] != 'VALID':
        result['next_action'] = '题意与参考答案尚未通过数学核验，请先由教师核对题目和参考答案。'
        return result
    lines = [line.strip() for line in process.replace('\\n', '\n').splitlines() if line.strip()]
    if len(lines) > 32:
        result['next_action'] = '过程超过32行，请分段交教师核对。'
        return result
    result['answer_validity'] = compare_steps(initial, answer)['local_validity']
    previous, earlier_known = initial, True
    for index, line in enumerate(lines, 1):
        checked = compare_steps(previous, line)
        validity = checked['local_validity']
        if validity == 'INVALID' and earlier_known and result['first_error_step'] is None:
            result['first_error_step'] = index
        result['steps'].append({'step': index, 'source': previous, 'target': line, **checked})
        earlier_known = earlier_known and validity != 'UNRESOLVED'
        previous = line
    result['process_answer_conflict'] = bool(lines) and compare_steps(lines[-1], answer)['local_validity'] == 'INVALID'
    wrong_step = next((step for step in result['steps'] if step['local_validity'] == 'INVALID'), None)
    if wrong_step:
        transition = f'“{wrong_step["source"]} → {wrong_step["target"]}”'
        if wrong_step['rule']['id'] == 'NEGATIVE_SCALING':
            result['next_action'] = f'请重新检查{transition}。两边同除以负数时要改变不等号方向，再选取边界两侧的数代回原题检验。'
        else:
            result['next_action'] = f'请回到{transition}，逐项检查移项、去括号或合并同类项的依据，再从原题重新推导。'
    elif result['process_answer_conflict']:
        result['next_action'] = f'请核对过程末行“{lines[-1]}”与最终填写“{answer}”，这两个数学式不等价；先依据原题检查，再统一最终作答。'
    elif result['answer_validity'] == 'VALID':
        result['next_action'] = '本次最终结果与原题数学等价。请说明关键变形依据，并用代入或逆向运算自检。'
    elif result['answer_validity'] == 'INVALID':
        result['next_action'] = '最终结果与原题不等价。请从原题重新推导，逐步写出运算依据，再检查最终填写。'
    if any(step['local_validity'] == 'UNRESOLVED' for step in result['steps']):
        result['limitations'].append('部分过程无法解析，需要教师核对，首错与错误传播关系保留未决。')
    return result
