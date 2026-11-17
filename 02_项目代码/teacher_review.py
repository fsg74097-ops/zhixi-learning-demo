"""保留机器建议，另存教师调整与审计依据；不修改事实统计或原始数据。"""
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def review_plan(plan, decision, *, allowed_material_ids):
    if not isinstance(decision, dict):
        raise ValueError('教师调整必须为JSON对象')
    if decision.get('expected_sha256') != fingerprint(plan):
        raise ValueError('建议版本已变化，请重新查看后调整')
    for field in ('reviewer', 'reason', 'guidance', 'follow_up'):
        if not isinstance(decision.get(field), str) or not decision[field].strip():
            raise ValueError(f'教师调整缺少 {field}')
    materials = decision.get('material_ids')
    if not isinstance(materials, list) or not materials or any(
            not isinstance(x, str) or x not in allowed_material_ids for x in materials):
        raise ValueError('材料必须在当前节点允许的范围内')
    if len(materials) != len(set(materials)):
        raise ValueError('材料不可重复')
    revised = copy.deepcopy(plan)
    revised.update({k: copy.deepcopy(decision[k]) for k in ('material_ids', 'guidance', 'follow_up')})
    return {'original_plan': copy.deepcopy(plan), 'teacher_plan': revised,
            'audit': {'reviewer': decision['reviewer'], 'reason': decision['reason'],
                      'time_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(),
                      'before_sha256': fingerprint(plan), 'after_sha256': fingerprint(revised),
                      'scope': '教师调整仅改变学习活动，不改变原始得分与诊断事实。'}}
