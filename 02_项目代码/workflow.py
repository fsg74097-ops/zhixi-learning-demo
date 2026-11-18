"""智慧学伴正式数据本地工作流：课程证据→数据检查→诊断→建议→复核。"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import sys
import time
from project_paths import data_root

ROOT = Path(__file__).resolve().parents[1]
# Keep help/import available when machine configuration needs repair.
# main resolves the default strictly inside its existing error boundary.
try:
    DEFAULT_DATA = data_root(ROOT)
except ValueError:
    DEFAULT_DATA = ROOT / '现有资料/赛题数据/赛题资料包'


def build_bundle(data_root, node_id=None, as_of=None):
    from official_data import load_dataset
    from resource_index import build_resource_index
    from diagnosis import build_analysis
    trace = []
    started = time.perf_counter()
    dataset = load_dataset(data_root, as_of=as_of)
    trace.append({'step': '正式数据读取与质量检查', 'status': '完成', 'seconds': round(time.perf_counter()-started, 4)})
    started = time.perf_counter()
    curriculum = build_resource_index(data_root)
    trace.append({'step': '课程与材料索引', 'status': '完成', 'seconds': round(time.perf_counter()-started, 4)})
    started = time.perf_counter()
    analysis = build_analysis(dataset, node_id=node_id)
    curriculum_by_id = {item['id']: item for item in curriculum['indicators']}
    for node in analysis['nodes'].values():
        for student in node['students'].values():
            for recommendation in student['recommendations']:
                source = curriculum_by_id[recommendation['indicator_id']]
                recommendation['standard_citation'] = source['standard_citation']
                recommendation['textbook_citations'] = source['textbook_citations']
                recommendation['citation_status'] = source['evidence_status']
    trace.append({'step': '节点内学情分析与建议', 'status': '完成', 'seconds': round(time.perf_counter()-started, 4)})
    return {'schema_version': '1.0', 'dataset': dataset, 'curriculum': curriculum,
            'analysis': analysis, 'trace': trace, 'model': {'status': '未调用'}, 'teacher_reviews': []}


def model_context(bundle, student_id, node_id):
    student = bundle['analysis']['nodes'][node_id]['students'][student_id]
    records = [r for r in bundle['dataset']['valid_records']
               if r['学生编号'] == student_id and r['节点编号'] == node_id]
    ids = sorted({q for rec in student['recommendations'] for q in rec['question_ids']})
    return {'student_id': student_id, 'node_id': node_id, 'analysis': student,
            'evidence_ids': [r['记录编号'] for r in records], 'records': records,
            'material_ids': ids,
            'materials': {q: bundle['dataset']['materials'][q] for q in ids},
            'scope': '仅本次大赛公开模拟样例；当前节点；解释草稿须人工核验'}


def review_target(bundle, student_id, node_id, recommendation_id):
    for recommendation in bundle['analysis']['nodes'][node_id]['students'][student_id]['recommendations']:
        if recommendation['recommendation_id'] == recommendation_id:
            return {'student_id': student_id, 'node_id': node_id,
                    'material_ids': recommendation['question_ids'], 'recommendation': recommendation}
    raise ValueError('找不到指定建议编号')


def apply_teacher_review(bundle, decision):
    from teacher_review import review_plan
    sid, nid, rid = (decision[k] for k in ('student_id', 'node_id', 'recommendation_id'))
    plan = review_target(bundle, sid, nid, rid)
    node = bundle['dataset']['nodes'][nid]
    allowed = set(node['可用题目编号'].replace('，', '、').split('、'))
    reviewed = review_plan(plan, decision, allowed_material_ids=allowed)
    reviewed['recommendation_id'] = rid
    bundle['teacher_reviews'].append(reviewed)


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def render_html(bundle):
    def esc(value): return escape(str(value))
    def evidence(ids): return '、'.join(f'<a href="#r-{esc(i)}">{esc(i)}</a>' for i in ids) or '无有效评分证据'
    def details(title, value): return f'<details><summary>{esc(title)}</summary><pre>{esc(dump(value))}</pre></details>'
    def metric(value):
        rate = '无可计算得分率' if value['score_rate'] is None else f"{value['score_rate']:.1%}"
        return f"{value['score']:g}/{value['max_score']:g} 分 · {rate} · {value['scored_events']} 个已评分事件"
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
             '<title>智慧学伴 · 正式数据复核</title><style>body{font:16px/1.7 system-ui;margin:30px auto;max-width:1100px;padding:0 20px;color:#182b3d}a{color:#125c92}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #ccd5df;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f8;padding:14px}details{margin:12px 0;border:1px solid #d6dfe8;padding:10px}summary{cursor:pointer}nav{position:sticky;top:0;background:white;padding:10px;border-bottom:1px solid #ccc}.note{background:#fff4d9;padding:14px}input{font:inherit;width:90%;padding:8px}</style>',
             '<h1>智慧学伴 · 学习证据复核</h1><p class="note">本地规则分析，非真实教学成效验证。得分率不是掌握概率；缺少记录不表示不会。模型状态：'+esc(bundle['model']['status'])+'</p>',
             '<nav><a href="#curriculum">课程依据</a> · <a href="#students">个体与班级</a> · <a href="#quality">数据质量</a> · <a href="#records">原始记录</a></nav>',
             '<h2 id="curriculum">学习内容与材料出处</h2>', details('展开课程要求、题目及教材页码索引', bundle['curriculum']),
             '<h2 id="students">分节点查看个体、班级与典型案例</h2>',
             '<p>搜索学生编号或班级编号；建议与异常记录均保留证据。</p><input id="filter" placeholder="例如 D01 或 DC02" aria-label="筛选学生">']
    for nid, node in bundle['analysis']['nodes'].items():
        parts += [f'<h2>{esc(nid)} · {esc(node["node"].get("说明", ""))}</h2>', details('三名典型案例选择依据', node['cases'])]
        for cid, group in node['classes'].items():
            parts += [f'<h3>{esc(cid)} · {esc(metric(group["overall"]))}</h3>', details('班级分主题、分指标与共性问题', group)]
        for sid, student in node['students'].items():
            parts += [f'<details class="student" data-key="{esc(sid)} {esc(student["class_id"])}"><summary>{esc(sid)} · {esc(student["class_id"])} · {esc(metric(student["overall"]))}</summary>',
                      '<p>统计证据：'+evidence(student['overall']['evidence_ids'])+'</p>',
                      '<table><tr><th>指标</th><th>已有表现</th><th>判断边界</th></tr>']
            for iid, cell in student['indicators'].items():
                parts += [f'<tr><td>{esc(iid)}</td><td>{esc(metric(cell))}</td><td>{esc(cell.get("status", ""))}</td></tr>']
            parts += ['</table>', details('实际作答与步骤观察', student['observations'])]
            for rec in student['recommendations']:
                parts += [f'<h4>{esc(rec["indicator_id"])} · {esc(rec["kind"])}</h4><p>{esc(rec["reason"])}</p>',
                          '<p>依据：'+evidence(rec['evidence_ids'])+'</p>', details('练习顺序、分步提示及调整条件', rec)]
            parts += ['</details>']
    parts += ['<h2 id="quality">数据质量与处理说明</h2>', details('计数与剔除情况', bundle['dataset']['summary']),
              details('全部问题标记（未修改原件）', bundle['dataset']['issues']), details('实际执行过程', bundle['trace']),
              details('教师调整审计', bundle['teacher_reviews']), details('模型解释状态与草稿', bundle['model']),
              '<h2 id="records">原始记录与材料核对</h2><p>包含原版本；被替代、待评分和异常记录不一定进入统计分母。引用编号可直接跳转。</p>']
    for row in bundle['dataset']['raw_records']:
        rid = row['记录编号']
        parts += [f'<details id="r-{esc(rid)}"><summary>{esc(rid)} · {esc(row["学生编号"])} · {esc(row["材料编号"])} · {esc(row["节点编号"])}</summary>',
                  '<pre>'+esc(dump({'作答记录': row, '统计处理': bundle['dataset']['record_meta'].get(rid),
                                  '题目与参考': bundle['dataset']['materials'].get(row['材料编号'])}))+'</pre></details>']
    parts += ['<script>document.getElementById("filter").addEventListener("input",function(){let q=this.value.trim().toUpperCase();document.querySelectorAll(".student").forEach(e=>e.hidden=!e.dataset.key.includes(q));});function focusEvidence(){let e=document.getElementById(location.hash.slice(1));if(e&&e.tagName==="DETAILS")e.open=true;}window.addEventListener("hashchange",focusEvidence);focusEvidence();</script></html>']
    return '\n'.join(parts)


def export_bundle(bundle, output):
    from teacher_review import fingerprint
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT) or output == ROOT:
        raise ValueError('输出必须位于项目内的新子目录')
    if output.exists():
        raise ValueError('输出目录已存在，拒绝覆盖')
    output.mkdir(parents=True, exist_ok=False)
    artifacts = {'curriculum.json': bundle['curriculum'], 'analysis.json': bundle['analysis'],
                 'data_quality.json': {'summary': bundle['dataset']['summary'], 'issues': bundle['dataset']['issues'],
                                       'record_meta': bundle['dataset']['record_meta']},
                 'evidence_records.json': bundle['dataset']['raw_records'], 'trace.json': bundle['trace'],
                 'teacher_reviews.json': bundle['teacher_reviews'], 'model_explanation.json': bundle['model']}
    for name, value in artifacts.items():
        (output / name).write_text(dump(value)+'\n', encoding='utf-8')
    (output / '复核报告.html').write_text(render_html(bundle), encoding='utf-8')
    columns = ['node_id', 'student_id', 'class_id', 'indicator_id', 'score', 'max_score', 'scored_events', 'score_rate', 'status', 'evidence_ids']
    with (output / 'student_indicators.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for nid, node in bundle['analysis']['nodes'].items():
            for sid, student in node['students'].items():
                for iid, cell in student['indicators'].items():
                    writer.writerow({'node_id': nid, 'student_id': sid, 'class_id': student['class_id'], 'indicator_id': iid,
                                     **{k: cell.get(k) for k in columns[4:-1]}, 'evidence_ids': '、'.join(cell['evidence_ids'])})
    templates = []
    for nid, node in bundle['analysis']['nodes'].items():
        for case in node['cases']:
            sid = case['student_id']
            for rec in node['students'][sid]['recommendations'][:1]:
                plan = review_target(bundle, sid, nid, rec['recommendation_id'])
                templates.append({'student_id': sid, 'node_id': nid, 'recommendation_id': rec['recommendation_id'],
                                  'expected_sha256': fingerprint(plan), 'reviewer': '', 'reason': '',
                                  'material_ids': plan['material_ids'], 'guidance': '', 'follow_up': ''})
    (output / '教师调整模板.json').write_text(dump(templates)+'\n', encoding='utf-8')
    (output / '字段与复现说明.md').write_text(FIELD_GUIDE, encoding='utf-8')
    manifest = {'generated_at_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(),
                'stage': '内部功能与结果复核包；非正式提交包', 'schema_version': '1.0',
                'data_sources': bundle['dataset']['sources'],
                'code_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))},
                'artifact_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()},
                'limits': ['独立评测数据尚未提供', '程序测试不替代教师语义核验', '模型真实联网状态以model_explanation.json为准']}
    (output / 'manifest.json').write_text(dump(manifest)+'\n', encoding='utf-8')
    return output


FIELD_GUIDE = '''# 字段与复现说明

本包仅限赛事内部使用。运行入口：`python -X utf8 .\\02_项目代码\\workflow.py`，默认分 N01/N02 分别输出。
可用 --data 指定同字段的新资料包，--node N01 或 N02 限制节点，--as-of 带时区ISO时间限定已知记录，--output 指定项目内新目录。

- curriculum.json：课程主题、观察指标、真实题号、课标/教材定位及来源文件摘要；必要联系属团队教学建议，不冒充课标原文。
- analysis.json：nodes→students/classes→overall/topics/indicators；score 为有效记录得分和，max_score 为对应满分和，score_rate=两者之比（无有效评分为null），scored_events为事件数。不是人数百分比、正确率或掌握概率。
- evidence_ids：进入该统计的记录编号；unanswered_ids/pending_ids/excluded_ids分别列未答、待评与异常候选；原版本仍在evidence_records.json。
- observations：原作答、过程、分点评分与有限观察；recommendations：真实题目、先后顺序、提示与再次观察条件。须教师复核。
- data_quality.json：最新版本选择与问题码；异常未自动修正。统计按最新版且已评分无error记录；最高版本异常不回退旧版。
- student_indicators.csv：每节点每学生每指标一行，evidence_ids用顿号分隔，空score_rate表示不能计算。
- teacher_reviews.json：机器原建议、教师调整、理由、时间和前后SHA256；事实统计不受改写。
- trace.json：实际本地执行步骤耗时；model_explanation.json：未调用或模型草稿状态，不伪造API结果。
- manifest.json：输入和代码摘要、产物摘要与适用边界，用于复现核对。

教师调整：复制教师调整模板.json填写一条或多条的reviewer/reason/guidance/follow_up，可更换当前节点内题号；用 --review 文件路径 重新运行。不要修改expected_sha256，原计划变化时旧调整会被拒绝。
模型：先核实赛事资料能否发送所选外部API。默认无网络；--explain D01 --node N02 --allow-external-data 才会向DeepSeek发送该生该节点的限定证据。密钥仅通过DEEPSEEK_API_KEY环境变量读取。引用范围验证不能证明语义正确。
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--node')
    parser.add_argument('--as-of')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--explain', metavar='STUDENT_ID')
    parser.add_argument('--allow-external-data', action='store_true', help='仅在确认赛事数据使用允许后启用外部API')
    args = parser.parse_args()
    try:
        stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d_%H%M%S_%f')
        output = args.output or ROOT / '04_测试与验证' / ('正式流程_v001_' + stamp)
        if output.exists():
            raise ValueError('输出目录已存在，拒绝覆盖')
        if args.explain and (not args.node or not args.allow_external_data):
            raise ValueError('模型调用需要指定--node并确认数据允许外发后使用--allow-external-data')
        source = args.data if args.data is not None else data_root(ROOT)
        bundle = build_bundle(source, args.node, args.as_of)
        if args.review:
            decisions = json.loads(args.review.read_text(encoding='utf-8-sig'))
            if not isinstance(decisions, list):
                raise ValueError('教师调整文件最外层必须为列表')
            for decision in decisions:
                apply_teacher_review(bundle, decision)
        failed_model = False
        if args.explain:
            from deepseek_client import explain
            try:
                bundle['model'] = {'status': '已返回待核验草稿', **explain(model_context(bundle, args.explain, args.node))}
            except ValueError as error:
                bundle['model'] = {'status': '调用失败', 'error': str(error)}
                failed_model = True
        path = export_bundle(bundle, output)
        print(f'本地结果已写入：{path}\n模型状态：{bundle["model"]["status"]}\n打开 复核报告.html 查看依据。')
        return 2 if failed_model else 0
    except (ValueError, OSError, KeyError) as error:
        print('流程失败：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
