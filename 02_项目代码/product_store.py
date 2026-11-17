"""Local interaction records, kept separate from official source files."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = ROOT / 'outputs/local_records'
# SHA256 of the sorted six-file digest map, anchored to the original v005 CSVs.
DEFAULT_SOURCE_DIGEST = 'f78f161185190e770cf24d652d8a53c9e3e6dc0c87e92bbf88d52094cc11c7c1'
STORE_LOCK = RLock()
MAX_STORE_BYTES = 16 * 1024 * 1024


def _path(store_path, kind):
    return Path(store_path) if store_path is not None else DEFAULT_STORE / (kind + '.json')


def dataset_store_paths(bundle):
    """Bind records to canonical input location and all six source digests.

    The original official directory retains its pre-existing record files.
    Alternate spellings of one package resolve to the same CSV identity.
    """
    dataset = bundle['dataset']
    directory = Path(dataset['data_dir']).resolve()
    default_directory = ROOT / '现有资料/赛题数据/赛题资料包/4_赛题数据/模拟学情数据'
    sources = {name: source['sha256'] for name, source in dataset['sources'].items()}
    source_digest = hashlib.sha256(json.dumps(sources, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    if directory == default_directory.resolve() and source_digest == DEFAULT_SOURCE_DIGEST:
        folder = DEFAULT_STORE
    else:
        identity = {'directory': os.path.normcase(str(directory)), 'sources': sources}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        folder = DEFAULT_STORE / 'datasets' / digest
    return {kind: folder / (kind + '.json') for kind in ('reviews', 'practice')}


def _validate_record(item, kind):
    identity = ('student_id','node_id','recommendation_id')
    if any(not isinstance(item.get(k),str) or not item[k] for k in identity):
        raise ValueError('本地使用记录损坏：缺少身份字段。')
    if kind == 'reviews':
        for key in ('original_plan','previous_plan','teacher_plan'):
            plan = item.get(key)
            if not isinstance(plan,dict) or any(plan.get(k)!=item[k] for k in identity):
                raise ValueError('本地教师记录损坏：方案身份不一致。')
        required = ('before_sha256','after_sha256','review_id')
        if any(not isinstance(item.get(k),str) or not item[k] for k in required) or not isinstance(item.get('audit'),dict):
            raise ValueError('本地教师记录损坏：审计字段不完整。')
        if any(not isinstance(item['audit'].get(k),str) or not item['audit'][k].strip()
               for k in ('reviewer','reason','reviewed_at_beijing','scope')):
            raise ValueError('本地教师记录损坏：操作者、理由或时间缺失。')
    elif not all(k in item for k in ('practice_id','question_id','answer','process','hint_level','status')):
        raise ValueError('本地练习记录损坏：练习字段不完整。')


def _load(store_path, kind):
    path = _path(store_path, kind)
    if not path.exists():
        return []
    if path.stat().st_size > MAX_STORE_BYTES:
        raise ValueError('本地使用记录过大，请先归档并复核。')
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('本地使用记录损坏，请保留文件并联系维护者。') from error
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError('本地使用记录损坏：应为对象列表。')
    for item in value:
        _validate_record(item,kind)
    return value


def load_reviews(store_path=None):
    with STORE_LOCK:
        return _load(store_path, 'reviews')


def load_practice(store_path=None):
    with STORE_LOCK:
        return _load(store_path, 'practice')


def append_record(record, *, store_path=None, kind='reviews'):
    if kind not in {'reviews', 'practice'}:
        raise ValueError('未知使用记录类型。')
    _validate_record(record,kind)
    with STORE_LOCK:
        path = _path(store_path, kind)
        rows = _load(path, kind)
        content = json.dumps([*rows, record], ensure_ascii=False, allow_nan=False, indent=2)
        if len(content.encode('utf-8')) > MAX_STORE_BYTES:
            raise ValueError('本地使用记录超过容量，请先归档。')
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                prefix=path.stem + '_', suffix='.tmp', delete=False) as handle:
            handle.write(content + '\n')
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.replace(temporary, path)
        except OSError as error:
            raise ValueError('本地记录保存失败；临时文件已保留，请检查目录权限。') from error
        return record
