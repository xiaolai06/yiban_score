"""Launch the local app or a loopback-bound HTTPS reverse-proxy backend."""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description='启动学绩学生管理网站')
    parser.add_argument('--port', type=int, default=8000, help='后端回环端口，默认 8000')
    parser.add_argument('--db', type=Path, help='自定义 SQLite 文件位置')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit('请安装 Python 3.11 或更新版本，建议 3.11–3.13。')
    if not 1024 <= args.port <= 65535:
        raise SystemExit('端口须为 1024–65535。')
    if args.db:
        os.environ['STUDENT_DB_PATH'] = str(args.db.resolve())
    try:
        import uvicorn
        from server.app import create_app
    except ImportError as exc:
        raise SystemExit('依赖尚未安装。请先运行：python -m pip install -r requirements.txt\n' + str(exc)) from exc
    print('\n学绩 · 学生成绩管理 V2\n')
    public_host = os.environ.get('STUDENT_PUBLIC_HOST', '').strip() or None
    try:
        app = create_app(public_host=public_host)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if public_host:
        print(f'HTTPS 入口：https://{app.state.public_host}')
        print('后端仅监听 127.0.0.1；请通过 Nginx 的 IP 白名单和 HTTPS 访问。')
    else:
        print(f'在浏览器打开：http://127.0.0.1:{args.port}')
        print('仅本机访问。')
    print(f'停止服务：Ctrl+C。数据库：{app.state.db_path}\n')
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False,
                proxy_headers=bool(public_host), forwarded_allow_ips='127.0.0.1')

if __name__ == '__main__':
    main()
