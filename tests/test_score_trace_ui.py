"""Full-row traces, current state, and legacy-service UI on isolated synthetic data."""
import csv
import io
from urllib.parse import urlsplit

import pytest

from tests.test_app import row
from tests.test_roster_removal_ui import seed
from tests.test_ui_shell import browser as trace_browser
from tests.test_ui_shell import playwright
from tests.test_ui_shell import ui_url as trace_url

browser = trace_browser
ui_url = trace_url
WRITE = {'X-Requested-With': 'StudentScore'}


def source_file(sid, name, rate, account, extra='原表备注'):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['学号', '姓名', '状态', '得分率', '易班ID', '备注', '备注', ''])
    writer.writerow([sid, name, '已完成', rate, account, extra, '=1+1', ''])
    return {'name': '原始追溯合成.csv', 'mimeType': 'text/csv', 'buffer': out.getvalue().encode('utf-8-sig')}


def save_source(page, url, source):
    uploaded = page.request.post(url + '/api/scores/analyze', headers=WRITE, multipart={'file': source}).json()
    sheet = uploaded['sheets'][uploaded['selected_sheet']]
    p = page.request.post(url + '/api/scores/preview', headers=WRITE, data={
        'upload_id': uploaded['upload_id'], 'sheet_index': uploaded['selected_sheet'],
        'header_row': sheet['header_row'], 'mapping': sheet['mapping'], 'batch_name': '完整行追溯测试',
    }).json()
    result = page.request.post(url + '/api/scores/commit', headers=WRITE, data={
        'preview_id': p['preview_id'], 'skip_issues': True, 'save_issues_only': not p['can_commit'],
    })
    assert result.ok, result.text()
    return result.json()['batch']


