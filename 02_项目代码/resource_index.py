"""正式范围课程索引。定位为人工核对后的固定证据，源文件变化自动失效。

课标62/63物理页已目视核验；教材摘录按PDF提取页与印刷页核对。
此模块不将关键词检索视为全书数学语义验证。无需第三方运行依赖。
"""
import csv
import hashlib

from official_data import resolve_data_directory

PDF_DIR = '5_课程标准及相关教学资源'
HASHES = {}
REQ_HASH = ''
CITATIONS = {}
STANDARD = {}

def build_resource_index(data_root):
    folder = resolve_data_directory(data_root)
    root = folder.parent.parent if folder.parent.name == '4_赛题数据' else folder
    def read(name):
        with (folder / (name + '.csv')).open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))
    requirements, materials = read('课程要求'), read('题目与材料')
    issues, sources = [], {}
    requirements_unchanged = hashlib.sha256((folder / '课程要求.csv').read_bytes()).hexdigest() == REQ_HASH
    if not requirements_unchanged:
        issues.append({'code': 'requirements_changed', 'message': '课程要求变化，原教材与课标映射需重新核对'})
    for filename, expected in HASHES.items():
        path = root / PDF_DIR / filename
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        sources[filename] = {'relative_path': f'{PDF_DIR}/{filename}', 'sha256': digest,
                             'expected_sha256': expected, 'verified': digest == expected}
        if digest != expected:
            issues.append({'code': 'source_changed' if digest else 'source_missing', 'file': filename,
                           'message': '来源已变化或缺失；撤销已核页码和摘录'})
    def citation(filename, physical, printed, excerpt, excerpt_type='原文摘录（空白和换行整理）'):
        verified = sources.get(filename, {}).get('verified', False) and requirements_unchanged
        return {'source_file': f'{PDF_DIR}/{filename}', 'physical_page': physical if verified else None,
                'printed_page': printed if verified else None, 'excerpt': excerpt if verified else '',
                'excerpt_type': excerpt_type, 'source_sha256': sources.get(filename, {}).get('sha256'),
                'status': 'source_verified' if verified else 'needs_review'}
    topics, indicators = {}, []
    for req in requirements:
        iid, tid = req['指标编号'], req['主题编号']
        topics.setdefault(tid, {'id': tid, 'name': req['主题'], 'goal': req['主题目标'], 'indicator_ids': []})['indicator_ids'].append(iid)
        t_refs = [citation(bid + '_相关资料切片.pdf', physical, printed, excerpt)
                  for bid, physical, printed, excerpt in CITATIONS.get(iid, [])]
        physical, printed = (62, '55') if tid == 'T01' else (63, '56')
        s_ref = citation('课程标准_2022.pdf', physical, printed, STANDARD.get(iid, ''), '课标相关条目节选整理；非全文')
        ok = iid in CITATIONS and requirements_unchanged and s_ref['status'] == 'source_verified' and all(c['status'] == 'source_verified' for c in t_refs)
        indicators.append({'id': iid, 'topic_id': tid, 'description': req['观察指标'],
                           'official_requirement': req, 'question_ids': [m['材料编号'] for m in materials if m['材料类型'] == '题目' and m['指标编号'] == iid],
                           'standard_citation': s_ref, 'textbook_citations': t_refs,
                           'evidence_status': 'source_verified' if ok else 'needs_review'})
    relations = [
        ('T01I1', 'T01I3', '先表达数量关系，再用整式解释问题'),
        ('T01I2', 'T02I1', '整式变形支持方程求解'),
        ('T02I1', 'T02I2', '消元将二元一次方程组转化为一元一次方程'),
        ('T02I1', 'T03I1', '类比等式性质，同时辨别负数乘除时不等号变向'),
        ('T03I1', 'T03I2', '先解各不等式，再取公共部分'),
        ('T03I2', 'T03I3', '把范围约束与情境中的取值限制结合'),
    ]
    ids = {r['指标编号'] for r in requirements}
    return {'schema_version': '1.0', 'topics': list(topics.values()), 'indicators': indicators,
            'materials': materials, 'relations': [{'from': a, 'to': b, 'reason': reason, 'basis_type': 'team_pedagogical_inference'}
                                                 for a, b, reason in relations if a in ids and b in ids],
            'sources': sources, 'issues': issues,
            'scope': '三主题九指标；教材仅定位相关内容，不把整个章节都列入本赛题。source_verified仅表示原文件与已核定位一致。'}
