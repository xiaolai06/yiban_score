"""Real browser removal flows use only the temporary test server/database."""
import pytest

from tests.test_app import HEADERS, WRITE, row, xlsx
from tests.test_ui_shell import browser as removal_browser
from tests.test_ui_shell import playwright
from tests.test_ui_shell import ui_url as removal_url

browser = removal_browser
ui_url = removal_url


def upload_payload(rows):
    return {'name':'合成剔除名单.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':xlsx(rows)}


def seed(page, url, rows):
    a = page.request.post(url+'/api/imports/analyze',headers=WRITE,multipart={'file':upload_payload([HEADERS,*rows])}).json()
    sh = a['sheets'][a['selected_sheet']]
    p = page.request.post(url+'/api/imports/preview',headers=WRITE,data={
        'upload_id':a['upload_id'],'sheet_index':a['selected_sheet'],'header_row':sh['header_row'],'mapping':sh['mapping']}).json()
    assert page.request.post(url+'/api/imports/commit',headers=WRITE,data={'preview_id':p['preview_id']}).ok


@pytest.mark.parametrize('width',[1920,1440,1024,768,390,320])
def test_removal_preview_confirm_export_stats_and_reimport(browser,ui_url,width,tmp_path):
    page = browser.new_page(viewport={'width':width,'height':950},accept_downloads=True)
    errors=[]
    page.on('pageerror',lambda error: errors.append(str(error)))
    sid=f'REMOVE{width}',f'KEEP{width}'
    try:
        seed(page,ui_url,[row(sid[0],name='剔除样例',rate=95,status='已完成'),row(sid[1],name='保留样例',rate=0,status='已完成')])
        page.goto(ui_url+'/#import')
        page.locator('[data-operation="remove"]').click()
        page.locator('#removal-file-input').set_input_files(upload_payload([['学号','姓名'],[sid[0],'不用于匹配的姓名'],[f'ABSENT{width}','未知'],[sid[0],'重复']]))
        page.locator('[data-action="removal-preview"]').click()
        page.locator('#removal-preview').wait_for()
        playwright.expect(page.locator('[data-action="removal-commit"]')).to_be_disabled()
        assert page.request.get(ui_url+f'/api/students/{sid[0]}').ok
        with page.expect_download() as downloading:
            page.locator('#removal-preview a[href$="/preview.csv"]').click()
        assert downloading.value.suggested_filename=='removal-preview.csv'
        path=tmp_path/'preview.csv'
        downloading.value.save_as(path)
        assert sid[0] in path.read_text(encoding='utf-8-sig')
        assert sid[1] not in path.read_text(encoding='utf-8-sig')
        page.locator('#removal-confirm').check()
        page.locator('#removal-confirmation').fill('确认')
        playwright.expect(page.locator('[data-action="removal-commit"]')).to_be_disabled()
        page.locator('#removal-confirmation').fill('确认剔除')
        playwright.expect(page.locator('[data-action="removal-commit"]')).to_be_enabled()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('[data-action="removal-commit"]').click()
        playwright.expect(page.locator('.success-panel h2')).to_have_text('剔除已完成')
        assert page.request.get(ui_url+f'/api/students/{sid[0]}').status==404
        assert page.request.get(ui_url+f'/api/students/{sid[1]}').json()['score_rate']==0
        page.locator('.success-panel [data-action="goto-scores"]').click()
        page.locator('.scores-workspace').wait_for()
        page.locator('#student-search').fill(sid[0])
        playwright.expect(page.locator('#score-pagination')).to_contain_text('共 0 条')
        page.locator('[data-nav="overview"]').click()
        page.locator('.overview-workspace').wait_for()
        page.locator('[data-nav="import"]').click()
        page.locator('[data-operation="import"]').click()
        page.locator('#file-input').set_input_files(upload_payload([HEADERS,row(sid[0])]))
        page.locator('[data-action="preview"]').click()
        page.locator('#preview').wait_for()
        playwright.expect(page.locator('#preview')).to_contain_text('已剔除，跳过')
        page.locator('[data-action="commit"]').click()
        page.locator('.success-panel').wait_for()
        assert page.request.get(ui_url+f'/api/students/{sid[0]}').status==404
        assert not errors,errors
    finally:
        page.close()


def test_invalid_a_column_no_delete_and_old_server_guard(browser,ui_url):
    page=browser.new_page(viewport={'width':390,'height':900})
    try:
        page.goto(ui_url)
        page.locator('[data-operation="remove"]').click()
        page.locator('#removal-file-input').set_input_files(upload_payload([['姓名','学号'],['不能按姓名删','000001']]))
        page.locator('[data-action="removal-preview"]').click()
        playwright.expect(page.locator('.removal-card [role="alert"]')).to_contain_text('A 列表头必须')
        page.route('**/api/health',lambda route:route.fulfill(json={'storage':'SQLite','directory_api_version':3}))
        page.reload()
        page.locator('[data-operation="remove"]').click()
        playwright.expect(page.locator('.removal-card [role="alert"]')).to_contain_text('重启服务')
        playwright.expect(page.locator('[data-action="removal-preview"]')).to_be_disabled()
        assert page.locator('[data-action="removal-choose"]').count()==0
    finally:
        page.close()