@pytest.mark.parametrize('width', [1920, 1440, 1024, 768, 390, 320])
def test_original_row_expansion_and_real_current_status_keep_layout_and_matching(browser, ui_url, tmp_path, width):
    page = browser.new_page(viewport={'width': width, 'height': 950})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    sid, account = f'TRACE-{width}', f'000{width}'
    malicious = '  第一行\n<img src=x onerror=alert(1)>  '
    try:
        seed(page, ui_url, [row(sid, name='合成在册甲', cls=f'{sid}班')])
        save_source(page, ui_url, source_file(sid, '合成在册甲', 92, account))
        older = save_source(page, ui_url, source_file(sid, '报表冲突姓名', 80, "'" + account + '.0', malicious))
        latest = save_source(page, ui_url, source_file(sid, '报表冲突姓名', 0, account, malicious))
        page.goto(ui_url + '/#score-import')
        opener = page.locator('.heading-actions [data-action="score-issues-open"]')
        assert opener.evaluate('el => el.nextElementSibling.dataset.action') == 'score-people-open'
        opener.click()
        dialog = page.locator('#score-issues-dialog')
        dialog.locator('#issue-search').fill(account)
        person = dialog.locator(f'[data-person-key="student:{sid}"]')
        playwright.expect(person).to_have_count(1)
        playwright.expect(person).to_contain_text(account)
        playwright.expect(person.locator('.rate-value')).to_have_text('80%')
        person.locator('[data-action="issue-person"]').click()
        detail = dialog.locator('#issue-detail')
        playwright.expect(detail.locator('tbody tr')).to_have_count(2)
        toggle = detail.locator(f'[data-action="issue-source-toggle"][data-key="{latest["id"]}:2"]')
        toggle.click()
        disclosure = detail.locator('#issue-source-panel')
        playwright.expect(disclosure.locator('.source-columns .source-column')).to_have_count(9)
        values = disclosure.locator('.source-cell-value')
        playwright.expect(values.nth(3)).to_have_text('0')
        assert values.nth(5).text_content() == malicious
        playwright.expect(disclosure.locator('.trace-location')).to_contain_text(f'批次 #{latest["id"]}')
        playwright.expect(disclosure).to_contain_text('异常未写入正式成绩')
        assert dialog.locator('img,script').count() == 0
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        box = dialog.bounding_box()
        assert box['x'] >= 0 and box['width'] <= width
        source_box = disclosure.locator('.source-columns').bounding_box()
        assert source_box['x'] >= box['x'] and source_box['x'] + source_box['width'] <= box['x'] + box['width']
        page.screenshot(path=str(tmp_path / f'full-trace-{width}.png'))
        # Refresh retains the expanded full-width panel and the displayed row.
        dialog.locator('[data-action="issue-refresh"]').click()
        playwright.expect(dialog.locator('#issue-people-table')).to_have_attribute('aria-busy', 'false')
        playwright.expect(toggle).to_have_attribute('aria-expanded', 'true')
        playwright.expect(values.nth(3)).to_have_text('0')
        detail.locator(f'[data-action="issue-source-toggle"][data-key="{older["id"]}:2"]').click()
        playwright.expect(disclosure.locator('.source-cell-value').nth(3)).to_have_text('80')
        # Current state is a separate query, not the historical anomaly's score/name.
        person.locator('[data-action="score-people-open"]').click()
        lookup = page.locator('#score-lookup-dialog')
        playwright.expect(dialog).not_to_be_visible()
        playwright.expect(lookup).to_be_visible()
        current = lookup.locator(f'[data-student-row="lookup:{sid}"]')
        playwright.expect(current).to_contain_text('合成在册甲')
        playwright.expect(current.locator('.rate-value')).to_have_text('92%')
        assert '报表冲突姓名' not in current.inner_text()
        lookup.locator('#lookup-search').fill(account)
        playwright.expect(lookup.locator('.issue-dialog-content')).to_have_attribute('aria-busy', 'false')
        playwright.expect(current).to_have_count(1)
        lookup.locator('#score-status-guide > summary').click()
        playwright.expect(lookup.locator('.status-guide-grid article')).to_have_count(7)
        playwright.expect(lookup.locator('.status-guide-note')).to_contain_text('历史异常不代表目前仍未解决')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(tmp_path / f'current-status-{width}.png'))
        current.locator('[data-action="score-issues-open"]').click()
        playwright.expect(lookup).not_to_be_visible()
        playwright.expect(dialog).to_be_visible()
        playwright.expect(dialog.locator('#issue-search')).to_have_value(sid)
        dialog.locator('.dialog-header [data-action="issue-close"]').click()
        page.locator(f'[data-action="score-audit"][data-id="{older["id"]}"]').click()
        audit = page.locator('#score-audit')
        # Source column disclosures must not alter the original table's row count.
        playwright.expect(audit.locator('.cleaning-table tbody tr')).to_have_count(1)
        audit.locator('[data-action="score-audit-source"]').click()
        playwright.expect(audit.locator('.source-cell-value').nth(3)).to_have_text('80')
        assert not errors, errors
    finally:
        page.close()


