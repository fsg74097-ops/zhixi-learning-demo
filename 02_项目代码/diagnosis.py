"""以有效记录为依据的离线诊断与教学建议；不执行自动数学评分或掌握概率推断。"""
from copy import deepcopy
from decimal import Decimal
from datetime import datetime, timedelta, timezone, date
import re


HINTS = {
    'T01I1': ['先说清每个量的含义，列出数量之间的关系。', '代入负数时连同负号加括号，再按运算顺序计算。', '回到原情境检查数量与单位是否一致。'],
    'T01I2': ['逐项检查字母及其指数，先判断哪些项可以合并。', '去括号时把括号前的符号或系数作用到每一项。', '只合并同类项，再用一个自选数值检查变形前后是否一致。'],
    'T01I3': ['用文字说明题目中的数量关系，再写代数式。', '解释化简中每一步依据的规则。', '选择一个符合情境的数值，检查代数式是否仍表达原来的关系。'],
    'T02I1': ['先圈出未知数项，说明希望得到什么形式。', '每次变形都说明等式两边做了什么相同的运算。', '把求得的值代回原方程，分别核对左右两边。'],
    'T02I2': ['观察两式中同一个未知数的系数，说明适合代入还是加减消元。', '消去一个未知数后，逐步解出另一个未知数。', '把结果代回原来的两条方程，分别检查。'],
    'T02I3': ['先设未知数并标明单位，用文字写出等量关系。', '根据本题已知条件列出方程，解释每一项代表什么。', '代回原情境核对结果，说明是否符合数量限制。'],
    'T03I1': ['记录每一步对不等式两边进行的运算。', '特别圈出乘除的数：它是正数还是负数，不等号方向需要怎样处理？', '在你写出的解集中与解集外各选一个数，代回原不等式检查。'],
    'T03I2': ['先分别求出两个不等式的解集。', '在同一数轴标出两个范围，区分端点是否包含。', '只保留同时满足两个条件的部分，并代入检查一个边界附近的数。'],
    'T03I3': ['把“至少”“不超过”等条件分别译成数量关系。', '写出不等式后说明变量的单位及现实限制。', '检查边界是否能取到，若要求整数，再核对相邻整数。'],
}


def _ids(text):
    return [value for value in re.split(r'[、,，;；\s]+', text or '') if value]


def _metric(valid, selected):
    evidence = {r['记录编号'] for r in valid}
    score = sum(int(r['得分']) for r in valid)
    maximum = sum(int(r['满分']) for r in valid)
    return {
        'scored_events': len(valid), 'score': score, 'max_score': maximum,
        'score_rate': score / maximum if maximum else None,
        'full_score_events': sum(int(r['得分']) == int(r['满分']) for r in valid),
        'evidence_ids': [r['记录编号'] for r in valid],
        'question_ids': sorted({r['材料编号'] for r in valid}),
        'unanswered_ids': [r['记录编号'] for r in selected if r['评分状态'] == '未作答'],
        'pending_ids': [r['记录编号'] for r in selected if r['评分状态'] == '待评分'],
        'excluded_ids': [r['记录编号'] for r in selected
                         if r['记录编号'] not in evidence and r['评分状态'] == '已评分'],
    }


def _status(cell):
    if cell['scored_events'] < 2:
        return '证据不足'
    if cell['score_rate'] < 0.8:
        return '需巩固线索'
    return '已有表现较好'


