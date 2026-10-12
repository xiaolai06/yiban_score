"""Create an isolated environment once, then launch the local application."""
from __future__ import annotations
import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main() -> int:
    if sys.version_info < (3, 11):
        print('Python 3.11+ is required. Recommended: Python 3.11, 3.12 or 3.13.')
        return 1
    env = ROOT / '.venv'
    python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        print('Creating the isolated .venv environment ...', flush=True)
        try:
            venv.EnvBuilder(with_pip=True).create(env)
        except Exception as exc:
            print(f'Environment creation failed: {exc}')
            print('On Ubuntu, install python3-venv; on Windows, install the complete Python distribution.')
            return 1
    signature = hashlib.sha256((ROOT / 'requirements.txt').read_bytes()).hexdigest()
    stamp = env / '.student-score-requirements'
    if not stamp.exists() or stamp.read_text(encoding='utf-8').strip() != signature:
        print('Installing application dependencies. Internet is needed for this first setup only.', flush=True)
        result = subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], cwd=ROOT)
        if result.returncode:
            print('Installation failed. Check the network / pip proxy settings, then start again.')
            return result.returncode
        stamp.write_text(signature, encoding='utf-8')
    try:
        return subprocess.call([str(python), str(ROOT / 'run.py'), *sys.argv[1:]], cwd=ROOT)
    except KeyboardInterrupt:
        return 0

if __name__ == '__main__':
    raise SystemExit(main())
