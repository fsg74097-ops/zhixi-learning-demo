"""G1 限定验证器：精确有理数、一元一次式、实数方程/不等式。"""
import ast
from dataclasses import dataclass
from fractions import Fraction
import re


FLIP = {'<': '>', '>': '<', '<=': '>=', '>=': '<=', '=': '='}
MAX_EXPRESSION = 512


class UnsupportedMath(ValueError):
    """输入或运算超出已实现的安全数学范围。"""


class UndefinedMath(ValueError):
    """已确认的零除等未定义运算。"""


def clean_expression(text):
    value = re.sub(r'^\s*\d+(?:\.\s+|、\s*)', '', text).strip().rstrip('。；;')
    return value.translate(str.maketrans({'−': '-', '×': '*', '÷': '/',
                                         '≤': '<=', '≥': '>=', '（': '(', '）': ')'}))


def _bounded(pair):
    if any(max(abs(item.numerator).bit_length(), item.denominator.bit_length()) > 512
           for item in pair):
        raise UnsupportedMath('有理数计算超过本原型的大小限制')
    return pair


def _affine(node, source, variable):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        literal = ast.get_source_segment(source, node)
        if not re.fullmatch(r'\d{1,24}(?:\.\d{1,24})?', literal or ''):
            raise UnsupportedMath('仅支持有限长度的十进制整数和小数')
        return Fraction(0), Fraction(literal)
    if isinstance(node, ast.Name) and node.id == variable:
        return Fraction(1), Fraction(0)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        a, b = _affine(node.operand, source, variable)
        sign = -1 if isinstance(node.op, ast.USub) else 1
        return sign * a, sign * b
    if not isinstance(node, ast.BinOp):
        raise UnsupportedMath('仅支持单变量一次式；文字、函数和程序调用待人工核对')
    left = _affine(node.left, source, variable)
    right = _affine(node.right, source, variable)
    return _binary(node.op, left, right)


def _binary(operator, left, right):
    a, b = left
    c, d = right
    if isinstance(operator, ast.Add):
        return _bounded((a + c, b + d))
    if isinstance(operator, ast.Sub):
        return _bounded((a - c, b - d))
    if isinstance(operator, ast.Mult):
        if a and c:
            raise UnsupportedMath('出现二次或更高次项，超出一元一次验证范围')
        return _bounded((a * d + b * c, b * d))
    if isinstance(operator, ast.Div):
        if c:
            raise UnsupportedMath('含变量分母需要额外定义域条件，当前未实现')
        if not d:
            raise UndefinedMath('分母为0，运算未定义')
        return _bounded((a / d, b / d))
    raise UnsupportedMath('幂、开根等规则及其条件尚未实现')


def _parse_affine(text, variable):
    prepared = re.sub(r'(?<=[0-9)])\s*(?=[a-zA-Z(])|(?<=[a-zA-Z])\s*(?=\()', '*', text.strip())
    if not prepared or not re.fullmatch(r'[0-9a-zA-Z+*/().\s-]+', prepared):
        raise UnsupportedMath('无法安全解析该数学表达式')
    try:
        tree = ast.parse(prepared, mode='eval')
    except (SyntaxError, RecursionError, ValueError) as error:
        raise UnsupportedMath('数学表达式语法不在支持范围内') from error
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise UnsupportedMath('表达式语法树过大')
    return _affine(tree.body, prepared, variable)


@dataclass(frozen=True)
class MathForm:
    coefficient: Fraction
    constant: Fraction
    operator: str | None

    def signature(self):
        a, b, operator = self.coefficient, self.constant, self.operator
        if operator is None:
            return 'EXPRESSION', a, b
        if not a:
            return ('ALL' if _holds(b, operator) else 'EMPTY',)
        return (operator if a > 0 else FLIP[operator], -b / a)

    def describe(self, variable='x'):
        signature = self.signature()
        if signature[0] == 'EXPRESSION':
            return f'{self.coefficient}*{variable}+({self.constant})'
        if signature[0] == 'ALL':
            return '全体实数'
        if signature[0] == 'EMPTY':
            return '空集'
        return f'{variable}{signature[0]}{signature[1]}'

    def holds(self, value):
        return _holds(self.coefficient * value + self.constant, self.operator)


def _holds(value, operator):
    return {'=': value == 0, '<': value < 0, '>': value > 0,
            '<=': value <= 0, '>=': value >= 0}[operator]


