"""Optional browser regressions: pip install playwright && playwright install chromium.

All browser writes use a temporary database, never the user's student records.
"""

import csv
import io
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from server.app import create_app

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture(scope='module')
def ui_url(tmp_path_factory):
    root = tmp_path_factory.mktemp('ui-shell')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(
            create_app(root / 'students.sqlite3', testing=True),
            log_level='error', access_log=False,
        ))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.05)
            assert server.started, 'Browser test server did not start'
            yield f'http://127.0.0.1:{listener.getsockname()[1]}'
        finally:
            server.should_exit = True
            thread.join(timeout=10)


@pytest.fixture(scope='module')
def browser():
    with playwright.sync_playwright() as runner:
        try:
            instance = runner.chromium.launch()
        except playwright.Error as exc:
            pytest.skip(f'Chromium is not installed: {exc}')
            raise
        yield instance
        instance.close()


@pytest.mark.parametrize('width', [1920, 1440, 1024, 768, 390, 320])
def test_simplified_shell_and_navigation(browser, ui_url, tmp_path, width):
    page = browser.new_page(viewport={'width': width, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(ui_url)
        page.locator('#main h1').wait_for()
        assert page.locator('.topbar, .sidebar-bottom, .nav-label, .workspace-footer').count() == 0
        assert page.locator('.navigation [data-nav]').count() == 4
        assert page.locator('[data-nav="students"]').count() == 0
        assert page.locator('.navigation').get_by_text('使用说明').count() == 0
        for key, title in [('import', '名单导入'), ('score-import', '导入成绩'),
                           ('scores', '成绩明细'), ('overview', '数据总览')]:
            page.locator(f'[data-nav="{key}"]').click()
            if key == 'scores':
                playwright.expect(page.locator('#main h1')).to_contain_text('全校')
            elif key == 'overview':
                page.locator('.overview-workspace').wait_for()
                assert page.locator('#main h1, #main .page-heading, #main .heading-actions').count() == 0
                assert page.locator('#main a[download], [data-action="refresh-overview"]').count() == 0
                assert page.locator('.overview-grid > .card').count() == 2
                assert page.locator('#main .warm-card, #main .distribution-note').count() == 0
                assert '平均' not in page.locator('#main').inner_text()
                assert page.locator('.metrics .metric').count() == 4
                playwright.expect(page.locator('.metrics .metric-label').last).to_have_text('已完成人数')
            else:
                playwright.expect(page.locator('#main h1')).to_have_text(title)
            page.wait_for_load_state('networkidle')
            playwright.expect(page).to_have_title(f'{title} · 学绩')
            playwright.expect(page.locator('[data-nav][aria-current="page"]')).to_have_attribute('data-nav', key)
            if not page.evaluate('document.documentElement.scrollWidth <= innerWidth'):
                page.screenshot(path=str(tmp_path / f'overflow-{key}-{width}.png'), full_page=True)
                offenders = page.evaluate('''Array.from(document.querySelectorAll('#main *'))
                    .filter(el => el.getBoundingClientRect().right > innerWidth + 1)
                    .map(el => el.tagName + '.' + el.className).slice(0, 12)''')
                pytest.fail(f'{width}px overflow on {key}: {offenders}')
            sidebar = page.locator('.sidebar').bounding_box()
            main = page.locator('#main').bounding_box()
            assert main['x'] >= sidebar['width'], 'Main content overlaps sidebar'
            if key == 'score-import':
                assert page.locator('#main > *').count() == 3
                assert page.locator('#main .rail, #main .requirements, #main .eyebrow-chip').count() == 0
                playwright.expect(page.locator('.score-import-card h2')).to_have_text('导入学生成绩')
                assert page.locator('.score-import-card .card-header .card-subtitle').count() == 0
                assert page.locator('.steps .step > span:last-child').all_text_contents() == ['选择文件', '识别与预览', '确认入库']
                card = page.locator('.score-import-card').bounding_box()
                heading = page.locator('.page-heading').bounding_box()
                assert abs(card['width'] - heading['width']) < 1
                page.screenshot(path=str(tmp_path / f'score-import-{width}.png'), full_page=True)
            if key == 'scores':
                assert page.locator('#main > *').count() == 1
                assert page.locator('.scores-workspace > .card').count() == 2
                assert page.locator('#main > .info-box, .directory-summary, #main .page-heading').count() == 0
                assert page.locator('#main a[download], #main a[href="#score-import"]').count() == 0
                assert page.locator('.students-table th').all_text_contents() == [
                    '', '序号', '学号', '姓名', '班级', '状态', '得分率', '得分', '答题时长', '操作']
                if width > 860:
                    left = page.locator('.organization').bounding_box()
                    right = page.locator('.students-panel').bounding_box()
                    assert abs(left['height'] - right['height']) < 1
                    assert abs(left['y'] - right['y']) < 1
                page.screenshot(path=str(tmp_path / f'scores-{width}.png'), full_page=True)
            if key == 'import':
                assert page.locator('#main > *').count() == 3
                assert page.locator('#main .metrics, #main .rail, #main .requirements').count() == 0
                assert page.locator('#main').get_by_text('真实数据，只在本机').count() == 0
                assert page.locator('#main .info-box').count() == 0
                playwright.expect(page.locator('#main .history h2')).to_have_text('最近导入记录')
                card = page.locator('.roster-import-card').bounding_box()
                heading = page.locator('.page-heading').bounding_box()
                assert abs(card['width'] - heading['width']) < 1, 'Import card should use full content width'
                page.screenshot(path=str(tmp_path / f'roster-import-{width}.png'), full_page=True)
        assert not errors, errors
        page.screenshot(path=str(tmp_path / f'overview-{width}.png'), full_page=True)
    finally:
        page.close()


def test_import_preview_and_student_edit_still_work(browser, ui_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(ui_url)
        page.locator('#file-input').wait_for(state='attached')
        template = Path(__file__).parents[1] / 'web/templates/student_template.xlsx'
        page.locator('#file-input').set_input_files(str(template))
        page.locator('#sheet-select-trigger').wait_for()
        page.locator('[data-action="preview"]').click()
        page.locator('[data-action="commit"]').wait_for()
        assert page.locator('.roster-import-workflow #preview').count() == 1
        page.locator('[data-action="commit"]').click()
        page.locator('.success-panel').wait_for()
        playwright.expect(page.locator('#main .history tbody tr')).not_to_have_count(0)
        page.locator('[data-nav="scores"]').click()
        page.locator('[data-action="edit-student"]').first.click()
        page.locator('#edit-rate').fill('88.5')
        page.locator('#edit-status-trigger').click()
        page.get_by_role('option', name='已完成', exact=True).click()
        page.locator('#save-student').click()
        playwright.expect(page.locator('#student-dialog')).not_to_be_visible()
        playwright.expect(page.locator('.students-table .rate-value').first).to_have_text('88.5%')
        playwright.expect(page.locator('.students-table .score-value').first).to_have_text('88.5分')
        page.reload()
        playwright.expect(page.locator('.students-table .rate-value').first).to_have_text('88.5%')
        playwright.expect(page.locator('.students-table .score-value').first).to_have_text('88.5分')
        page.goto(ui_url + '/#students')
        playwright.expect(page).to_have_url(ui_url + '/#scores')
        playwright.expect(page.locator('#main h1')).to_contain_text('全校')
        assert not errors, errors
    finally:
        page.close()


@pytest.mark.parametrize('width', [1440, 390])
def test_score_import_incremental_workflow_and_saved_history(browser, ui_url, tmp_path, width):
    page = browser.new_page(viewport={'width': width, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    headers = {'X-Requested-With': 'StudentScore'}
    try:
        template = Path(__file__).parents[1] / 'web/templates/student_template.xlsx'
        response = page.request.post(ui_url + '/api/imports/analyze', headers=headers,
                                     multipart={'file': {'name': template.name, 'mimeType': 'application/octet-stream',
                                                         'buffer': template.read_bytes()}})
        assert response.ok
        upload = response.json()
        sheet = upload['sheets'][upload['selected_sheet']]
        response = page.request.post(ui_url + '/api/imports/preview', headers=headers, data={
            'upload_id': upload['upload_id'], 'sheet_index': upload['selected_sheet'],
            'header_row': sheet['header_row'], 'mapping': sheet['mapping'], 'mode': 'update',
        })
        assert response.ok
        assert page.request.post(ui_url + '/api/imports/commit', headers=headers,
                                 data={'preview_id': response.json()['preview_id']}).ok
        students = page.request.get(ui_url + '/api/students').json()['items'][:2]
        assert len(students) == 2
        for student, rate in zip(students, [80, None], strict=True):
            assert page.request.patch(ui_url + '/api/students/' + student['student_id'], headers=headers,
                                      data={'version': student['version'], 'status': '已完成' if rate is not None else '待录入',
                                            'score_rate': rate}).ok
        before_total = page.request.get(ui_url + '/api/scores/batches').json()['total']
        page.goto(ui_url + '/#score-import')
        for index, (rates, expected) in enumerate([([80, 55], [80, 55]), ([40, 20], [80, 55]), ([95, 60], [95, 60])], 1):
            if page.locator('.success-panel').count():
                page.locator('[data-action="score-reset"]').click()
            out = io.StringIO(newline='')
            writer = csv.writer(out)
            writer.writerow(['学号', '姓名', '状态', '得分率'])
            for student, rate in zip(students, rates, strict=True):
                writer.writerow([student['student_id'], student['name'], '已完成', rate])
            page.locator('#score-file').set_input_files({'name': f'测试成绩-{index}.csv',
                                                       'mimeType': 'text/csv', 'buffer': out.getvalue().encode('utf-8-sig')})
            page.locator('#score-name').wait_for()
            assert page.locator('#score-mode, #score-protect').count() == 0
            page.locator('[data-action="score-preview"]').click()
            page.locator('#score-preview').wait_for()
            playwright.expect(page.locator('[data-action="score-commit"]')).to_be_enabled()
            if index == 1:
                page.screenshot(path=str(tmp_path / f'score-preview-{width}.png'), full_page=True)
            with page.expect_response(lambda response: response.url.endswith('/api/scores/commit')) as committed:
                page.locator('[data-action="score-commit"]').click()
            assert committed.value.ok
            assert committed.value.json()['batch']['options']['update_mode'] == 'keep_highest'
            page.locator('.success-panel').wait_for()
            page.wait_for_load_state('networkidle')
            history = page.request.get(ui_url + '/api/scores/batches').json()
            assert history['total'] == before_total + index
            if index == 2:
                assert history['items'][0]['updated_count'] == 0
            for student, rate in zip(students, expected, strict=True):
                assert page.request.get(ui_url + '/api/students/' + student['student_id']).json()['score_rate'] == rate
        page.reload()
        page.locator('#score-history').wait_for()
        playwright.expect(page.locator('.history-tools .small-muted')).to_have_text(f'全部保存 · 共 {before_total + 3} 个批次')
        page.locator('[data-action="score-audit"]').first.click()
        page.locator('#score-audit').wait_for()
        assert page.locator('#score-audit .cleaning-table tbody tr').count() == 2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(tmp_path / f'score-history-{width}.png'), full_page=True)
        assert not errors, errors
    finally:
        page.close()