@pytest.mark.parametrize('width', [1440, 390])
def test_old_service_and_legacy_record_degrade_without_breaking_import(browser, ui_url, width):
    page = browser.new_page(viewport={'width': width, 'height': 950})
    errors, mappings = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    sid = f'LEGACY-TRACE-{width}'
    try:
        def old_service(route):
            path = urlsplit(route.request.url).path
            if path == '/api/scores/preview':
                mappings.append(route.request.post_data_json['mapping'])
                assert 'yiban_id' not in mappings[-1]
            response = route.fetch()
            if 'application/json' not in response.headers.get('content-type', ''):
                route.fulfill(response=response)
                return
            data = response.json()
            if path == '/api/scores/analyze':
                data['fields'].pop('yiban_id')
                for sheet in data['sheets']:
                    sheet['mapping'].pop('yiban_id', None)
            if '/rows/' in path:
                for key in ('source_row', 'yiban_id', 'raw_yiban_id'):
                    data.get('row', {}).pop(key, None)
            if path == '/api/scores/issues':
                for item in data['items']:
                    item.pop('yiban_ids', None)
                    item.pop('raw_yiban_ids', None)
            route.fulfill(response=response, json=data)
        page.route('**/api/**', old_service)
        page.goto(ui_url + '/#score-import')
        page.locator('#score-file').set_input_files(source_file(sid, '旧服务合成人员', 80, '000771'))
        playwright.expect(page.locator('#score-map-yiban_id')).to_be_disabled()
        playwright.expect(page.locator('#score-source-note')).to_contain_text('兼容旧版服务')
        page.locator('[data-action="score-preview"]').click()
        page.locator('#score-skip').check()
        page.locator('[data-action="score-commit"]').click()
        playwright.expect(page.locator('.success-panel')).to_be_visible()
        assert mappings
        page.locator('.heading-actions [data-action="score-issues-open"]').click()
        dialog = page.locator('#score-issues-dialog')
        dialog.locator('#issue-search').fill(sid)
        dialog.locator(f'[data-person-key="student:{sid}"] [data-action="issue-person"]').click()
        dialog.locator('[data-action="issue-source-toggle"]').click()
        source = dialog.locator('#issue-source-panel')
        playwright.expect(source.locator('.info-box.warning')).to_contain_text('此旧批次未保存')
        assert source.locator('.source-columns').count() == 0
        playwright.expect(source).to_contain_text('80%')
        dialog.locator('.dialog-header [data-action="issue-close"]').click()
        page.route('**/api/scores/people?*', lambda route: route.fulfill(status=404, json={'detail': 'Not Found'}))
        page.locator('.heading-actions [data-action="score-people-open"]').click()
        lookup = page.locator('#score-lookup-dialog')
        playwright.expect(lookup.locator('.empty')).to_contain_text('原导入和成绩查看不受影响')
        lookup.locator('.dialog-header [data-action="lookup-close"]').click()
        playwright.expect(page.locator('.heading-actions [data-action="score-people-open"]')).to_be_focused()
        assert not errors, errors
    finally:
        page.close()


def test_source_error_retry_and_lookup_refresh_retains_results(browser, ui_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 950})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    sid = 'RETRY-TRACE'
    try:
        seed(page, ui_url, [row(sid, name='状态重试合成甲')])
        batch = save_source(page, ui_url, source_file(sid, '冲突姓名', 80, '007711'))
        path = f'**/api/scores/batches/{batch["id"]}/rows/2'
        page.route(path, lambda route: route.fulfill(status=503, json={'detail': '原始行读取测试错误'}))
        page.goto(ui_url + '/#score-import')
        page.locator('.heading-actions [data-action="score-issues-open"]').click()
        dialog = page.locator('#score-issues-dialog')
        dialog.locator('#issue-search').fill(sid)
        dialog.locator(f'[data-person-key="student:{sid}"] [data-action="issue-person"]').click()
        dialog.locator('[data-action="issue-source-toggle"]').click()
        source = dialog.locator('#issue-source-panel')
        playwright.expect(source.locator('[role="alert"]')).to_contain_text('原始行读取测试错误')
        page.unroute(path)
        source.locator('[data-action="issue-source-retry"]').click()
        playwright.expect(source.locator('.source-columns')).to_be_visible()
        dialog.locator('.dialog-header [data-action="issue-close"]').click()
        page.locator('.heading-actions [data-action="score-people-open"]').click()
        lookup = page.locator('#score-lookup-dialog')
        lookup.locator('#lookup-search').fill(sid)
        current = lookup.locator('.lookup-table tbody tr')
        playwright.expect(current).to_have_count(1)
        page.route('**/api/scores/people?*', lambda route: route.fulfill(status=503, json={'detail': '人员状态测试错误'}))
        lookup.locator('[data-action="lookup-refresh"]').click()
        playwright.expect(lookup.locator('.issue-summary')).to_contain_text('暂时保留上次结果')
        playwright.expect(current).to_have_count(1)
        playwright.expect(lookup.locator('#lookup-search')).to_have_value(sid)
        page.unroute('**/api/scores/people?*')
        lookup.locator('[data-action="lookup-refresh"]').click()
        playwright.expect(lookup.locator('.issue-dialog-content')).to_have_attribute('aria-busy', 'false')
        assert not errors, errors
    finally:
        page.close()