def _observation(row, dataset):
    question = dataset['questions'][row['材料编号']]
    points = dataset['record_meta'][row['记录编号']]['point_scores']
    rubric = {}
    for item in question.get('评分点', '').replace('\\n', '\n').splitlines():
        match = re.fullmatch(r'\s*(\S+)\s+(.+?)\s+(\d+)分\s*', item)
        if match:
            rubric[match[1]] = {'label': match[2], 'max_score': int(match[3])}
    unearned = [pid for pid, value in points.items()
                if pid in rubric and value < rubric[pid]['max_score']]
    findings = []
    if unearned:
        findings.append({'code': 'unearned_scoring_points', 'basis': '提供的分点评分',
                         'point_ids': unearned,
                         'text': '分点评分显示以下步骤未得满分：' + '；'.join(rubric[p]['label'] for p in unearned)
                                 + '。需结合原过程核对，不据此推断稳定的认知错误。'})
    process = row.get('解答过程', '').replace('\\n', '\n')
    lines = [re.sub(r'^\s*\d+[.、]\s*', '', line).strip().rstrip('。；;')
             for line in process.splitlines() if line.strip()]
    compact = [re.sub(r'\s+', '', line).replace('−', '-') for line in lines]
    # 仅验证相邻的简单数值除法步骤；拒绝猜测任意数学表达式的等价性。
    number = r'-?\d+(?:\.\d+)?'
    for previous, current in zip(compact, compact[1:]):
        left = re.fullmatch(r'(-\d+(?:\.\d+)?)x([<>≤≥])(' + number + ')', previous)
        right = re.fullmatch(r'x([<>≤≥])(' + number + ')', current)
        if left and right and Decimal(left[1]) != 0:
            if Decimal(left[3]) / Decimal(left[1]) == Decimal(right[2]) and left[2] == right[1]:
                findings.append({'code': 'negative_division_direction', 'basis': '相邻过程原文与数值运算',
                                 'quote': [previous, current],
                                 'text': '这两步由负数系数化为1，数值除法一致，但不等号方向未改变；本次过程有负数相除变向问题。'})
    if lines:
        last_text = lines[-1].lstrip('=')
        final_text = row.get('学生最终作答', '').strip().rstrip('。；;')
        if last_text and final_text and last_text != final_text:
            findings.append({'code': 'process_final_text_difference', 'basis': '文字对照，未做等价判定',
                             'quote': [last_text, final_text],
                             'text': '过程末行与最终作答文字不同；可能是等价形式或记录差异，请教师核对，不能仅据此判错。'})
    return {'record_id': row['记录编号'], 'event_id': row['作答事件编号'],
            'question_id': row['材料编号'], 'indicator_ids': _ids(question['指标编号']),
            'score': int(row['得分']), 'max_score': int(row['满分']),
            'question': question['题干或内容'], 'student_answer': row.get('学生最终作答', ''),
            'student_process': row.get('解答过程', ''),
            'point_scores': deepcopy(points), 'rubric': rubric, 'unearned_point_ids': unearned,
            'findings': findings, 'limitations': ['not_semantically_graded'],
            'reference_answer': question.get('参考答案', ''),
            'reference_explanation': question.get('参考解析', ''),
            'reference_visibility': '教师核对区；不包含在学习提示中'}


