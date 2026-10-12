"""Score-directory browser regressions; all data is synthetic and temporary."""

import math
import re
import socket
import threading
import time

import pytest
import uvicorn

from server.app import create_app
from server.database import connection
from server.excel_reader import read_workbook

playwright = pytest.importorskip('playwright.sync_api')


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


@pytest.fixture(scope='module')
def directory_url(tmp_path_factory):
    db = tmp_path_factory.mktemp('score-directory') / 'students.sqlite3'
    app = create_app(db, testing=True)
    with connection(db) as con:
        con.execute("INSERT INTO colleges(id,name) VALUES (1,'测试学院甲'),(2,'测试学院乙')")
        con.execute("INSERT INTO classes(id,college_id,name) VALUES (1,1,'测试一班'),(2,1,'测试二班'),(3,2,'测试三班')")
        for i in range(1, 36):
            status, rate = '已完成', 86.0
            if i == 2:
                rate = 0.0
            elif i == 3:
                status, rate = '待录入', None
            elif i == 4:
                status, rate = '未开始', 0.0
            elif i == 5:
                status, rate = '进行中', 20.0
            elif i == 6:
                status, rate = '已完成', None
            elif i == 7:
                rate = 86.75
            class_id = 1 if i <= 15 else 2 if i <= 30 else 3
            # HTML-looking source data must stay text, not executable markup.
            name = '<img src=x onerror=alert(1)>' if i == 8 else f'测试学生{i:02}'
            con.execute('INSERT INTO students(student_id,name,class_id,status,score_rate) VALUES (?,?,?,?,?)',
                        (f'202600{i:04}', name, class_id, status, rate))
        con.commit()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.05)
            assert server.started
            yield f'http://127.0.0.1:{listener.getsockname()[1]}'
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def clear_directory_filters(page):
    page.locator('#student-search').fill('')
    for key, label in [('status-filter', '全部状态'), ('score-filter', '全部得分率')]:
        page.locator(f'#{key}-trigger').click()
        page.get_by_role('option', name=label, exact=True).click()
        page.wait_for_load_state('networkidle')


