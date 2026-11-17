"""Export the same official facts and effective plans used by the product."""
import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile

from product_service import student_view
from workflow import FIELD_GUIDE


def _json(value):
    return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')


def _indicators_csv(bundle):
    buffer = io.StringIO(newline='')
    fields = ['node_id','student_id','class_id','indicator_id','score','max_score','scored_events','score_rate','status','evidence_ids']
    writer = csv.DictWriter(buffer,fieldnames=fields)
    writer.writeheader()
    for nid,node in bundle['analysis']['nodes'].items():
        for sid,student in node['students'].items():
            for iid,cell in student['indicators'].items():
                writer.writerow({'node_id':nid,'student_id':sid,'class_id':student['class_id'],'indicator_id':iid,
                                 **{key:cell.get(key) for key in fields[4:-1]},'evidence_ids':'、'.join(cell['evidence_ids'])})
    return ('\ufeff'+buffer.getvalue()).encode('utf-8')


def export_product(bundle,reviews=(),practice=()):
    effective = {nid:{sid:student_view(bundle,sid,nid,reviews) for sid in node['students']}
                 for nid,node in bundle['analysis']['nodes'].items()}
    first_nid = 'N02' if 'N02' in effective else next(iter(effective))
    first_sid = 'D01' if 'D01' in effective[first_nid] else next(iter(effective[first_nid]))
    sample = {'note':'仅用于本次大赛内部复核；不含真实学生身份',
              'input':{'student_id':first_sid,'node_id':first_nid,
                       'records':[r for r in bundle['dataset']['raw_records']
                                  if r['学生编号']==first_sid and r['节点编号']==first_nid]},
              'output':effective[first_nid][first_sid]}
    artifacts = {'curriculum.json':_json(bundle['curriculum']), 'analysis.json':_json(bundle['analysis']),
                 'data_quality.json':_json({'summary':bundle['dataset']['summary'],
                                           'issues':bundle['dataset']['issues'],
                                           'record_meta':bundle['dataset']['record_meta']}),
                 'evidence_records.json':_json(bundle['dataset']['raw_records']),
                 'effective_plans.json':_json(effective), 'teacher_reviews.json':_json(list(reviews)),
                 'learning_records.json':_json(list(practice)), 'trace.json':_json(bundle['trace']),
                 'model_explanation.json':_json(bundle['model']),
                 'input_output_sample.json':_json(sample),'student_indicators.csv':_indicators_csv(bundle)}
    explanation = ('\n## 产品新增字段\n\neffective_plans.json按节点/学生组织，是教师和学生共同使用的有效方案；'
                   '隐藏参考答案。analysis.json保留机器原分析。teacher_reviews含调整前后方案、理由、时间和摘要；'
                   'learning_records是本机新增待评练习，不改变正式分数。input_output_sample含一名学生的对应输入与输出。'
                   '本机视角不是账号授权系统。\n')
    artifacts['字段与操作说明.md'] = (FIELD_GUIDE+explanation).encode('utf-8')
    artifacts['manifest.json'] = _json({'schema_version':'2.0','scope':'本次大赛内部使用',
        'data_sources':bundle['dataset']['sources'],
        'code_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(Path(__file__).parent.glob('*.py')) if not p.name.startswith('test_')},
        'artifact_sha256':{name:hashlib.sha256(value).hexdigest() for name,value in artifacts.items()},
        'limits':['教师教学适切性需人工复核','模拟数据不能证明学习效果','真实API状态见model_explanation.json']})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,value in artifacts.items():
            archive.writestr(name,value)
    return buffer.getvalue()
