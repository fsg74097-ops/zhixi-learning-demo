"""Read the six official CSV tables without changing a source value.

Selection is a team-defined conservative policy, not an official adjudication:
one highest available revision per event, then quarantine validation errors;
never fall back to an older score when the newest revision is pending or bad.
``as_of`` restricts node dates AND revision timestamps before selection.
"""
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re

SCHEMA = {
    'students': ('模拟学生', '学生编号 模拟班级编号 年级'),
    'materials': ('题目与材料', '材料编号 主题编号 材料类型 题型 题干或内容 选项 参考答案 参考解析 满分 评分点 指标编号 来源或资料文件'),
    'nodes': ('测验节点', '节点编号 模拟测验日期 已教及测量主题 说明 可用题目编号'),
    'requirements': ('课程要求', '主题编号 主题 主题目标 指标编号 观察指标 课标整理编号 课标文件页／正文页 教材正文页'),
    'records': ('作答记录', '记录编号 学生编号 材料编号 主题编号 节点编号 作答事件编号 修订版本 模拟提交时间 评分状态 得分 满分 学生最终作答 解答过程 评分点得分'),
    'field_definitions': ('字段说明', '数据表 字段名 类型 含义 取值或格式 示例 为空时'),
}
KEYS = {'students': '学生编号', 'materials': '材料编号', 'nodes': '节点编号', 'requirements': '指标编号'}
BEIJING = timezone(timedelta(hours=8))


def _integer(value):
    if not re.fullmatch(r'-?\d+', value):
        raise ValueError(f'不是整数: {value!r}')
    return int(value)


def _timestamp(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)', value):
        raise ValueError('时间必须为带时区、精确到秒的 ISO 8601 字符串')
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError('时间缺少时区')
    return result


def _read_tables(folder):
    tables, sources = {}, {}
    for key, (name, required) in SCHEMA.items():
        path = folder / f'{name}.csv'
        if not path.is_file():
            raise ValueError(f'缺少数据文件: {path}')
        with path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames or []
            missing = set(required.split()) - set(headers)
            if missing or len(headers) != len(set(headers)):
                raise ValueError(f'{path.name} 字段缺失或重复: {", ".join(sorted(missing))}')
            rows = []
            for number, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f'{path.name} 第{number}条逻辑记录列数与表头不符')
                rows.append(row)
            tables[key] = rows
        sources[path.name] = {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                              'rows': len(rows)}
    return tables, sources


def _unique(rows, key, name):
    lookup = {}
    for row in rows:
        value = row[key]
        if not value.strip() or value in lookup:
            raise ValueError(f'{name}: {key} 缺失或重复: {value!r}')
        lookup[value] = row
    return lookup


def resolve_data_directory(path):
    """Resolve either a package entry point or an arbitrary six-CSV folder."""
    base = Path(path).resolve()
    candidates = (base, base / '模拟学情数据', base / '4_赛题数据/模拟学情数据')
    folder = next((candidate for candidate in candidates if (candidate / '作答记录.csv').is_file()), None)
    if folder is None:
        raise ValueError(f'未找到作答记录.csv，请选择六张CSV所在目录或完整资料包目录：{base}')
    return folder