def _recommendations(student_id, node_id, indicators, allowed, rows, dataset):
    recs = []
    for indicator_id, cell in indicators.items():
        questions = [q for q in allowed if indicator_id in _ids(dataset['questions'][q]['指标编号'])]
        if not questions:
            continue
        requirement = dataset['requirements'][indicator_id]
        relevant = [r for r in rows if indicator_id in _ids(dataset['questions'][r['材料编号']]['指标编号'])]
        attempted = {r['材料编号'] for r in relevant}
        missed = {r['材料编号'] for r in relevant if int(r['得分']) < int(r['满分'])}
        process_missed = {r['材料编号'] for r in relevant
                          if r.get('解答过程', '').strip() and int(r['得分']) < int(r['满分'])}
        if cell['status'] == '需巩固线索':
            kind, priority = '巩固练习', 0
            chosen = sorted(questions, key=lambda q: (0 if q in process_missed else 1 if q in missed else 2 if q not in attempted else 3, q))[:2]
            reason = f"{cell['scored_events']}个有效事件得{cell['score']}/{cell['max_score']}分；先复核失分步骤，再练同指标题。"
        elif cell['status'] == '证据不足':
            kind, priority = '补充观察', 1
            chosen = sorted(questions, key=lambda q: (q in attempted, q))[:2]
            reason = f"该指标只有{cell['scored_events']}个有效已评分事件，不足以概括表现；缺记录不代表不会。"
        else:
            kind, priority = '迁移与解释', 2
            chosen = sorted(questions, key=lambda q: (q in attempted, q))[:2]
            reason = f"该指标{cell['scored_events']}个有效事件得{cell['score']}/{cell['max_score']}分，已有表现较好；用解释和检验检查迁移。"
        hints = HINTS.get(indicator_id, ['先用自己的话说明本题要求。', '逐步写出解答，每步说明理由。', '回到题目核对条件。'])
        steps = [
            {'order': 1, 'activity': '独立尝试并保留全部过程；先不显示参考答案。',
             'question_ids': chosen[:1], 'hints': [hints[0]]},
            {'order': 2, 'activity': '需要时逐级使用提示，标记在哪一步需要帮助；教师核对对应评分点。',
             'question_ids': chosen[:1], 'hints': hints[1:]},
            {'order': 3, 'activity': ('用另一题独立复练并解释每一步。' if len(chosen) > 1
                                     else '本指标可用题有限：遮住原过程重新解释同题；不能把重复作答当作迁移证据。'),
             'question_ids': chosen[1:] or chosen[:1], 'hints': ['完成后再核对条件、单位和边界，记录是否用过提示。']},
        ]
        if kind == '迁移与解释':
            steps[2]['activity'] = ('完成所列题后比较解法或给出另一种检验方法，解释为何成立；记录任务是团队设计的活动。'
                                    if len(chosen) > 1 else '解释同题每一步并另给一种检验方法；仅作重复解释观察。')
        recs.append({'recommendation_id': f'{student_id}-{node_id}-{indicator_id}',
                     'student_id': student_id, 'node_id': node_id, 'indicator_id': indicator_id,
                     'kind': kind, 'priority': priority, 'reason': reason,
                     'evidence_ids': cell['evidence_ids'][:], 'question_ids': chosen,
                     'steps': steps, 'requirement_ref': deepcopy(requirement),
                     'follow_up': '教师记录独立完成情况、提示层级与评分点。若两道不同题均能独立完成并说明关键步骤，转入解释或迁移；若同类步骤再次失分，回看该步骤再补一题；若仍缺评分，先补评后再诊断。此阈值为本队暂定教学规则，不是教学成效证明。',
                     'review_status': '待教师复核'})
    recs.sort(key=lambda r: (r['priority'],
                            indicators[r['indicator_id']]['score_rate'] if indicators[r['indicator_id']]['score_rate'] is not None else -1,
                            r['indicator_id']))
    chosen_recs = recs[:3]
    # 高表现且有足量证据者仍须检查迁移，不能让未覆盖指标占满所有活动。
    total_max = sum(int(r['满分']) for r in rows)
    overall_rate = sum(int(r['得分']) for r in rows) / total_max if total_max else None
    transfer = next((r for r in recs if r['kind'] == '迁移与解释'), None)
    if len(rows) >= 3 and overall_rate >= .8 and transfer and transfer not in chosen_recs:
        chosen_recs = chosen_recs[:2] + [transfer]
    return chosen_recs


def _cases(students):
    cases = []
    used = set()
    partial = [s for s in students.values() if any(0 < o['score'] < o['max_score'] for o in s['observations'])]
    partial.sort(key=lambda s: (-sum(f['code'] == 'negative_division_direction' for o in s['observations'] for f in o['findings']), s['student_id']))
    strong = [s for s in students.values() if s['overall']['scored_events'] >= 3 and s['overall']['score_rate'] >= .8]
    strong.sort(key=lambda s: (-s['overall']['score_rate'], -s['overall']['scored_events'], s['student_id']))
    sparse = [s for s in students.values() if any(c['scored_events'] < 2 for c in s['indicators'].values())]
    sparse.sort(key=lambda s: (s['overall']['scored_events'], s['student_id']))
    for case_type, candidates, reason in [
        ('部分得分', partial, '至少一个有效解答事件获得部分分，展示分点与实际步骤。'),
        ('表现较好', strong, '本节点至少3个有效事件且总得分率不低于80%；仅说明已有题目表现。'),
        ('记录不足', sparse, '至少一个本节点可测指标不足2个有效事件，展示补充观察。'),
    ]:
        candidate = next((s for s in candidates if s['student_id'] not in used), None)
        if candidate:
            used.add(candidate['student_id'])
            cases.append({'student_id': candidate['student_id'], 'case_type': case_type,
                          'selection_reason': reason, 'evidence_ids': candidate['overall']['evidence_ids'][:]})
    return cases


