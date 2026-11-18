"""G0机器路径适配。数据口径及业务逻辑由原模块维护。"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def data_root(root=None):
    workspace = ROOT if root is None else Path(root)
    config = workspace/'config/paths.local.json'
    if not config.is_file():
        return workspace/'现有资料/赛题数据/赛题资料包'
    try:
        settings = json.loads(config.read_text(encoding='utf-8-sig'))
        if not isinstance(settings, dict) or set(settings) != {'data_root'}:
            raise ValueError('路径配置字段不符')
        value = settings['data_root']
        if not isinstance(value, str) or not value.strip() or '\x00' in value:
            raise ValueError('路径配置data_root必须为非空文本')
        path = Path(value)
        return (path if path.is_absolute() else workspace/path).resolve()
    except (OSError, ValueError) as error:
        raise ValueError('路径配置无法读取，请检查config/paths.local.json。') from error
