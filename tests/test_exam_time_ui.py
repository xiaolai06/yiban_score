"""Real browser import, duration rendering, history, and downloads on temporary data."""
import csv
import io

import pytest

from server.excel_reader import read_workbook
from tests.test_app import HEADERS, row
from tests.test_roster_removal_ui import seed, upload_payload
from tests.test_ui_shell import browser as time_browser
from tests.test_ui_shell import playwright
from tests.test_ui_shell import ui_url as time_url

browser = time_browser
ui_url = time_url


@pytest.mark.parametrize('width', [1440, 390, 320])
def test_duration_import_all_score_tables_and_actual_downloads(browser, ui_url, tmp_path, width):
    page = browser.new_page(viewport={'width':width, 'height':1000}, accept_downloads=True)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    prefix = f'DURATION{width}-'
    try:
        seed(page, ui_url, [row(prefix+str(i), name=f'合成时长{i}', cls=f'时长{width}班') for i in range(4)])
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['学号','状态','得分率','开始时间','结束时间','系统评定时间'])
        pairs = [('2026-10-10 11:02:15','2026-10-10 11:15:08'),
                 ('2026-10-10 11:02:15','2026-10-10 11:02:15'), ('',''),
                 ('2026-10-10 11:02:15','2026-10-12 12:02:16')]
        for i, (start, end) in enumerate(pairs):
            writer.writerow([prefix+str(i), '已完成', 59, start, end, '2026-10-20 23:59:59'])
        page.goto(ui_url+'/#score-import')
        page.locator('#score-file').set_input_files({'name':'合成答题时长.csv', 'mimeType':'text/csv',
                                                     'buffer':out.getvalue().encode('utf-8-sig')})
        page.locator('#score-map-exam_started_at-trigger').wait_for()
        playwright.expect(page.locator('#score-map-exam_started_at-trigger')).to_contain_text('开始时间')
        playwright.expect(page.locator('#score-map-exam_finished_at-trigger')).to_contain_text('结束时间')
        page.locator('[data-action="score-preview"]').click()
        page.locator('#score-preview').wait_for()
        preview = page.locator('#score-preview .cleaning-table .duration-cell')
        playwright.expect(preview.nth(0)).to_contain_text('00:12:53')
        playwright.expect(preview.nth(1)).to_contain_text('00:00:00')
        playwright.expect(preview.nth(2)).to_contain_text('—')
        playwright.expect(preview.nth(3)).to_contain_text('49:00:01')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('[data-action="score-commit"]').click()
        page.locator('.success-panel').wait_for()
        page.locator('[data-action="score-audit"]').first.click()
        playwright.expect(page.locator('#score-audit .duration-cell').first).to_contain_text('00:12:53')
        page.locator('[data-nav="scores"]').click()
        page.locator('#student-search').fill(prefix)
        playwright.expect(page.locator('#score-pagination')).to_contain_text('共 4 条')
        cells = page.locator('.students-table .duration-value')
        playwright.expect(cells).to_have_text(['00:12:53','00:00:00','—','49:00:01'])
        playwright.expect(cells.first.locator('..')).to_have_attribute('title',
            '开始：2026-10-10 11:02:15；结束：2026-10-10 11:15:08；答题时长＝结束时间－开始时间（时:分:秒）')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        if width >= 1440:
            assert page.locator('.score-table-scroll').evaluate('(el) => el.scrollWidth<=el.clientWidth'), page.evaluate('''() => ({
                area:document.querySelector('.score-table-scroll').clientWidth,
                widths:[...document.querySelectorAll('.students-table th')].map(el=>[el.textContent,el.getBoundingClientRect().width])
            })''')
            duration_box = cells.first.bounding_box()
            edit_box = page.locator('.students-table tbody tr').first.locator('td').last.bounding_box()
            assert duration_box['x'] + duration_box['width'] <= edit_box['x']
        page.screenshot(path=str(tmp_path/f'duration-scores-{width}.png'), full_page=True)
        page.locator('[data-action="edit-student"]').first.click()
        playwright.expect(page.locator('.exam-time-detail')).to_contain_text('答题时长：00:12:53')
        playwright.expect(page.locator('.exam-time-detail')).to_contain_text('开始：2026-10-10 11:02:15')
        page.locator('#student-dialog [data-action="close-dialog"]').first.click()
        page.locator('[data-action="check-all-students"]').click()
        page.locator('#score-list-tools .export-menu summary').click()
        with page.expect_download() as download:
            page.locator('[data-action="score-export-students"]').click()
        path = tmp_path/f'duration-{width}.xlsx'
        download.value.save_as(path)
        book = read_workbook(path.read_bytes(), path.name)
        assert book[0].cell(0,9).text() == '答题时长'
        assert [book[0].cell(i,9).text() for i in range(1,5)] == ['00:12:53','00:00:00','','49:00:01']
        page.locator('[data-action="follow-open"]').click()
        playwright.expect(page.locator('#follow-content .duration-cell')).to_have_text(['00:12:53','00:00:00','—','49:00:01'])
        with page.expect_download() as follow_download:
            page.locator('[data-action="follow-export"][data-format="csv"]').click()
        follow_path = tmp_path/f'follow-duration-{width}.csv'
        follow_download.value.save_as(follow_path)
        with follow_path.open(encoding='utf-8-sig', newline='') as source:
            assert [record['答题时长'] for record in csv.DictReader(source)] == ['00:12:53','00:00:00','','49:00:01']
        page.locator('[data-action="follow-close"]').first.click()
        page.locator('[data-nav="overview"]').click()
        page.locator('.overview-workspace').wait_for()
        assert page.locator('.overview-workspace').get_by_text('答题时长').count() == 0
        page.locator('[data-nav="import"]').click()
        page.locator('#file-input').set_input_files(upload_payload([HEADERS, row(prefix+'0', name='合成时长0')]))
        page.locator('[data-action="preview"]').click()
        playwright.expect(page.locator('#preview .duration-cell')).to_have_text('00:12:53')
        page.locator('[data-operation="remove"]').click()
        page.locator('#removal-file-input').set_input_files(upload_payload([['学号'], [prefix+'0']]))
        page.locator('[data-action="removal-preview"]').click()
        playwright.expect(page.locator('#removal-preview .duration-cell')).to_have_text('00:12:53')
        assert page.request.get(ui_url+f'/api/students/{prefix}0').ok
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors, errors
    finally:
        page.close()


def test_previous_workbook_version_downloads_with_explicit_compat_notice(browser, ui_url, tmp_path):
    page = browser.new_page(accept_downloads=True)
    try:
        seed(page, ui_url, [row('DURATION-GUARD', rate=92)])
        page.goto(ui_url+'/#scores')
        playwright.expect(page.locator('.score-table-scroll')).to_have_attribute('aria-busy','false')
        def old_response(route):
            response = route.fetch()
            route.fulfill(response=response, headers={**response.headers, 'x-export-workbook-version':'5'})
        page.route('**/api/students/export.xlsx', old_response)
        page.locator('#score-list-tools .export-menu summary').click()
        with page.expect_download() as result:
            page.locator('[data-action="score-export"]').click()
        result.value.save_as(tmp_path/'compatible.xlsx')
        assert read_workbook((tmp_path/'compatible.xlsx').read_bytes(),'compatible.xlsx')
        playwright.expect(page.locator('#toast')).to_contain_text('兼容旧版格式，不含答题时间')
        playwright.expect(page.locator('#toast')).not_to_have_class('error')
    finally:
        page.close()
