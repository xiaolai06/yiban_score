"""Browser checks use the temporary UI server, never the real student database."""
import csv
import io
from pathlib import Path

import pytest

from server.excel_reader import read_workbook
from tests.test_ui_shell import browser as issue_browser
from tests.test_ui_shell import playwright
from tests.test_ui_shell import ui_url as issue_url

browser = issue_browser
ui_url = issue_url
HEADERS = {'X-Requested-With': 'StudentScore'}


def file_payload(rows):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['学号', '姓名', '状态', '得分率'])
    writer.writerows(rows)
    return {'name': '异常测试.csv', 'mimeType': 'text/csv', 'buffer': out.getvalue().encode('utf-8-sig')}


def save_extra(page, url, rows):
    upload = page.request.post(url + '/api/scores/analyze', headers=HEADERS,
                               multipart={'file': file_payload(rows)}).json()
    sheet = upload['sheets'][upload['selected_sheet']]
    response = page.request.post(url + '/api/scores/preview', headers=HEADERS, data={
        'upload_id': upload['upload_id'], 'mapping': sheet['mapping'], 'header_row': sheet['header_row'],
        'sheet_index': upload['selected_sheet'], 'batch_name': '异常增量测试',
    })
    assert response.ok
    p = response.json()
    result = page.request.post(url + '/api/scores/commit', headers=HEADERS,
                               data={'preview_id': p['preview_id'], 'skip_issues': True, 'save_issues_only': True})
    assert result.ok


@pytest.mark.parametrize('width', [1440, 390, 320])
def test_exception_dialog_incremental_records_filters_and_exports(browser, ui_url, tmp_path, width):
    page = browser.new_page(viewport={'width': width, 'height': 900}, accept_downloads=True)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    sid = f'{width:05}'
    second = f'{width + 1:05}'
    malicious_name = '<img src=x onerror=alert(1)>'
    try:
        page.goto(ui_url + '/#score-import')
        page.locator('#score-file').wait_for(state='attached')
        page.locator('#score-file').set_input_files(file_payload([[sid, malicious_name, '已完成', 85],
                                                               [second, '另一位异常学生', '已完成', 70]]))
        page.locator('#score-name').wait_for()
        playwright.expect(page.locator('[data-action="score-preview"]')).to_be_enabled()
        page.locator('[data-action="score-preview"]').click()
        page.locator('#score-preview').wait_for()
        playwright.expect(page.locator('[data-action="score-commit"]')).to_be_disabled()
        page.locator('#score-skip').check()
        playwright.expect(page.locator('[data-action="score-commit"]')).to_contain_text('保存')
        page.locator('[data-action="score-commit"]').click()
        playwright.expect(page.locator('.success-panel h2')).to_contain_text('正式成绩未修改')
        assert page.request.get(ui_url + '/api/stats').json()['students'] == 0
        open_button = page.locator('.heading-actions [data-action="score-issues-open"]')
        open_button.click()
        dialog = page.locator('#score-issues-dialog')
        playwright.expect(dialog).to_be_visible()
        page.locator('#issue-search').fill(sid)
        person = dialog.locator('.issue-dialog-content > .table-scroll tbody tr').filter(has_text=sid)
        playwright.expect(person).to_have_count(1)
        playwright.expect(person.locator('.rate-value')).to_have_text('85%')
        assert dialog.locator('img').count() == 0
        save_extra(page, ui_url, [[sid, malicious_name, '已完成', 95]])
        save_extra(page, ui_url, [[sid, malicious_name, '已完成', 60]])
        dialog.locator('[data-action="issue-refresh"]').click()
        playwright.expect(person.locator('.rate-value')).to_have_text('95%')
        playwright.expect(person).to_contain_text('3 次')
        person.locator('[data-action="issue-person"]').click()
        details = dialog.locator('#issue-detail')
        playwright.expect(details.locator('tbody tr')).to_have_count(3)
        assert details.locator('.issue-original').all_text_contents() == ['60', '95', '85']
        dialog.locator('.issue-toolbar .issue-export summary').click()
        with page.expect_download() as downloaded:
            dialog.locator('.issue-toolbar').get_by_text('人员汇总 · Excel', exact=True).click()
        download = downloaded.value
        assert download.suggested_filename == 'score-issues-summary.xlsx'
        sheet = read_workbook(Path(download.path()).read_bytes(), 'issues.xlsx')[0]
        assert sheet.rows == 2 and sheet.cell(1, 0).value == sid and sheet.cell(1, 4).value == 95
        details.locator('.issue-export summary').click()
        with page.expect_download() as downloaded:
            details.get_by_text('出现明细 · CSV', exact=True).click()
        rows = list(csv.DictReader(io.StringIO(Path(downloaded.value.path()).read_text(encoding='utf-8-sig'))))
        assert len(rows) == 3 and {row['原得分率'] for row in rows} == {'85', '95', '60'}
        save_extra(page, ui_url, [[second, '另一位异常学生', '已完成', 130]])
        dialog.locator('#issue-kind-trigger').click()
        page.get_by_role('option', name='数据错误', exact=True).click()
        playwright.expect(dialog.locator('.empty h3')).to_have_text('暂无异常人员记录')
        page.locator('#issue-search').fill(second)
        playwright.expect(dialog.locator('.issue-dialog-content > .table-scroll tbody tr')).to_have_count(1)
        playwright.expect(dialog.locator('.issue-dialog-content > .table-scroll .rate-value')).to_have_text('70%')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        box = dialog.bounding_box()
        assert box['width'] <= width and box['x'] >= 0
        page.screenshot(path=str(tmp_path / f'exception-dialog-{width}.png'))
        page.keyboard.press('Escape')
        playwright.expect(dialog).not_to_be_visible()
        page.reload()
        page.locator('.heading-actions [data-action="score-issues-open"]').click()
        page.locator('#issue-search').fill(sid)
        playwright.expect(dialog.locator('.issue-dialog-content > .table-scroll .rate-value')).to_have_text('95%')
        assert not errors, errors
    finally:
        page.close()


