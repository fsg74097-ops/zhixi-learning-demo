"""三个自建合成样例的API契约及引用边界评测，无正式数据读写。"""
import copy

from deepseek_client import ExplanationError, explain, validate_explanation


def synthetic_cases():
    scope = '自建合成接口样例，非赛事数据；仅检验引用与事实边界，解释草稿待人工复核'
    normal = {'scope': scope, 'student_id': 'SYNTHETIC-STUDENT', 'node_id': 'SYNTHETIC-NODE',
              'evidence_ids': ['SYNTHETIC-E1'], 'material_ids': ['SYNTHETIC-M1'],
              'records': [{'id': 'SYNTHETIC-E1', 'score': 0, 'max_score': 1,
                           'observation': '虚构练习中将2+3写成6，仅有一次观察。'}],
              'materials': [{'id': 'SYNTHETIC-M1', 'task': '画计数图核对加法，提示中不直接透露答案。'}]}
    insufficient = {**normal, 'evidence_ids': [], 'records': [],
                    'analysis': {'status': '无有效证据', 'score_rate': None}}
    conflict = {**normal, 'evidence_ids': ['SYNTHETIC-CONFLICT'],
                'records': [{'id': 'SYNTHETIC-CONFLICT', 'score': 0, 'max_score': 1,
                             'scoring_point_total': 1, 'quality_issue': '总分与分点合计矛盾，待人工核对'}],
                'analysis': {'status': '矛盾记录已排除统计', 'score_rate': None}}
    return copy.deepcopy([
        {'case_id': 'normal', 'name': '单次有效记录', 'context': normal,
         'manual_fact_checks': ['仅陈述一次观察', '不把得分率解释为掌握概率', '不推断粗心或动机']},
        {'case_id': 'insufficient', 'name': '信息不足', 'context': insufficient,
         'manual_fact_checks': ['说明缺少有效作答', '不将缺失解释为不会', '建议用于补充观察']},
        {'case_id': 'conflict', 'name': '评分矛盾', 'context': conflict,
         'manual_fact_checks': ['说明总分与分点矛盾', '保持排除统计', '将裁定留给人工复核']},
    ])


def evaluate_result(case, result):
    """自动核对结构和引用，保留语义事实、提示适切性的人工检查项。"""
    context = case['context']
    explanation = result.get('explanation')
    failure_codes = []
    try:
        validate_explanation(explanation, set(context['evidence_ids']), set(context['material_ids']))
    except ExplanationError as error:
        failure_codes.append(error.metadata['reason_code'])
    valid = not failure_codes
    observations = explanation['observations'] if valid else []
    suggestions = explanation['suggestions'] if valid else []
    evidence_count = sum(len(item['evidence_ids']) for item in observations + suggestions)
    material_count = sum(len(item['material_ids']) for item in suggestions)
    return {'case_id': case['case_id'], 'name': case['name'], 'passed_automated_checks': valid,
            'failure_codes': failure_codes, 'evidence_references': evidence_count,
            'material_references': material_count, 'citation_scope_valid': valid,
            'cited_evidence_ids': sorted({identity for item in observations + suggestions for identity in item['evidence_ids']}),
            'cited_material_ids': sorted({identity for item in suggestions for identity in item['material_ids']}),
            'uncertainty_present': bool(valid and explanation['uncertainties']),
            'statistics_fields_absent': valid,
            'empty_evidence_has_no_observations': not observations if not context['evidence_ids'] else None,
            'fact_consistency_review': '待人工复核', 'manual_fact_checks': case['manual_fact_checks'],
            'teaching_appropriateness_review': '待教师复核', 'metadata': result.get('metadata', {})}


def run_evaluation(*, live=False, api_key=None, model=None, thinking=False, reasoning_effort='high',
                   max_tokens=None, transport=None, case_id='all'):
    cases = [case for case in synthetic_cases() if case_id in ('all', case['case_id'])]
    if not cases:
        raise ValueError('未知合成样例类型')
    base = {'scope': '仅自建合成样例', 'cases': len(cases), 'education_validity_established': False,
            'automatic_scope': 'JSON结构、引用范围及缺少证据时的观察限制',
            'remaining_reviews': ['事实表述与原始材料一致性', '建议的教学适切性', '真实模型独立质量评估']}
    if not live:
        return {**base, 'mode': '预览，未调用API',
                'samples': [{'case_id': case['case_id'], 'name': case['name'],
                             'context': case['context'], 'manual_fact_checks': case['manual_fact_checks']} for case in cases]}
    reports = []
    for case in cases:
        try:
            result = explain(case['context'], transport=transport, api_key=api_key, model=model, thinking=thinking,
                             reasoning_effort=reasoning_effort, max_tokens=max_tokens)
            reports.append(evaluate_result(case, result))
        except ExplanationError as error:
            reports.append({'case_id': case['case_id'], 'name': case['name'],
                            'passed_automated_checks': False, 'failure_codes': [error.metadata['reason_code']],
                            'metadata': error.metadata, 'fact_consistency_review': '未取得可用解释'})
    accepted = sum(report['passed_automated_checks'] for report in reports)
    return {**base, 'mode': '离线注入响应' if transport is not None else '真实API合成样例',
            'accepted': accepted, 'rejected': len(cases) - accepted, 'results': reports}
