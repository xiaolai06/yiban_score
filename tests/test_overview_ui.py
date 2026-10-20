"""Data overview regressions; all writes use a temporary test database."""

import pytest

from tests import test_ui_shell as shell_tests
from tests.test_app import HEADERS, xlsx

browser = shell_tests.browser
ui_url = shell_tests.ui_url
playwright = shell_tests.playwright


@pytest.fixture(scope='module')
def overview_url(ui_url, browser):
    headers = {'X-Requested-With': 'StudentScore'}
    context = browser.new_context()
    try:
        rows = [HEADERS]
        specs = [
            ('测试甲学院', [(100, '已完成'), (90, '已完成'), (None, '待录入'),
                          (0, '已完成'), (59.99, '已完成')], 2),
            ('测试乙学院', [(99, '已完成'), (95, '已完成'), (90, '进行中'),
                          (92, '未开始')], 1),
            ('测试丙学院', [(None, '待录入'), (None, '待录入'), (None, '待录入')], 3),
            ('测试丁学院<svg onload=alert(1)>', [(50, '已完成')], 1),
        ]
        sid = 0
        for college, students, classes in specs:
            for index, (rate, status) in enumerate(students):
                sid += 1
                rows.append([f'{sid:06}', f'虚构学生{sid}', '女', college, '测试专业',
                             f'测试班{index % classes + 1}', status, rate])
        response = context.request.post(ui_url + '/api/imports/analyze', headers=headers,
                                        multipart={'file': {'name': 'overview.xlsx',
                                                            'mimeType': 'application/octet-stream',
                                                            'buffer': xlsx(rows)}})
        assert response.ok
        upload = response.json()
        sheet = upload['sheets'][upload['selected_sheet']]
        response = context.request.post(ui_url + '/api/imports/preview', headers=headers, data={
            'upload_id': upload['upload_id'], 'sheet_index': upload['selected_sheet'],
            'header_row': sheet['header_row'], 'mapping': sheet['mapping'], 'mode': 'update',
        })
        assert response.ok
        assert context.request.post(ui_url + '/api/imports/commit', headers=headers,
                                    data={'preview_id': response.json()['preview_id']}).ok
        yield ui_url
    finally:
        context.close()


