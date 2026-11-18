"""Run the rebuilt workspace from the app root with one verified startup path."""
import argparse
from getpass import getpass
import os
from pathlib import Path
import sys

import uvicorn

SOURCE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description='启动智析学伴重做版')
    parser.add_argument('--port', type=int, default=8510)
    parser.add_argument('--demo', action='store_true', help='使用独立自建数据和演示记录')
    parser.add_argument('--model', action='store_true', help='在需要时隐藏输入 DeepSeek 密钥')
    options = parser.parse_args()
    if not 1 <= options.port <= 65535:
        parser.error('端口必须在 1 至 65535 之间')
    if not (SOURCE / 'web/dist/index.html').is_file():
        parser.error('缺少 web/dist，请先在 web 目录依次运行 npm ci 和 npm run build')
    if options.model and not os.environ.get('DEEPSEEK_API_KEY'):
        key = getpass('DeepSeek API Key（隐藏输入，直接回车可使用本地核验）: ').strip()
        if key:
            os.environ['DEEPSEEK_API_KEY'] = key
        key = None
    os.environ['PUBLIC_DEMO_MODE'] = '1' if options.demo else '0'
    sys.path.insert(0, str(SOURCE / '02_项目代码'))
    uvicorn.run('http_api:app', host='127.0.0.1', port=options.port)


if __name__ == '__main__':
    main()
