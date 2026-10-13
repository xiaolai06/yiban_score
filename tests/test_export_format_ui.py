"""Real downloads and format-version guards against a temporary UI server."""
import pytest

from server.excel_reader import read_workbook
from tests.test_app import row
from tests.test_roster_removal_ui import seed
from tests.test_ui_shell import browser as export_browser
from tests.test_ui_shell import playwright
from tests.test_ui_shell import ui_url as export_url

browser = export_browser
ui_url = export_url


def download(page, path, action, expected):
    page.locator('.export-menu summary').click()
    with page.expect_download() as result:
        page.locator(f'[data-action="{action}"]').click()
    result.value.save_as(path)
    sheets = read_workbook(path.read_bytes(),path.name)
    assert sheets[-1].name == '导出说明'
    assert [sheets[-1].cell(1, col).value for col in range(3,10)] == pytest.approx(expected)
    assert sum(sheet.rows-1 for sheet in sheets[:-1]) == expected[0]
    playwright.expect(page.locator('.export-menu summary')).not_to_contain_text('导出中')


@pytest.mark.parametrize('width',[1440,390])
def test_download_summary_style_descriptions_and_exact_selected_scope(browser,ui_url,tmp_path,width):
    page = browser.new_page(viewport={'width':width,'height':1000},accept_downloads=True)
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    try:
        # Each width owns a separate class; class scope must not include the other width.
        ids = [f'FORMAT{width}-{index}' for index in range(5)]
        rates = [90,0,20,None,100]
        seed(page,ui_url,[row(sid,name=f'样式学生{index}',cls=f'样式{width}班',rate=rate,
                              status='进行中' if index==2 else '已完成')
                          for index,(sid,rate) in enumerate(zip(ids,rates,strict=True))])
        student = page.request.get(ui_url+f'/api/students/{ids[0]}').json()
        page.goto(ui_url+'/#scores')
        page.locator(f'[data-action="select-college"][data-id="{student["college_id"]}"]').click()
        page.locator(f'[data-action="select-class"][data-id="{student["class_id"]}"]').click()
        playwright.expect(page.locator('.score-table-scroll')).to_have_attribute('aria-busy','false')
        page.locator('.export-menu summary').click()
        playwright.expect(page.locator('.export-popover small')).to_contain_text('中文微软雅黑')
        playwright.expect(page.locator('.export-popover small')).to_contain_text('合格率＝合格人数／参与人数')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('.export-menu summary').click()
        download(page,tmp_path/'class.xlsx','score-export',[5,3,2,.6,2,2/3,.4])
        download(page,tmp_path/'unqualified.xlsx','score-export-below',[3,1,2,1/3,0,0,0])
        page.locator(f'[data-action="check-student"][data-student="{ids[2]}"]').click()
        download(page,tmp_path/'selected.xlsx','score-export-students',[1,1,0,1,0,0,0])
        assert not errors,errors
    finally:
        page.close()


def test_old_export_response_without_summary_cannot_silently_download(browser,ui_url):
    page=browser.new_page(viewport={'width':390,'height':1000},accept_downloads=True)
    downloads=[]
    page.on('download',lambda item:downloads.append(item))
    try:
        seed(page,ui_url,[row('LEGACY-EXPORT',rate=95,status='已完成')])
        page.goto(ui_url+'/#scores')
        playwright.expect(page.locator('.score-table-scroll')).to_have_attribute('aria-busy','false')
        def old_response(route):
            response=route.fetch()
            headers=dict(response.headers)
            headers.pop('x-export-workbook-version',None)
            route.fulfill(response=response,headers=headers)
        page.route('**/api/students/export.xlsx',old_response)
        page.locator('.export-menu summary').click()
        page.locator('[data-action="score-export"]').click()
        playwright.expect(page.locator('#toast')).to_contain_text('尚未加载统一≥90合格规则及新版导出说明表')
        playwright.expect(page.locator('#toast')).to_contain_text('Ctrl+C')
        assert not downloads
        playwright.expect(page.locator('.export-menu summary')).not_to_contain_text('导出中')
    finally:
        page.close()