@pytest.mark.parametrize('width', [1920, 1440, 1024, 768, 390, 320])
def test_overview_counts_sort_navigation_and_readability(browser, overview_url, tmp_path, width):
    page = browser.new_page(viewport={'width': width, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(overview_url + '/#overview')
        page.locator('.overview-table').wait_for()
        page.wait_for_load_state('networkidle')
        playwright.expect(page).to_have_title('数据总览 · 学绩')
        assert page.locator('.metrics .metric-value').all_text_contents() == ['13', '4', '7', '7']
        assert '平均' not in page.locator('#main').inner_text()
        assert page.locator('#main .page-heading, #main a[download], #main h1').count() == 0
        assert page.locator('.overview-table th').all_text_contents() == [
            '学院名称', '班级数', '学生人数', '已完成人数', '合格人数']
        assert page.locator('.metrics .metric-label').last.inner_text() == '已完成人数'
        assert page.locator('.overview-table tbody td:nth-child(4)').all_text_contents() == ['4', '2', '0', '1']
        assert page.locator('.overview-table .qualified-count').all_text_contents() == ['2', '4', '0', '0']
        assert page.locator('.overview-status .distribution-label strong').all_text_contents() == [
            '4 人', '1 人', '1 人', '7 人', '0 人', '0 人', '0 人']
        assert page.locator('.college-name svg, .rank-square, .college-bar').count() == 0
        assert page.locator('.college-name').first.inner_text() == '测试甲学院'
        assert page.locator('.overview-table th, .overview-table td, .college-name, .metric-value, .metric-note').evaluate_all(
            'elements => elements.every(el => getComputedStyle(el).textAlign === "center")')
        assert page.locator('.overview-table .sort-button').evaluate_all(
            'elements => elements.every(el => getComputedStyle(el).justifyContent === "center")')
        cell = page.locator('.overview-table tbody td').first.bounding_box()
        name = page.locator('.college-name').first.bounding_box()
        assert abs((cell['x'] + cell['width'] / 2) - (name['x'] + name['width'] / 2)) < 1
        assert page.locator('.college-name').first.evaluate('el => getComputedStyle(el).borderWidth') == '0px'
        assert page.locator('.college-name').first.evaluate('el => getComputedStyle(el).backgroundColor') == 'rgba(0, 0, 0, 0)'
        overview_scrollbar = page.locator('.overview-table-scroll').evaluate('el => getComputedStyle(el).scrollbarColor')
        overview_border = page.locator('.overview-colleges').evaluate('el => getComputedStyle(el).borderColor')
        overview_radius = page.locator('.overview-colleges').evaluate('el => getComputedStyle(el).borderRadius')
        assert page.locator('.overview-colleges .card-header').evaluate('el => getComputedStyle(el).borderBottomWidth') == '0px'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('.metric-label').first.evaluate('el => parseFloat(getComputedStyle(el).fontSize)') >= 15
        assert page.locator('.college-name').first.evaluate('el => parseFloat(getComputedStyle(el).fontSize)') == 16
        if width > 1100:
            left = page.locator('.overview-colleges').bounding_box()
            right = page.locator('.overview-status').bounding_box()
            assert abs(left['y'] - right['y']) < 1
            assert right['x'] >= left['x'] + left['width']
        else:
            left = page.locator('.overview-colleges').bounding_box()
            right = page.locator('.overview-status').bounding_box()
            assert right['y'] >= left['y'] + left['height']
        page.screenshot(path=str(tmp_path / f'overview-populated-{width}.png'), full_page=True)

        for key, column in [('students', 2), ('classes', 1), ('completed', 3), ('qualified', 4)]:
            button = page.locator(f'#overview-sort-{key}')
            button.click()
            th = page.locator('.overview-table th').nth(column)
            order = th.get_attribute('aria-sort')
            assert order in ['ascending', 'descending']
            values = [int(value) for value in page.locator(
                f'.overview-table tbody td:nth-child({column + 1})').all_text_contents()]
            assert values == sorted(values, reverse=order == 'descending')
            scroll_left = page.locator('.overview-table-scroll').evaluate('el => el.scrollLeft')
            button.press('Enter')
            playwright.expect(th).to_have_attribute('aria-sort', 'descending' if order == 'ascending' else 'ascending')
            assert page.locator('.overview-table-scroll').evaluate('el => el.scrollLeft') == scroll_left
            assert page.evaluate('document.activeElement.id') == f'overview-sort-{key}'
            flipped = [int(value) for value in page.locator(
                f'.overview-table tbody td:nth-child({column + 1})').all_text_contents()]
            assert flipped == list(reversed(values))
        page.locator('#overview-sort-name').click()
        playwright.expect(page.locator('.overview-table th').first).to_have_attribute('aria-sort', 'ascending')
        names = page.locator('.college-name > span:last-of-type').all_text_contents()
        assert page.evaluate('(names) => names.every((name, i) => !i || names[i-1].localeCompare(name, "zh-CN") <= 0)', names)
        page.locator('#overview-sort-name').click()
        assert page.locator('.college-name > span:last-of-type').all_text_contents() == list(reversed(names))
        page.locator('#overview-sort-qualified').click()
        page.locator('.college-name').first.click()
        playwright.expect(page).to_have_url(overview_url + '/#scores')
        playwright.expect(page.locator('#main h1')).to_contain_text('测试乙学院')
        page.wait_for_load_state('networkidle')
        playwright.expect(page.locator('.students-table tbody tr')).to_have_count(4)
        assert page.locator('.score-table-scroll').evaluate('el => getComputedStyle(el).scrollbarColor') == overview_scrollbar
        assert page.locator('.students-panel').evaluate('el => getComputedStyle(el).borderColor') == overview_border
        assert page.locator('.students-panel').evaluate('el => getComputedStyle(el).borderRadius') == overview_radius
        page.locator('[data-nav="overview"]').click()
        page.wait_for_load_state('networkidle')
        playwright.expect(page.locator('#overview-sort-qualified').locator('..')).to_have_attribute('aria-sort', 'descending')
        assert page.locator('.college-name > span:last-of-type').first.inner_text() == '测试乙学院'
        assert not errors, errors
    finally:
        page.close()


def test_overview_refreshes_counts_after_edit(browser, overview_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    try:
        page.goto(overview_url + '/#overview')
        page.locator('.overview-table').wait_for()
        student = page.request.get(overview_url + '/api/students/000002').json()
        response = page.request.patch(overview_url + '/api/students/000002',
                                      headers={'X-Requested-With': 'StudentScore'}, data={
                                          'version': student['version'], 'status': '已完成', 'score_rate': None,
                                      })
        assert response.ok
        page.locator('[data-nav="import"]').click()
        page.locator('[data-nav="overview"]').click()
        playwright.expect(page.locator('.metrics .metric-value').last).to_have_text('6')
        row = page.locator('.overview-table tbody tr').filter(has_text='测试甲学院')
        playwright.expect(row.locator('.qualified-count')).to_have_text('1')
        page.reload()
        playwright.expect(page.locator('.metrics .metric-value').last).to_have_text('6')
    finally:
        page.close()
