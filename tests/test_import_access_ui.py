"""Password-gated import views, exercised only against temporary databases."""
import json
import os
import re
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import uvicorn

from server.app import create_app
from server.import_access import password_hash
from tests.test_app import HEADERS, row, xlsx
from tests.test_ui_shell import browser as gate_browser
from tests.test_ui_shell import playwright

browser = gate_browser

PASSWORD = 'Synthetic-browser-password-2026!'


@pytest.fixture(scope='module')
def access_url(tmp_path_factory):
    root = tmp_path_factory.mktemp('password-gate')
    config = root / 'access.json'
    config.write_text(json.dumps({'password_hash': password_hash(PASSWORD)}))
    with patch.dict(os.environ, {'STUDENT_IMPORT_AUTH_FILE': str(config)}):
        app = create_app(root / 'test.sqlite3', testing=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(.05)
            assert server.started
            yield f'http://127.0.0.1:{listener.getsockname()[1]}'
        finally:
            server.should_exit = True
            thread.join(timeout=10)


@pytest.mark.parametrize('width', [1440, 390])
def test_import_gate_public_navigation_unlock_upload_and_lock(browser, access_url, tmp_path, width):
    page = browser.new_page(viewport={'width': width, 'height': 950})
    errors = []
    console_errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: console_errors.append(message.text)
            if message.type in {'error', 'warning'} else None)
    try:
        page.goto(access_url)
        page.locator('.overview-workspace').wait_for()
        assert '验证导入密码' not in page.locator('#main').inner_text()
        page.locator('[data-nav="import"]').click()
        page.get_by_label('导入密码', exact=True).wait_for()
        playwright.expect(page).to_have_title('名单导入 · 学绩')
        assert page.locator('input[type=file]').count() == 0
        assert page.request.post(access_url + '/api/imports/analyze',
                                 headers={'X-Requested-With': 'StudentScore'}).status == 401
        page.get_by_label('导入密码', exact=True).fill('wrong-password')
        page.get_by_role('button', name='验证并进入').click()
        playwright.expect(page.locator('[role=alert]')).to_contain_text('密码不正确')
        assert page.locator('input[type=file]').count() == 0
        page.get_by_label('导入密码', exact=True).fill(PASSWORD)
        page.get_by_role('button', name='验证并进入').click()
        page.get_by_role('button', name='选择学生名单', exact=True).wait_for()
        assert page.get_by_label('导入密码', exact=True).count() == 0
        page.locator('#file-input').set_input_files({
            'name': '合成验证名单.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'buffer': xlsx([HEADERS, row(f'AUTH{width}', name='验证合成学生')]),
        })
        page.get_by_role('button', name='生成导入预览', exact=True).click()
        page.get_by_role('button', name=re.compile('确认导入 1 条记录')).wait_for()
        page.get_by_role('button', name=re.compile('确认导入 1 条记录')).click()
        page.get_by_role('heading', name='学生名单已写入数据库').wait_for()
        page.locator('[data-nav="score-import"]').click()
        page.get_by_role('button', name='退出导入验证').wait_for()
        assert page.get_by_label('导入密码', exact=True).count() == 0
        assert page.locator('input[type=file]').count() > 0
        page.get_by_role('button', name='退出导入验证').click()
        page.get_by_label('导入密码', exact=True).wait_for()
        page.reload()
        page.get_by_label('导入密码', exact=True).wait_for()
        assert not page.request.get(access_url + '/api/import-access').json()['unlocked']
        page.locator('[data-nav="import"]').click()
        playwright.expect(page.locator('#main h1')).to_have_text('名单导入')
        page.get_by_label('导入密码', exact=True).wait_for()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        screenshot_dir = Path(os.environ.get('IMPORT_ACCESS_SCREENSHOT_DIR', tmp_path))
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_dir / f'import-password-{width}.png'), full_page=True)
        page.locator('[data-nav="scores"]').click()
        page.locator('.score-table-scroll').wait_for()
        page.locator('[data-nav="overview"]').click()
        page.locator('.overview-workspace').wait_for()
        assert not errors
        # The intentionally wrong password produces one expected HTTP 401 console message.
        assert not [message for message in console_errors if '401' not in message]
    finally:
        page.close()
