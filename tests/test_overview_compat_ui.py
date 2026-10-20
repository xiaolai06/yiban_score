"""Old-server compatibility uses synthetic intercepted GETs, never student writes."""

from urllib.parse import parse_qs, urlparse

import pytest

from tests import test_ui_shell as shell_tests

browser = shell_tests.browser
ui_url = shell_tests.ui_url
playwright = shell_tests.playwright


def install_stats_routes(page, mode='legacy'):
    records = [
        {'student_id': f'{i:06}', 'college_id': 1 + i % 2,
         'score_rate': [0, 59.99, 60, 89.99, 90][i % 5],
         'status': ['已完成', '未开始', '进行中'][i % 3]}
        for i in range(230)
    ]
    if mode == 'empty':
        records = []
    state = {'stats_calls': 0, 'student_pages': [], 'fail': mode == 'failure'}

    def stats_response(route):
        state['stats_calls'] += 1
        revision = 1
        if mode == 'change' and state['stats_calls'] > 1:
            revision = 2
        details = []
        if records:
            for college_id in [1, 2]:
                college_records = [r for r in records if r['college_id'] == college_id]
                details.append({'id': college_id, 'name': f'虚构学院{college_id}',
                                'students': len(college_records), 'classes': 1,
                                'recorded': len(college_records)})
        result = {'students': len(records), 'colleges': len(details), 'classes': len(details),
                  'recorded': len(records), 'revision': revision, 'statuses': [],
                  'colleges_detail': details}
        if mode in {'modern', 'previous'}:
            threshold = 60 if mode == 'modern' else 90
            result['pass_score'] = threshold
            result['qualified'] = sum(r['score_rate'] >= threshold for r in records)
            for college in details:
                college['qualified'] = sum(r['college_id'] == college['id'] and r['score_rate'] >= threshold
                                           for r in records)
            if mode == 'modern':
                result['completed'] = sum(r['status'] == '已完成' for r in records)
                for college in details:
                    college['completed'] = sum(r['college_id'] == college['id'] and r['status'] == '已完成'
                                               for r in records)
        route.fulfill(json=result)

    def student_response(route):
        query = parse_qs(urlparse(route.request.url).query)
        assert query['score_filter'] == ['recorded']
        assert query['page_size'] == ['100']
        assert query['sort'] == ['student_id']
        number = int(query['page'][0])
        state['student_pages'].append(number)
        if state['fail']:
            route.fulfill(status=503, json={'detail': '合成测试：统计读取暂时失败'})
            return
        route.fulfill(json={'total': len(records), 'page': number, 'page_size': 100,
                            'pages': max(1, (len(records) + 99) // 100),
                            'items': records[(number - 1) * 100:number * 100]})

    page.route('**/api/stats', stats_response)
    # This synthetic legacy/early-modern service predates hierarchy revisions.
    # Do not mix its mocked stats revision with the real empty test DB revision.
    page.route('**/api/hierarchy',lambda route:route.fulfill(json={'colleges':[]}))
    page.route('**/api/students?**', student_response)
    return state


@pytest.mark.parametrize('mode', ['legacy', 'modern', 'previous', 'empty', 'change'])
def test_full_qualified_counts_with_old_or_new_server(browser, ui_url, mode):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    try:
        state = install_stats_routes(page, mode)
        page.goto(ui_url + '/#overview')
        expected = '0' if mode == 'empty' else '77'
        playwright.expect(page.locator('.metrics .metric-value').last).to_have_text(expected)
        assert page.locator('.overview-count-error').count() == 0
        if mode != 'empty':
            assert page.locator('.qualified-count').all_text_contents() == ['69', '69']
        if mode == 'modern':
            assert state['student_pages'] == []
        elif mode == 'empty':
            assert state['student_pages'] == [1]
        elif mode == 'change':
            assert state['student_pages'] == [1, 2, 3, 1, 2, 3]
        else:
            assert state['student_pages'] == [1, 2, 3]
        page.locator('[data-nav="import"]').click()
        page.locator('[data-nav="overview"]').click()
        playwright.expect(page.locator('.metrics .metric-value').last).to_have_text(expected)
    finally:
        page.close()


def test_qualified_failure_is_not_zero_and_can_retry(browser, ui_url):
    page = browser.new_page(viewport={'width': 390, 'height': 1000})
    try:
        state = install_stats_routes(page, 'failure')
        page.goto(ui_url + '/#overview')
        playwright.expect(page.locator('.metrics .metric-value').last).to_have_text('读取失败')
        assert page.locator('.qualified-count').all_text_contents() == ['读取失败', '读取失败']
        playwright.expect(page.locator('.overview-count-error')).to_contain_text('统计读取暂时失败')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        state['fail'] = False
        page.locator('[data-action="retry-overview"]').click()
        playwright.expect(page.locator('.metrics .metric-value').last).to_have_text('77')
        assert page.locator('.qualified-count').all_text_contents() == ['69', '69']
        assert page.locator('.overview-count-error').count() == 0
    finally:
        page.close()
