"""治理输入登记与稳定序列化。原始输入只读，标准化文件另存。"""
import csv
import hashlib
import io
import json
from pathlib import Path

from official_data import SCHEMA, resolve_data_directory


class InputBlocked(ValueError):
    def __init__(self, message, sources=None):
        super().__init__(message)
        self.sources = sources or {}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_identity(sources):
    value = {'rules_version': 'v1.1', 'source_sha256': {name: item['sha256'] for name, item in sources.items()}}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode('utf-8')).hexdigest()


def normalize_text(value):
    """仅安全字符形式与首尾空白，不消除数学符号、单位或内部分隔。"""
    fullwidth = {chr(code): chr(code-65248) for code in range(65281, 65375)}
    fullwidth.update({'\u3000': ' ', '−': '-', '﹣': '-', '×': '*', '÷': '/'})
    return value.translate(str.maketrans(fullwidth)).replace('\r\n', '\n').strip()


def read_csv(path, role='OFFICIAL_RAW'):
    path = Path(path).resolve()
    content = path.read_bytes()
    encoding = 'utf-8-sig'
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError:
        encoding = 'gb18030'
        text = content.decode(encoding)
    reader = csv.DictReader(io.StringIO(text, newline=''))
    fields = reader.fieldnames or []
    rows = list(reader)
    info = {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest(), 'role': role,
            'encoding': encoding, 'rows': len(rows), 'columns': fields, 'column_count': len(fields)}
    if not fields or len(fields) != len(set(fields)):
        raise InputBlocked('CSV表头缺失或重复', {path.name: info})
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise InputBlocked('CSV逻辑行列数与表头不一致', {path.name: info})
    return rows, info


def read_official(path):
    folder = resolve_data_directory(path)
    tables, sources = {}, {}
    for key, (name, required) in SCHEMA.items():
        file = folder/(name+'.csv')
        try:
            rows, info = read_csv(file)
        except (OSError, UnicodeError) as error:
            raise InputBlocked(f'必需输入无法读取：{name}.csv', sources) from error
        sources = {**sources, file.name: info}
        if not set(required.split()) <= set(info['columns']):
            raise InputBlocked(f'核心字段缺失：{name}.csv', sources)
        tables = {**tables, key: rows}
    if any(not normalize_text(row['模拟班级编号']) or not normalize_text(row['年级']).isdigit()
           or int(normalize_text(row['年级'])) <= 0 for row in tables['students']):
        raise InputBlocked('学生年级应为正整数，班级编号不能为空', sources)
    return folder, tables, sources


def _cell(value):
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if value is None:
        return ''
    return value


def write_csv(path, rows, fields=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = fields or list(dict.fromkeys(key for row in rows for key in row))
    if not keys:
        keys = ['status']
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, lineterminator='\n')
        writer.writeheader()
        writer.writerows({key: _cell(row.get(key)) for key in keys} for row in rows)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                                    allow_nan=False)+'\n', encoding='utf-8')


def stage_normalized(tables, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    normalized = {key: [{field: normalize_text(value).replace(';', '；') if field == '评分点得分'
                        else normalize_text(value) for field, value in row.items()} for row in rows]
                  for key, rows in tables.items()}
    for key, rows in normalized.items():
        fields = list(tables[key][0]) if tables[key] else SCHEMA[key][1].split()
        write_csv(folder/(SCHEMA[key][0]+'.csv'), rows, fields)
    return normalized