def test_dialog_read_error_retry_close_and_keyboard_focus(browser, ui_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    try:
        save_extra(page, ui_url, [['70001', '重试异常学生', '已完成', 80]])
        page.goto(ui_url + '/#score-import')
        def fail(route):
            route.fulfill(status=503, content_type='application/json', body='{"detail":"异常记录测试错误"}')
        page.route('**/api/scores/issues?*', fail)
        open_button = page.locator('.heading-actions [data-action="score-issues-open"]')
        open_button.click()
        dialog = page.locator('#score-issues-dialog')
        playwright.expect(dialog.locator('.empty h3')).to_have_text('异常汇总暂时无法读取')
        playwright.expect(dialog.locator('.empty p')).to_have_text('异常记录测试错误')
        page.unroute('**/api/scores/issues?*', fail)
        dialog.locator('[data-action="issue-retry"]').click()
        playwright.expect(dialog.locator('.issue-dialog-content > .table-scroll')).to_be_visible()
        dialog.locator('.dialog-header [data-action="issue-close"]').click()
        playwright.expect(dialog).not_to_be_visible()
        playwright.expect(open_button).to_be_focused()
    finally:
        page.close()


def test_dialog_paginates_people_and_occurrences_and_exports_whole_selection(browser, ui_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 900}, accept_downloads=True)
    try:
        save_extra(page, ui_url, [[f'PAGE{number:03}', f'分页异常{number}', '已完成', 80] for number in range(1, 26)])
        for _ in range(22):
            save_extra(page, ui_url, [['PAGE001', '分页异常1', '已完成', 80]])
        page.goto(ui_url + '/#score-import')
        page.locator('.heading-actions [data-action="score-issues-open"]').click()
        dialog = page.locator('#score-issues-dialog')
        dialog.locator('#issue-search').fill('PAGE')
        rows = dialog.locator('.issue-dialog-content > .table-scroll tbody tr')
        playwright.expect(rows).to_have_count(20)
        dialog.locator('[data-action="issue-list-next"]').click()
        playwright.expect(rows).to_have_count(5)
        dialog.locator('.issue-toolbar .issue-export summary').click()
        with page.expect_download() as downloaded:
            dialog.get_by_text('人员汇总 · Excel', exact=True).click()
        assert read_workbook(Path(downloaded.value.path()).read_bytes(), 'issues.xlsx')[0].rows == 26
        dialog.locator('[data-action="issue-list-prev"]').click()
        rows.filter(has_text='PAGE001').locator('[data-action="issue-person"]').click()
        detail = dialog.locator('#issue-detail')
        playwright.expect(detail.locator('tbody tr')).to_have_count(20)
        detail.locator('[data-action="issue-detail-next"]').click()
        playwright.expect(detail.locator('tbody tr')).to_have_count(3)
        detail.locator('.issue-export summary').click()
        with page.expect_download() as downloaded:
            detail.get_by_text('出现明细 · CSV', exact=True).click()
        exported = list(csv.DictReader(io.StringIO(Path(downloaded.value.path()).read_text(encoding='utf-8-sig'))))
        assert len(exported) == 23
        dialog.locator('#issue-kind-trigger').click()
        page.keyboard.press('Escape')
        playwright.expect(dialog).to_be_visible()
        assert dialog.locator('.control-popover').count() == 0
        page.keyboard.press('Escape')
        playwright.expect(dialog).not_to_be_visible()
    finally:
        page.close()


@pytest.mark.parametrize(('width', 'height'), [(900, 420), (390, 480)])
def test_short_viewport_keeps_all_dialog_content_reachable(browser, ui_url, width, height):
    page = browser.new_page(viewport={'width': width, 'height': height})
    try:
        save_extra(page, ui_url, [[f'LAND{width}', '横屏异常学生', '已完成', 80]])
        page.goto(ui_url + '/#score-import')
        page.locator('.heading-actions [data-action="score-issues-open"]').click()
        dialog = page.locator('#score-issues-dialog')
        dialog.locator('#issue-search').fill('LAND')
        playwright.expect(dialog.locator('.issue-dialog-content > .table-scroll')).to_be_visible()
        box = dialog.bounding_box()
        assert box['height'] <= height and box['y'] >= 0
        background_scroll = page.evaluate('scrollY')
        dialog.locator('.dialog-footer [data-action="issue-close"]').click()
        playwright.expect(dialog).not_to_be_visible()
        assert page.evaluate('scrollY') == background_scroll
    finally:
        page.close()