def build_analysis(dataset, node_id=None):
    """输出各节点全部学生、班级及候选典型案例；输入遵循 official_data.load_dataset。"""
    if node_id is not None and node_id not in dataset['nodes']:
        raise ValueError(f'未知测验节点：{node_id}')
    available_nodes = sorted(dataset['nodes'])
    if dataset.get('as_of'):
        cutoff_date = datetime.fromisoformat(dataset['as_of']).astimezone(timezone(timedelta(hours=8))).date()
        available_nodes = [nid for nid in available_nodes
                           if date.fromisoformat(dataset['nodes'][nid]['模拟测验日期']) <= cutoff_date]
        if node_id is not None and node_id not in available_nodes:
            raise ValueError('所选节点晚于历史截止时间，不能生成未来教学建议')
    output = {
        'schema_version': '1.0',
        'methodology': {
            'engine': '本地确定性规则与可复核统计；未调用大模型',
            'score_rate': '有效已评分事件的得分之和/满分之和；不是正确率或掌握概率。',
            'missing': '未作答、待评分、隔离记录均不进入得分率分母；无记录保留未知。',
            'thresholds': '本队暂定：指标少于2个有效事件为证据不足；否则得分率<0.8为巩固线索，>=0.8为已有表现较好。未做效度验证，不是官方标准。',
            'node_comparison': '两个节点题目与已教范围不同，分开统计；分数差不能直接解释为学习进步或教学效果。',
            'scope': '建议仅使用该节点允许的题目和指标。先后顺序为待教师复核的教学安排。',
            'process': '保留实际作答、过程和分点评分。只对匹配的简单负系数不等式步骤核验变向；其余数学语义未自动判定。',
            'case_selection': '按证据选三类不同学生；若无符合条件者则缺项，不虚构代表案例。',
        }, 'nodes': {},
    }
    for nid in ([node_id] if node_id is not None else available_nodes):
        node = dataset['nodes'][nid]
        allowed = [q for q in _ids(node['可用题目编号']) if q in dataset['questions']]
        indicator_ids = sorted({i for q in allowed for i in _ids(dataset['questions'][q]['指标编号'])})
        topic_ids = sorted({dataset['questions'][q]['主题编号'] for q in allowed})
        valid = [r for r in dataset['valid_records'] if r['节点编号'] == nid]
        selected = [r for r in dataset['selected_records'] if r['节点编号'] == nid]

        def cells(vrows, srows, dimension, keys):
            def match(row, key):
                question = dataset['questions'].get(row['材料编号'], {})
                return key in _ids(question.get(dimension, ''))
            return {key: _metric([r for r in vrows if match(r, key)], [r for r in srows if match(r, key)]) for key in keys}

        students = {}
        for sid, raw_student in sorted(dataset['students'].items()):
            vrows = [r for r in valid if r['学生编号'] == sid]
            srows = [r for r in selected if r['学生编号'] == sid]
            indicators = cells(vrows, srows, '指标编号', indicator_ids)
            for iid, cell in indicators.items():
                cell.update(status=_status(cell), label=dataset['requirements'][iid]['观察指标'])
            students[sid] = {'student_id': sid, 'class_id': raw_student['模拟班级编号'],
                             'overall': _metric(vrows, srows),
                             'topics': cells(vrows, srows, '主题编号', topic_ids), 'indicators': indicators,
                             'observations': [_observation(r, dataset) for r in vrows],
                             'recommendations': _recommendations(sid, nid, indicators, allowed, vrows, dataset)}
        classes = {}
        for cid in sorted({s['模拟班级编号'] for s in dataset['students'].values()}):
            members = [sid for sid, s in students.items() if s['class_id'] == cid]
            vrows = [r for r in valid if r['学生编号'] in members]
            srows = [r for r in selected if r['学生编号'] in members]
            rates = [students[s]['overall']['score_rate'] for s in members if students[s]['overall']['score_rate'] is not None]
            needs = []
            for iid in indicator_ids:
                weak = [sid for sid in members if students[sid]['indicators'][iid]['status'] == '需巩固线索']
                sparse = [sid for sid in members if students[sid]['indicators'][iid]['status'] == '证据不足']
                needs.append({'indicator_id': iid, 'label': dataset['requirements'][iid]['观察指标'],
                              'consolidation_student_ids': weak, 'insufficient_student_ids': sparse,
                              'class_student_count': len(members),
                              'action': '先对有效记录中的共性失分步骤组织讲解，再为记录不足者补充观察；不把两类人数合并成未掌握人数。'})
            classes[cid] = {'class_id': cid, 'student_ids': members, 'overall': _metric(vrows, srows),
                            'observed_student_count': len(rates),
                            'mean_student_score_rate': sum(rates)/len(rates) if rates else None,
                            'topics': cells(vrows, srows, '主题编号', topic_ids),
                            'indicators': cells(vrows, srows, '指标编号', indicator_ids), 'common_needs': needs}
        output['nodes'][nid] = {'node_id': nid, 'node': deepcopy(node),
                                'students': students, 'classes': classes, 'cases': _cases(students)}
    return output
