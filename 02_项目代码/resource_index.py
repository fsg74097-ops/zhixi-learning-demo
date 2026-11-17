"""正式范围课程索引。定位为人工核对后的固定证据，源文件变化自动失效。

课标62/63物理页已目视核验；教材摘录按PDF提取页与印刷页核对。
此模块不将关键词检索视为全书数学语义验证。无需第三方运行依赖。
"""
import csv
import hashlib

from official_data import resolve_data_directory

PDF_DIR = '5_课程标准及相关教学资源'
HASHES = {
    '课程标准_2022.pdf': '1183b95c58a65eaac4c456f2d2b329bbe42f65a6482993edb537e3eaf8baa144',
    'B02_相关资料切片.pdf': 'ccde81c935d7adeaf3c0da331b8881ea86ca0b173e01d20c4ab427d202efc6b6',
    'B05_相关资料切片.pdf': '3f5e0575e5983a35662e705e90230ee5c0c9876b47c98c9da8b9b444027e2e8e',
    'B06_相关资料切片.pdf': 'ccee907e3a43e701d851942d59511c6a7f78c2ee315cc831592b28517168a990',
}
REQ_HASH = '1a2e43dad2d9741e2c30268a56b4ae1d0905fa63e5ced871e138ea921a02cf11'
# 物理页从1起；教材合并切片中上/下册的页码必须分别定位，不能用统一偏移。
CITATIONS = {
    'T01I1': [('B05', 2, '上册69', '工作量＝工作效率×工作时间．'), ('B05', 12, '上册79', '３．２ 代数式的值')],
    'T01I2': [('B05', 29, '上册96', '把多项式中的同类项合并成一项，叫作合并同类项．')],
    'T01I3': [('B05', 31, '上册98', '把进货的数量记为正，售出的数量记为负')],
    'T02I1': [('B06', 7, '上册116', '等式两边加 （或减）同一个数 （或式子），结果仍相等．')],
    'T02I2': [('B06', 45, '下册92', '这种解二元一次方程组的方法叫作代入消元法，简称代入法．'), ('B06', 51, '下册98', '解方程组的基本思想是消元．')],
    'T02I3': [('B06', 5, '上册114', '方程左、右两边的值相等的未知数的值，叫作方程的解'), ('B06', 54, '下册101', '根据两种情况的饲料用量，找出相等关系，列得方程组')],
    'T03I1': [('B02', 6, '下册125', '不等式两边乘 （或除以）同一个负数，不等号的方向改变．')],
    'T03I2': [('B02', 19, '下册138', '不等式组中的各不等式解集的公共部分，就是不等式组中狓的取值范围．')],
    'T03I3': [('B02', 14, '下册133', '通过用不等式表示实际问题中的不等关系，可以把实际问题转化为数学问题')],
}
STANDARD = {
    'T01I1': '能分析具体问题中的简单数量关系，并用代数式表示；会把具体数代入代数式进行计算。',
    'T01I2': '理解整式的概念，掌握合并同类项和去括号的法则。',
    'T01I3': '能分析具体问题中的简单数量关系，并用代数式表示。',
    'T02I1': '掌握等式的基本性质；能解一元一次方程。',
    'T02I2': '掌握消元法，能解二元一次方程组。',
    'T02I3': '能针对具体问题列出方程；能根据具体问题的实际意义，检验方程解的合理性。',
    'T03I1': '探索不等式的基本性质；能解数字系数的一元一次不等式。',
    'T03I2': '会用数轴确定两个一元一次不等式组成的不等式组的解集。',
    'T03I3': '能根据具体问题中的数量关系，列出一元一次不等式，解决简单的问题。',
}


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
        verified = sources[filename]['verified'] and requirements_unchanged
        return {'source_file': f'{PDF_DIR}/{filename}', 'physical_page': physical if verified else None,
                'printed_page': printed if verified else None, 'excerpt': excerpt if verified else '',
                'excerpt_type': excerpt_type, 'source_sha256': sources[filename]['sha256'],
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