def test_lookup_pagination_custom_status_and_clear(browser, ui_url):
    page = browser.new_page(viewport={'width': 1440, 'height': 950})
    try:
        seed(page, ui_url, [row(f'LOOKUPPAGE{i:03}', name=f'分页合成人员{i}', cls='状态分页班',
                               status='进行中' if i == 0 else '待录入', rate=60 if i == 0 else None) for i in range(25)])
        page.goto(ui_url + '/#score-import')
        page.locator('.heading-actions [data-action="score-people-open"]').click()
        dialog = page.locator('#score-lookup-dialog')
        with page.expect_response(lambda response: '/api/scores/people?' in response.url and 'q=LOOKUPPAGE' in response.url):
            dialog.locator('#lookup-search').fill('LOOKUPPAGE')
        playwright.expect(dialog.locator('.lookup-table tbody tr')).to_have_count(20)
        dialog.locator('[data-action="lookup-next"]').click()
        playwright.expect(dialog.locator('.lookup-table tbody tr')).to_have_count(5)
        dialog.locator('#lookup-status-trigger').click()
        page.get_by_role('option', name='进行中', exact=True).click()
        playwright.expect(dialog.locator('.lookup-table tbody tr')).to_have_count(1)
        playwright.expect(dialog.locator('.lookup-table')).to_contain_text('分值合格（≥60）')
        playwright.expect(dialog.locator('.lookup-table')).to_contain_text('暂存分数不计已完成人数')
        dialog.locator('[data-action="lookup-clear"]').click()
        playwright.expect(dialog.locator('#lookup-search')).to_have_value('')
        playwright.expect(dialog.locator('#lookup-status')).to_have_value('')
        dialog.locator('#lookup-search').fill('NONEXISTENT-LOOKUP')
        page.keyboard.press('Enter')
        playwright.expect(dialog.locator('.empty h3')).to_have_text('未找到在册人员')
        dialog.locator('.empty [data-action="score-issues-open"]').click()
        issues = page.locator('#score-issues-dialog')
        playwright.expect(issues.locator('#issue-search')).to_have_value('NONEXISTENT-LOOKUP')
        issues.locator('.dialog-header [data-action="issue-close"]').click()
        playwright.expect(page.locator('.heading-actions [data-action="score-issues-open"]')).to_be_focused()
    finally:
        page.close()


@pytest.mark.parametrize(('width', 'height'), [(900, 420), (390, 480)])
def test_lookup_short_viewport_reaches_guide_and_close_without_background_scroll(browser, ui_url, width, height):
    page = browser.new_page(viewport={'width': width, 'height': height})
    try:
        page.goto(ui_url + '/#score-import')
        page.locator('.heading-actions [data-action="score-people-open"]').click()
        dialog = page.locator('#score-lookup-dialog')
        playwright.expect(dialog.locator('.issue-dialog-content')).to_have_attribute('aria-busy', 'false')
        dialog.locator('#score-status-guide > summary').click()
        box = dialog.bounding_box()
        assert box['y'] >= 0 and box['height'] <= height and box['width'] <= width
        before = page.evaluate('scrollY')
        dialog.locator('.dialog-footer [data-action="lookup-close"]').click()
        playwright.expect(dialog).not_to_be_visible()
        assert page.evaluate('scrollY') == before
    finally:
        page.close()