@pytest.mark.parametrize('width', [1920, 1440, 1024, 768, 390, 320])
def test_populated_directory_layout(browser, directory_url, tmp_path, width):
    page = browser.new_page(viewport={'width': width, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(directory_url + '/#scores')
        rows = page.locator('.students-table tbody tr')
        page.wait_for_load_state('networkidle')
        match = re.search(r'(\d+) 条', page.locator('#page-size-trigger').inner_text())
        assert match
        capacity = int(match.group(1))
        pages = math.ceil(35 / capacity)
        playwright.expect(rows).to_have_count(capacity)
        playwright.expect(rows.nth(0).locator('.score-value')).to_have_text('86分')
        playwright.expect(rows.nth(1).locator('.score-value')).to_have_text('0分')
        playwright.expect(rows.nth(2).locator('.score-empty')).to_have_text('—')
        if capacity >= 7:
            playwright.expect(rows.nth(6).locator('.score-value')).to_have_text('86.75分')
        assert page.locator('#main select:visible').count() == 0
        assert page.locator('#status-filter-trigger, #score-filter-trigger, #page-size-trigger').count() == 3
        assert page.locator('.student-cell img').count() == 0
        assert page.locator('#main > *').count() == 1
        assert page.locator('.scores-workspace > .card').count() == 2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        left = page.locator('.organization').bounding_box()
        right = page.locator('.students-panel').bounding_box()
        if width > 860:
            assert abs(left['height'] - right['height']) < 1
            assert abs(left['y'] - right['y']) < 1
            assert abs(right['y'] - (900 - right['y'] - right['height'])) < 1
        if width >= 1440:
            assert page.locator('.score-table-scroll').evaluate('(el) => el.scrollWidth <= el.clientWidth')
            last_row = rows.last.bounding_box()
            footer = page.locator('.pagination').bounding_box()
            assert capacity > 8
            assert last_row['y'] + last_row['height'] <= footer['y'] + 1
            assert footer['y'] - last_row['y'] - last_row['height'] < 54
        page.screenshot(path=str(tmp_path / f'populated-scores-{width}.png'), full_page=True)
        # Last page has fewer rows, but the panel and footer must not collapse.
        page.locator('#page-jump').fill(str(pages))
        page.locator('#page-jump').press('Enter')
        playwright.expect(page.locator('.page-number')).to_have_text(f'{pages} / {pages}')
        playwright.expect(rows).to_have_count(35 - (pages - 1) * capacity)
        assert abs(page.locator('.students-panel').bounding_box()['height'] - right['height']) < 1
        playwright.expect(page.locator('[data-action="next-page"]')).to_be_disabled()
        page.locator('[data-action="prev-page"]').click()
        playwright.expect(page.locator('.page-number')).to_have_text(f'{pages - 1} / {pages}')
        assert not errors, errors
    finally:
        page.close()


def test_selection_paging_filters_and_averages(browser, directory_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    try:
        page.goto(directory_url + '/#scores')
        page.wait_for_load_state('networkidle')
        pages = int(page.locator('#page-jump').get_attribute('max'))
        qualified = page.locator('[data-stat="qualified"]')
        settled = page.locator('[data-stat="settled"]')
        average = page.locator('[data-stat="average"]')
        playwright.expect(page.locator('h1 .count-pill')).to_have_text('总人数 35 人')
        playwright.expect(qualified).to_have_text('0人')
        playwright.expect(settled).to_have_text('31人')
        playwright.expect(average).to_have_text('83.25%')
        page.locator('#page-jump').fill('2')
        page.locator('#page-jump-form button').click()
        playwright.expect(page.locator('.page-number')).to_have_text(f'2 / {pages}')
        for invalid in ['0', str(pages + 1), '1.5']:
            page.locator('#page-jump').fill(invalid)
            page.locator('#page-jump-form button').click()
            assert not page.locator('#page-jump').evaluate('(el) => el.validity.valid')
            playwright.expect(page.locator('.page-number')).to_have_text(f'2 / {pages}')
        page.locator('[data-action="select-college"][data-id="1"]').click()
        playwright.expect(page.locator('h1 .count-pill')).to_have_text('总人数 30 人')
        page.locator('[data-action="select-class"][data-id="1"]').click()
        playwright.expect(page.locator('h1 .count-pill')).to_have_text('总人数 15 人')
        playwright.expect(settled).to_have_text('11人')
        playwright.expect(average).to_have_text('78.25%')
        page.locator('#status-filter-trigger').click()
        page.get_by_role('option', name='未开始', exact=True).click()
        playwright.expect(settled).to_have_text('0人')
        playwright.expect(average).to_have_text('—')
        playwright.expect(page.locator('.students-table tbody tr')).to_have_count(1)
        clear_directory_filters(page)
        playwright.expect(settled).to_have_text('11人')
        playwright.expect(page.locator('.tree-class.selected')).to_contain_text('测试一班')
        page.locator('#page-size-trigger').click()
        page.get_by_role('option', name='20 条 / 页', exact=True).click()
        playwright.expect(page.locator('.students-table tbody tr')).to_have_count(15)
        page.locator('#student-search').fill('不存在的学生')
        playwright.expect(page.locator('.empty h3')).to_have_text('没有符合条件的学生')
        playwright.expect(page.locator('.page-number')).to_have_text('1 / 1')
        playwright.expect(page.locator('[data-action="prev-page"]')).to_be_disabled()
        playwright.expect(page.locator('[data-action="next-page"]')).to_be_disabled()
        page.locator('#student-search').fill('测试学生01')
        playwright.expect(page.locator('.students-table tbody tr')).to_have_count(1)
        playwright.expect(page.locator('.student-cell')).to_have_text('测试学生01')
    finally:
        page.close()


def test_score_read_failure_can_retry(browser, directory_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    try:
        def fail(route):
            route.fulfill(status=503, content_type='application/json', body='{"detail":"测试读取失败"}')
        page.route('**/api/students?*', fail)
        page.goto(directory_url + '/#scores')
        playwright.expect(page.locator('.empty h3')).to_have_text('成绩读取失败')
        playwright.expect(page.locator('#page-jump')).to_be_disabled()
        page.unroute('**/api/students?*', fail)
        page.locator('.empty [data-action="refresh-students"]').click()
        playwright.expect(page.locator('.score-table-scroll')).to_have_attribute('aria-busy', 'false')
        playwright.expect(page.locator('.students-table .score-value').first).to_have_text('86分')
        playwright.expect(page.locator('#page-jump')).to_be_enabled()
    finally:
        page.close()


@pytest.mark.parametrize('width', [1440, 390])
def test_scope_checkboxes_and_real_xlsx_downloads(browser, directory_url, tmp_path, width):
    page = browser.new_page(viewport={'width': width, 'height': 1000}, accept_downloads=True)
    try:
        page.goto(directory_url + '/#scores')
        page.wait_for_load_state('networkidle')
        college = page.locator('[data-scope="college"][data-id="1"]')
        college.click()
        playwright.expect(college).to_have_attribute('aria-checked', 'true')
        page.locator('[data-action="toggle-college"][data-id="1"]').click()
        first_class = page.locator('[data-scope="class"][data-id="1"]')
        playwright.expect(first_class).to_have_attribute('aria-checked', 'true')
        first_class.click()
        playwright.expect(college).to_have_attribute('aria-checked', 'mixed')
        playwright.expect(page.locator('[data-scope="all"]')).to_have_attribute('aria-checked', 'false')
        first_class.click()
        playwright.expect(college).to_have_attribute('aria-checked', 'true')

        def download(action, filename, expected_sheets, expected_people):
            page.locator('.export-menu summary').click()
            with page.expect_download() as result:
                page.locator(f'[data-action="{action}"]').click()
            path = tmp_path / filename
            result.value.save_as(path)
            workbook = read_workbook(path.read_bytes(), filename)
            assert len(workbook) == expected_sheets
            assert sum(sheet.rows - 1 for sheet in workbook) == expected_people
            playwright.expect(page.locator('.export-menu summary')).not_to_contain_text('导出中')

        download('score-export', 'college.xlsx', 2, 30)
        page.locator('#student-search').fill('不存在的学生')
        playwright.expect(page.locator('.empty h3')).to_have_text('没有符合条件的学生')
        # Scope export deliberately includes the complete classes, not search or page.
        download('score-export', 'complete-scope.xlsx', 2, 30)
        download('score-export-below', 'unqualified.xlsx', 2, 30)
        page.locator('[data-action="clear-export-selection"]').click()
        playwright.expect(college).to_have_attribute('aria-checked', 'false')
        clear_directory_filters(page)
        page.locator('[data-action="select-college"][data-id="2"]').click()
        page.locator('[data-action="select-class"][data-id="3"]').click()
        download('score-export-below', 'current-class.xlsx', 1, 5)
        page.locator('[data-scope="class"][data-id="3"]').click()
        download('score-export', 'selected-class.xlsx', 1, 5)
        page.locator('[data-scope="all"]').click()
        playwright.expect(page.locator('[data-scope="all"]')).to_have_attribute('aria-checked', 'true')
        download('score-export', 'all.xlsx', 3, 35)
        page.locator('[data-scope="all"]').press('Space')
        playwright.expect(page.locator('[data-scope="all"]')).to_have_attribute('aria-checked', 'false')
    finally:
        page.close()


def test_custom_dropdown_keyboard_focus_and_escape(browser, directory_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    try:
        page.goto(directory_url + '/#scores')
        page.wait_for_load_state('networkidle')
        trigger = page.locator('#status-filter-trigger')
        trigger.focus()
        trigger.press('ArrowDown')
        playwright.expect(trigger).to_have_attribute('aria-expanded', 'true')
        selected = page.get_by_role('option', selected=True)
        assert selected.evaluate('(el) => getComputedStyle(el).backgroundColor') == 'rgb(54, 93, 197)'
        page.keyboard.press('End')
        page.keyboard.press('Enter')
        playwright.expect(page.locator('#status-filter')).to_have_value('免考')
        playwright.expect(page.locator('.empty h3')).to_have_text('没有符合条件的学生')
        trigger.click()
        page.keyboard.press('Escape')
        playwright.expect(trigger).to_have_attribute('aria-expanded', 'false')
        playwright.expect(trigger).to_be_focused()
        clear_directory_filters(page)
        page.locator('[data-action="edit-student"]').first.click()
        page.locator('#edit-status-trigger').click()
        page.get_by_role('option', name='已完成', exact=True).click()
        playwright.expect(page.locator('#edit-status')).to_have_value('已完成')
        page.locator('#edit-status-trigger').click()
        page.keyboard.press('Escape')
        playwright.expect(page.locator('#student-dialog')).to_be_visible()
        page.locator('#student-dialog [data-action="close-dialog"]').first.click()
        playwright.expect(page.locator('#student-dialog')).not_to_be_visible()
    finally:
        page.close()


def test_auto_page_capacity_tracks_resize_and_can_be_overridden(browser, directory_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 720})
    calls = []
    page.on('request', lambda request: calls.append(request.url) if '/api/students?' in request.url else None)
    try:
        page.goto(directory_url + '/#scores')
        capacities = []
        for height in [720, 1000, 1280]:
            page.set_viewport_size({'width': 1440, 'height': height})
            if capacities:
                page.wait_for_function(r'''previous => {
                    const text = document.querySelector('#page-size-trigger')?.textContent || '';
                    const match = text.match(/(\d+) 条/);
                    return match && Number(match[1]) !== previous;
                }''', arg=capacities[-1])
            page.wait_for_load_state('networkidle')
            playwright.expect(page.locator('.score-table-scroll')).to_have_attribute('aria-busy', 'false')
            match = re.search(r'(\d+) 条', page.locator('#page-size-trigger').inner_text())
            assert match
            capacity = int(match.group(1))
            capacities.append(capacity)
            playwright.expect(page.locator('.students-table tbody tr')).to_have_count(capacity)
            box = page.locator('.students-panel').bounding_box()
            assert abs(box['y'] - (height - box['y'] - box['height'])) < 1
            last = page.locator('.students-table tbody tr').last.bounding_box()
            footer = page.locator('.pagination').bounding_box()
            assert last['y'] + last['height'] <= footer['y'] + 1
            assert footer['y'] - last['y'] - last['height'] < 54
        assert capacities[0] < capacities[1] < capacities[2]
        assert len(calls) < 15, 'Automatic sizing should settle, not repeatedly reload'
        page.locator('#page-size-trigger').click()
        page.get_by_role('option', name='20 条 / 页', exact=True).click()
        playwright.expect(page.locator('.students-table tbody tr')).to_have_count(20)
        page.set_viewport_size({'width': 1440, 'height': 800})
        playwright.expect(page.locator('.students-table tbody tr')).to_have_count(20)
        page.locator('#page-size-trigger').click()
        page.get_by_role('option', name=re.compile('自适应')).click()
        playwright.expect(page.locator('#page-size')).to_have_value('auto')
        page.wait_for_load_state('networkidle')
        assert page.locator('.students-table tbody tr').count() < 20
    finally:
        page.close()


def downloaded_students(page, tmp_path, action, filename):
    page.locator('.export-menu summary').click()
    with page.expect_download() as result:
        page.locator(f'[data-action="{action}"]').click()
    path = tmp_path / filename
    result.value.save_as(path)
    workbook = read_workbook(path.read_bytes(), filename)
    return {sheet.cell(row, 0).text() for sheet in workbook for row in range(1, sheet.rows)}


@pytest.mark.parametrize('width', [1440, 390])
def test_individual_and_all_filtered_student_selection(browser, directory_url, tmp_path, width):
    page = browser.new_page(viewport={'width': width, 'height': 1000}, accept_downloads=True)
    try:
        page.goto(directory_url + '/#scores')
        page.wait_for_load_state('networkidle')
        assert page.locator('.list-tools [data-action="clear-filters"]').count() == 0
        assert page.locator('h1 .count-pill b').evaluate('(el) => parseFloat(getComputedStyle(el).fontSize)') >= 24
        first_id = page.locator('[data-student-row]').first.get_attribute('data-student-row')
        page.locator('[data-action="check-student"]').first.click()
        playwright.expect(page.locator('[data-action="check-all-students"]')).to_have_attribute('aria-checked', 'mixed')
        playwright.expect(page.locator('[data-scope="all"]')).to_have_attribute('aria-checked', 'false')
        assert downloaded_students(page, tmp_path, 'score-export-students', 'one.xlsx') == {first_id}
        page.locator('[data-action="next-page"]').click()
        page.wait_for_load_state('networkidle')
        second_id = page.locator('[data-student-row]').first.get_attribute('data-student-row')
        assert second_id != first_id
        page.locator('[data-action="check-student"]').first.click()
        assert downloaded_students(page, tmp_path, 'score-export-students', 'two.xlsx') == {first_id, second_id}
        page.locator('[data-action="check-all-students"]').click()
        page.wait_for_load_state('networkidle')
        playwright.expect(page.locator('[data-action="check-all-students"]')).to_have_attribute('aria-checked', 'true')
        playwright.expect(page.locator('.student-selection-summary')).to_contain_text('35 人')
        playwright.expect(page.locator('[data-scope="all"]')).to_have_attribute('aria-checked', 'false')
        all_ids = {f'202600{i:04}' for i in range(1, 36)}
        assert downloaded_students(page, tmp_path, 'score-export-students', 'all-filtered.xlsx') == all_ids
        unchecked_id = page.locator('[data-student-row]').first.get_attribute('data-student-row')
        page.locator('[data-action="check-student"]').first.click()
        playwright.expect(page.locator('[data-action="check-all-students"]')).to_have_attribute('aria-checked', 'mixed')
        assert downloaded_students(page, tmp_path, 'score-export-students', 'all-except-one.xlsx') == all_ids - {unchecked_id}
        page.locator('[data-action="check-all-students"]').click()
        playwright.expect(page.locator('[data-action="check-all-students"]')).to_have_attribute('aria-checked', 'true')
        page.locator('[data-action="check-all-students"]').click()
        playwright.expect(page.locator('[data-action="check-all-students"]')).to_have_attribute('aria-checked', 'false')
        assert page.locator('.student-selection-summary').count() == 0
    finally:
        page.close()


def test_current_scope_export_is_available_without_extra_checkboxes(browser, directory_url, tmp_path):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    try:
        page.goto(directory_url + '/#scores')
        page.wait_for_load_state('networkidle')
        page.locator('[data-action="select-college"][data-id="1"]').click()
        page.locator('[data-action="select-class"][data-id="1"]').click()
        playwright.expect(page.locator('[data-scope="all"]')).to_have_attribute('aria-checked', 'false')
        assert downloaded_students(page, tmp_path, 'score-export', 'browse-class.xlsx') == {f'202600{i:04}' for i in range(1, 16)}
        page.locator('[data-scope="class"][data-id="1"]').click()
        playwright.expect(page.locator('[data-scope="all"]')).to_have_attribute('aria-checked', 'false')
        page.locator('[data-scope="college"][data-id="1"]').click()
        playwright.expect(page.locator('[data-scope="all"]')).to_have_attribute('aria-checked', 'false')
    finally:
        page.close()


def test_positive_grade_statistics_below_90_filter_and_sorting(browser, directory_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    headers = {'X-Requested-With': 'StudentScore'}
    student_url = directory_url + '/api/students/2026000009'
    before = page.request.get(student_url).json()
    assert page.request.patch(student_url, headers=headers, data={'version': before['version'], 'status': '已完成', 'score_rate': 99}).ok
    try:
        page.goto(directory_url + '/#scores')
        page.wait_for_load_state('networkidle')
        playwright.expect(page.locator('[data-stat="qualified"]')).to_have_text('1人')
        playwright.expect(page.locator('[data-stat="below-90"]')).to_have_text('34人')
        playwright.expect(page.locator('[data-stat="settled"]')).to_have_text('31人')
        playwright.expect(page.locator('[data-stat="average"]')).to_have_text('83.67%')
        assert page.locator('.directory-service-alert').count() == 0
        page.locator('[data-action="tree-sort"][data-sort="count"]').click()
        page.locator('[data-action="tree-sort"][data-sort="count"]').click()
        assert page.locator('[data-action="select-college"]').evaluate_all('(els) => els.map(el => el.dataset.id)') == ['2', '1']
        page.locator('[data-action="sort"][data-sort="college_name"]').click()
        playwright.expect(page.locator('.students-table .cell-secondary').first).to_have_text('测试学院乙')
        page.locator('[data-action="sort"][data-sort="college_name"]').click()
        playwright.expect(page.locator('.students-table .cell-secondary').first).to_have_text('测试学院甲')
        page.locator('#score-filter-trigger').click()
        page.get_by_role('option', name='不合格（含无成绩）', exact=True).click()
        playwright.expect(page.locator('[data-stat="qualified"]')).to_have_text('0人')
        playwright.expect(page.locator('[data-stat="below-90"]')).to_have_text('34人')
        playwright.expect(page.locator('[data-stat="settled"]')).to_have_text('30人')
        playwright.expect(page.locator('[data-stat="average"]')).to_have_text('83.16%')
        playwright.expect(page.locator('.page-range')).to_contain_text('共 34 条')
        page.locator('[data-action="select-college"][data-id="1"]').click()
        page.locator('[data-action="select-class"][data-id="1"]').click()
        playwright.expect(page.locator('[data-stat="below-90"]')).to_have_text('14人')
    finally:
        current = page.request.get(student_url).json()
        assert page.request.patch(student_url, headers=headers, data={'version': current['version'], 'status': before['status'], 'score_rate': before['score_rate']}).ok
        page.close()


def test_old_backend_does_not_fake_zero_statistics_or_hide_export_failure(browser, directory_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    headers = {'X-Requested-With': 'StudentScore'}
    student_url = directory_url + '/api/students/2026000009'
    before = page.request.get(student_url).json()
    assert page.request.patch(student_url, headers=headers, data={'version': before['version'], 'status': '已完成', 'score_rate': 99}).ok
    try:
        def old_statistics(route):
            response = route.fetch()
            payload = response.json()
            for key in ['qualified', 'below_90', 'at_90']:
                payload.pop(key)
            route.fulfill(response=response, json=payload)
        page.route('**/api/students?*', old_statistics)
        page.route('**/api/students/export.xlsx', lambda route: route.fulfill(status=405, content_type='application/json', body='{"detail":"Method Not Allowed"}'))
        page.goto(directory_url + '/#scores')
        page.wait_for_load_state('networkidle')
        playwright.expect(page.locator('[data-stat="qualified"]')).to_have_text('1人')
        playwright.expect(page.locator('[data-stat="below-90"]')).to_have_text('34人')
        playwright.expect(page.locator('.directory-service-alert')).to_contain_text('补算统计')
        playwright.expect(page.locator('.directory-service-alert')).to_contain_text('旧后端')
        page.locator('.export-menu summary').click()
        playwright.expect(page.locator('[data-action="score-export"]')).to_be_enabled()
        page.locator('[data-action="score-export"]').click()
        playwright.expect(page.locator('#toast')).to_contain_text('Ctrl+C')
        playwright.expect(page.locator('.export-menu summary')).not_to_contain_text('导出中')
    finally:
        current = page.request.get(student_url).json()
        assert page.request.patch(student_url, headers=headers, data={'version': current['version'], 'status': before['status'], 'score_rate': before['score_rate']}).ok
        page.close()


def test_legacy_statistics_read_every_page_and_verify_revision(browser, directory_url):
    page = browser.new_page()
    try:
        page.goto(directory_url)
        result = page.evaluate('''async () => {
            const {completeDirectoryStatistics} = await import('/static/directory_stats.js');
            const rows = Array.from({length:205},(_,i)=>({student_id:String(i),score_rate:[0,89,90,99][i%4],status:i%10===0?'进行中':'已完成'}));
            const settled = rows.filter(row=>row.status==='已完成');
            const scans = []; let healthCalls=0;
            const api = async path => {
                if(path==='/api/health')return {revision:++healthCalls===1?1:2};
                const query = new URL(path,location.origin).searchParams;
                const size = Number(query.get('page_size')), page = Number(query.get('page'));
                if(size===100)scans.push(page);
                return {items:rows.slice((page-1)*size,page*size),total:205,recorded:205,settled:settled.length,page,page_size:size,pages:Math.ceil(205/size)};
            };
            const data = await completeDirectoryStatistics(api,'score_filter=all&page_size=7&page=2');
            return {data,scans,expected:{qualified:settled.filter(row=>row.score_rate>90).length,below_90:rows.filter(row=>row.status==='进行中'||row.score_rate<90).length,at_90:settled.filter(row=>row.score_rate===90).length}};
        }''')
        assert result['scans'] == [1, 2, 3, 1, 2, 3]
        assert len(result['data']['items']) == 7
        for key, expected in result['expected'].items():
            assert result['data'][key] == expected
    finally:
        page.close()


def test_sanitized_template_mount_keeps_controls_without_event_sinks(browser, directory_url):
    page = browser.new_page()
    try:
        page.goto(directory_url)
        result = page.evaluate('''async () => {
            const {mountHTML} = await import('/static/safe_dom.js');
            window.domAttack=0;
            const root=document.createElement('div');root.style.display='none';document.body.append(root);
            mountHTML(root,'<script>window.domAttack=1</script><button id="safe-test" onclick="window.domAttack=2">按钮</button><a href="javascript:window.domAttack=3">链接</a><svg onload="window.domAttack=4"><circle cx="2" cy="2" r="1"/></svg><details open><summary>设置</summary><input value="0001"></details><span id="bar-test" style="width:42%"></span>');
            root.querySelector('button').click();root.querySelector('a').click();
            const result={scripts:root.querySelectorAll('script').length,events:root.querySelectorAll('[onclick],[onload]').length,href:root.querySelector('a').hasAttribute('href'),open:root.querySelector('details').open,value:root.querySelector('input').value,width:root.querySelector('#bar-test').style.width,svg:root.querySelector('svg').namespaceURI,attack:window.domAttack};
            root.remove();return result;
        }''')
        assert result == {'scripts': 0, 'events': 0, 'href': False, 'open': True, 'value': '0001',
                          'width': '42%', 'svg': 'http://www.w3.org/2000/svg', 'attack': 0}
    finally:
        page.close()


@pytest.mark.parametrize('width', [1440, 390])
def test_unqualified_filter_export_and_row_selection_include_no_scores(browser, directory_url, tmp_path, width):
    page = browser.new_page(viewport={'width': width, 'height': 1000}, accept_downloads=True)
    try:
        page.goto(directory_url + '/#scores')
        page.wait_for_load_state('networkidle')
        playwright.expect(page.locator('[data-stat="below-90"]')).to_have_text('35人')
        playwright.expect(page.locator('[data-stat="settled"]')).to_have_text('31人')
        playwright.expect(page.locator('[data-stat="average"]')).to_have_text('83.25%')
        page.locator('#score-filter-trigger').click()
        page.get_by_role('option', name='不合格（含无成绩）', exact=True).click()
        page.wait_for_load_state('networkidle')
        playwright.expect(page.locator('.page-range')).to_contain_text('共 35 条')
        expected = {f'202600{i:04}' for i in range(1, 36)}
        assert downloaded_students(page, tmp_path, 'score-export-below', 'all-unqualified.xlsx') == expected
        # Students whose entire grade is missing are still selectable and exportable.
        page.locator('[data-action="check-student"][data-student="2026000003"]').click()
        assert downloaded_students(page, tmp_path, 'score-export-below', 'missing-selected.xlsx') == {'2026000003'}
        workbook = read_workbook((tmp_path / 'missing-selected.xlsx').read_bytes(), 'missing-selected.xlsx')
        assert workbook[0].cell(1, 4).text() == '待录入'
        assert workbook[0].cell(1, 5).value is None and workbook[0].cell(1, 6).value is None
        page.locator('[data-action="check-all-students"]').click()
        assert downloaded_students(page, tmp_path, 'score-export-students', 'all-filtered.xlsx') == expected
        page.locator('[data-action="check-student"][data-student="2026000003"]').click()
        assert downloaded_students(page, tmp_path, 'score-export-below', 'excluded-missing.xlsx') == expected - {'2026000003'}
    finally:
        page.close()


def test_v2_backend_never_silently_exports_the_old_smaller_unqualified_list(browser, directory_url, tmp_path):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    exported = []
    page.on('request', lambda request: exported.append(request.url) if request.method == 'POST' and request.url.endswith('/api/students/export.xlsx') else None)
    try:
        def legacy_health(route):
            response = route.fetch()
            payload = response.json()
            payload['directory_api_version'] = 2
            route.fulfill(response=response, json=payload)
        def legacy_counts(route):
            response = route.fetch()
            payload = response.json()
            payload['below_90'] = 31  # The previous backend excluded four no-score students.
            route.fulfill(response=response, json=payload)
        page.route('**/api/health', legacy_health)
        page.route('**/api/students?*', legacy_counts)
        page.goto(directory_url + '/#scores')
        page.wait_for_load_state('networkidle')
        playwright.expect(page.locator('[data-stat="below-90"]')).to_have_text('35人')
        playwright.expect(page.locator('.directory-service-alert')).to_contain_text('不合格含无有效成绩')
        page.locator('.export-menu summary').click()
        page.locator('[data-action="score-export-below"]').click()
        playwright.expect(page.locator('#toast')).to_contain_text('Ctrl+C')
        playwright.expect(page.locator('.export-menu summary')).not_to_contain_text('导出中')
        assert not exported
        page.locator('#score-filter-trigger').click()
        page.get_by_role('option', name='不合格（含无成绩）', exact=True).click()
        playwright.expect(page.locator('.empty h3')).to_have_text('成绩读取失败')
        playwright.expect(page.locator('.empty p')).to_contain_text('重新启动')
        # A refreshed/upgraded service can resume without reloading the whole UI.
        page.unroute('**/api/health', legacy_health)
        page.unroute('**/api/students?*', legacy_counts)
        page.locator('.empty [data-action="refresh-students"]').click()
        playwright.expect(page.locator('[data-stat="below-90"]')).to_have_text('35人')
        assert page.locator('.directory-service-alert').count() == 0
        expected = {f'202600{i:04}' for i in range(1, 36)}
        assert downloaded_students(page, tmp_path, 'score-export-below', 'new-rule.xlsx') == expected
        assert len(exported) == 1
    finally:
        page.close()