def load_dataset(path, *, as_of=None):
    """Return raw tables, latest candidates, valid scored rows and audit metadata.

    Accept a package root, ``4_赛题数据`` or the CSV directory. ``source_row`` is
    a logical CSV row (header=1), not a physical text line; quoted fields may
    contain line breaks. Invalid schemas/duplicate primary keys raise ValueError.
    Row-level issues are retained and excluded from valid_records.
    """
    folder = resolve_data_directory(path)
    cutoff = _timestamp(as_of) if as_of is not None else None
    tables, sources = _read_tables(folder)
    result = {key: _unique(tables[key], field, SCHEMA[key][0]) for key, field in KEYS.items()}
    for key in KEYS:
        if not result[key]:
            raise ValueError(f'{SCHEMA[key][0]}.csv 没有数据，请补充至少一条有效记录后重新加载。')
    _unique(tables['records'], '记录编号', '作答记录')
    questions = {key: row for key, row in result['materials'].items() if row['材料类型'] == '题目'}
    if not questions:
        raise ValueError('题目与材料.csv 没有可分析的题目，请核对材料类型后重新加载。')
    requirements, nodes = result['requirements'], result['nodes']
    topics = {r['主题编号'] for r in requirements.values()}
    question_points, node_dates = {}, {}
    for qid, question in questions.items():
        maximum = _integer(question['满分'])
        if maximum <= 0 or not question['参考答案'].strip() or not question['参考解析'].strip():
            raise ValueError(f'{qid} 题目满分或答案/解析无效')
        indicator = requirements.get(question['指标编号'])
        if indicator is None or indicator['主题编号'] != question['主题编号']:
            raise ValueError(f'{qid} 题目指标/主题无法关联课程要求')
        points = {}
        for line in question['评分点'].splitlines():
            match = re.fullmatch(r'(\S+)\s+(.+)\s+(\d+)分', line.strip())
            if not match or match[1] in points or not match[1].startswith(qid + 'P'):
                raise ValueError(f'{qid} 评分点定义无法解析或重复: {line!r}')
            points[match[1]] = int(match[3])
        if not points or sum(points.values()) != maximum:
            raise ValueError(f'{qid} 评分点定义与满分不一致')
        question_points[qid] = points
    for nid, node in nodes.items():
        node_dates[nid] = date.fromisoformat(node['模拟测验日期'])
        if not set(node['可用题目编号'].split('、')) <= set(questions):
            raise ValueError(f'{nid} 可用题目编号包含未知题目')
        if not set(node['已教及测量主题'].split('、')) <= topics:
            raise ValueError(f'{nid} 已教及测量主题包含未知主题')

    issues, meta, times, events = [], {}, {}, defaultdict(list)

    def add(row, code, message, severity='error'):
        rid = row['记录编号']
        if code in meta[rid]['issue_codes']:
            return
        meta[rid]['issue_codes'].append(code)
        issues.append({'record_id': rid, 'event_id': row['作答事件编号'], 'code': code,
                       'severity': severity, 'message': message, 'source_row': meta[rid]['source_row'],
                       'source_file': '作答记录.csv'})

    for index, row in enumerate(tables['records'], 2):
        rid, qid, nid = row['记录编号'], row['材料编号'], row['节点编号']
        m = meta[rid] = {'source_row': index, 'score': None, 'max_score': None, 'point_scores': {},
                        'revision': None, 'selected': False, 'valid': False, 'eligible': True,
                        'issue_codes': []}
        for field, target in [('得分', 'score'), ('满分', 'max_score'), ('修订版本', 'revision')]:
            if field == '得分' and not row[field]:
                continue
            try:
                m[target] = _integer(row[field])
                if target != 'score' and m[target] <= 0:
                    raise ValueError('应为正整数')
            except ValueError:
                m[target] = None
                add(row, f'invalid_{target}', f'{field}必须为有效整数，满分和修订版本必须为正数')
        try:
            times[rid] = _timestamp(row['模拟提交时间'])
        except ValueError:
            times[rid] = None
            add(row, 'invalid_timestamp', '模拟提交时间不是带时区的 ISO 8601 时间')
        if row['学生编号'] not in result['students']:
            add(row, 'unknown_student', '学生编号无法关联')
        question, node = questions.get(qid), nodes.get(nid)
        if question is None:
            add(row, 'unknown_question', '材料编号无法关联题目')
        else:
            if row['主题编号'] != question['主题编号']:
                add(row, 'topic_mismatch', '记录主题与题目主题不一致')
            if m['max_score'] != _integer(question['满分']):
                add(row, 'max_score_mismatch', '记录满分与题目满分不一致')
        if node is None:
            add(row, 'unknown_node', '节点编号无法关联')
        else:
            if qid not in node['可用题目编号'].split('、'):
                add(row, 'question_outside_node', '题目不属于该节点可用题目范围')
            if question and question['主题编号'] not in node['已教及测量主题'].split('、'):
                add(row, 'topic_outside_node', '题目主题在该节点尚未教学/测量')
            if times[rid] and times[rid].astimezone(BEIJING).date() < node_dates[nid]:
                add(row, 'submission_before_node', '提交时间早于节点测验日期')
        expected_event = f"{row['学生编号']}-{nid}-{qid}"
        if row['作答事件编号'] != expected_event:
            add(row, 'event_identity_mismatch', '作答事件编号与学生/节点/题目不一致')

        state = row['评分状态']
        if state not in {'已评分', '待评分', '未作答'}:
            add(row, 'invalid_status', '未知评分状态')
        elif state == '已评分':
            if m['score'] is None:
                add(row, 'missing_score', '已评分记录缺少有效得分')
            if not row['学生最终作答'].strip():
                add(row, 'missing_answer', '已评分记录缺少最终答案')
            if m['score'] is not None and m['max_score'] is not None and not 0 <= m['score'] <= m['max_score']:
                add(row, 'score_out_of_range', '得分超出0到满分范围；保留原值，不截断')
            for part in row['评分点得分'].split('；'):
                match = re.fullmatch(r'([^=；]+)=(-?\d+)', part.strip())
                if not match or match[1] in m['point_scores']:
                    add(row, 'invalid_point_scores', '评分点得分无法解析或评分点编号重复')
                else:
                    m['point_scores'][match[1]] = int(match[2])
            expected_points = question_points.get(qid, {})
            if question and set(m['point_scores']) != set(expected_points):
                add(row, 'point_id_mismatch', '作答评分点编号与题目评分点集合不一致')
            if any(not 0 <= value <= expected_points.get(key, 0) for key, value in m['point_scores'].items()):
                add(row, 'point_out_of_range', '评分点得分超出对应评分点范围')
            if m['score'] is not None and sum(m['point_scores'].values()) != m['score']:
                add(row, 'point_total_mismatch', '评分点合计与记录总分不一致')
        else:
            if row['得分'] or row['评分点得分']:
                add(row, 'unscored_has_score', '未评分状态却有得分或评分点得分')
            if state == '未作答' and (row['学生最终作答'] or row['解答过程']):
                add(row, 'unanswered_has_answer', '未作答状态却有答案或解答过程')

        if cutoff is not None:
            if times[rid] is not None and times[rid] > cutoff:
                m['eligible'] = False
            if nid in node_dates and node_dates[nid] > cutoff.astimezone(BEIJING).date():
                m['eligible'] = False
        if m['eligible']:
            events[row['作答事件编号']].append(row)

    selected = []
    for event_id, rows in events.items():
        revisions = [meta[r['记录编号']]['revision'] for r in rows]
        if None in revisions or len(revisions) != len(set(revisions)):
            for row in rows:
                add(row, 'ambiguous_revision', '事件修订版本缺失、无效或重复，无法唯一选取')
            continue
        ordered = sorted(rows, key=lambda r: meta[r['记录编号']]['revision'])
        ordered_times = [times[r['记录编号']] for r in ordered]
        if any(a and b and a > b for a, b in zip(ordered_times, ordered_times[1:])):
            for row in rows:
                add(row, 'revision_time_conflict', '更高修订版本的提交时间早于旧版本')
        row = ordered[-1]
        meta[row['记录编号']]['selected'] = True
        selected.append(row)
    selected.sort(key=lambda row: meta[row['记录编号']]['source_row'])
    valid = []
    for row in selected:
        m = meta[row['记录编号']]
        if row['评分状态'] == '已评分' and not m['issue_codes']:
            m['valid'] = True
            valid.append(row)
    result.update({
        'schema_version': '1.0', 'data_dir': str(folder), 'as_of': as_of,
        'tables': tables, 'questions': questions, 'question_points': question_points,
        'raw_records': tables['records'], 'selected_records': selected, 'valid_records': valid,
        'record_meta': meta, 'issues': issues, 'sources': sources,
        'policy': {
            'status': '团队候选保守口径，非命题方裁定',
            'revision': '截止时间内取最大修订版本；重复版本隔离；最新异常/待评分不回退旧分数',
            'denominator': '仅最新、已评分且无结构或一致性错误的记录进入得分统计',
            'missing': '待评分、未作答及异常均不计为错误；分别报告数量',
            'time': 'as_of同时限制节点日期和提交时间；不传则使用所有已提供修订',
            'limits': '未验证全部答案数学语义、Excel逐格一致性或独立评测集；分数不等于知识掌握概率',
        },
        'summary': {
            'raw_records': len(tables['records']),
            'unique_events': len({r['作答事件编号'] for r in tables['records']}),
            'eligible_events': len(events), 'selected_records': len(selected), 'valid_records': len(valid),
            'superseded_records': sum(len(rows) - 1 for rows in events.values()),
            'quarantined_selected': sum(bool(meta[r['记录编号']]['issue_codes']) for r in selected),
            'ambiguous_events': sum(any('ambiguous_revision' in meta[r['记录编号']]['issue_codes'] for r in rows) for rows in events.values()),
            'raw_statuses': dict(Counter(r['评分状态'] for r in tables['records'])),
            'selected_statuses': dict(Counter(r['评分状态'] for r in selected)),
            'issue_records': len({i['record_id'] for i in issues}),
            'students': len(result['students']), 'questions': len(questions), 'nodes': len(nodes),
        },
    })
    return result