def parse_math(text, variable='x'):
    if not isinstance(text, str) or len(text) > MAX_EXPRESSION:
        raise UnsupportedMath('表达式为空或超过512字符')
    parts = re.split(r'(<=|>=|=|<|>)', clean_expression(text))
    if len(parts) == 1:
        a, b = _parse_affine(parts[0], variable)
        return MathForm(a, b, None)
    if len(parts) != 3:
        raise UnsupportedMath('暂不处理连写等式或复合不等式')
    a, b = _parse_affine(parts[0], variable)
    c, d = _parse_affine(parts[2], variable)
    return MathForm(a - c, b - d, parts[1])


def _counterexample(source, target, variable):
    boundaries = tuple(-form.constant / form.coefficient for form in (source, target)
                       if form.coefficient and form.operator is not None)
    candidates = tuple(value + offset for value in boundaries for offset in (1, -1, 0))
    for value in candidates + (Fraction(0), Fraction(1), Fraction(-1)):
        if source.operator is None:
            left = source.coefficient * value + source.constant
            right = target.coefficient * value + target.constant
            if left != right:
                return {variable: str(value), 'source_value': str(left), 'target_value': str(right)}
        elif source.holds(value) != target.holds(value):
            return {variable: str(value), 'source_holds': source.holds(value),
                    'target_holds': target.holds(value)}
    return None


def _rule(source, target):
    is_inequality = source.operator in ('<', '>', '<=', '>=')
    negative_ratio = source.coefficient * target.coefficient < 0
    same_boundary = source.constant * target.coefficient == target.constant * source.coefficient
    if is_inequality and target.operator in ('<', '>', '<=', '>=') and negative_ratio and same_boundary:
        return {'id': 'NEGATIVE_SCALING',
                'statement': '不等式两边同乘或同除一个负数，必须改变不等号方向。'}
    return {'id': 'LINEAR_EQUIVALENCE',
            'statement': '一次表达式按系数恒等核对；方程或不等式按实数解集等价核对，允许合法跳步。'}


def _unresolved(reason):
    return {'local_validity': 'UNRESOLVED', 'verification_level': 'UNRESOLVED',
            'transformation': 'UNPARSED', 'rule': {'id': 'UNSUPPORTED', 'statement': reason},
            'preconditions': [], 'verification': {'method': 'none', 'reason': reason}}


def compare_steps(source_text, target_text, variable='x'):
    """检查局部等价性。FORMAL_VERIFIED 也可证明某次变换无效。"""
    try:
        source = parse_math(source_text, variable)
    except (UnsupportedMath, UndefinedMath) as error:
        return _unresolved(f'上一行无法作为已定义的受支持数学式：{error}')
    try:
        target = parse_math(target_text, variable)
    except UndefinedMath as error:
        return {'local_validity': 'INVALID', 'verification_level': 'FORMAL_VERIFIED',
                'transformation': 'UNDEFINED_DIVISION',
                'rule': {'id': 'NONZERO_DIVISOR', 'statement': '除法的分母必须非零。'},
                'preconditions': [{'condition': '分母非零', 'satisfied': False}],
                'verification': {'method': 'exact_definedness_check', 'reason': str(error)}}
    except UnsupportedMath as error:
        return _unresolved(str(error))
    if (source.operator is None) != (target.operator is None):
        return _unresolved('表达式与方程/不等式类型不同，需要额外题意，不能自动推定变换')
    valid = source.signature() == target.signature()
    rule = _rule(source, target)
    verification = {
        'method': 'exact_rational_linear_normalization',
        'source_normal_form': _normal_form(source), 'target_normal_form': _normal_form(target),
        'source_solution': source.describe(variable), 'target_solution': target.describe(variable),
        'counterexample': None if valid else _counterexample(source, target, variable),
        'reason': '精确规范形式/解集一致' if valid else '精确规范形式/解集不同，或出现未定义运算',
    }
    return {'local_validity': 'VALID' if valid else 'INVALID',
            'verification_level': 'FORMAL_VERIFIED', 'transformation': rule['id'], 'rule': rule,
            'preconditions': [
                {'condition': f'仅有实变量{variable}，表达式为有理系数一次式', 'satisfied': True},
                {'condition': '出现的每个分母均为已核验的非零常数', 'satisfied': True}],
            'verification': verification}


def _normal_form(form):
    return {'coefficient': str(form.coefficient), 'constant': str(form.constant),
            'operator': form.operator, 'domain': 'REAL'}
