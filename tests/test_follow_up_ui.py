"""Actual counselor workflows with synthetic records and a temporary server."""
import csv
import io
from pathlib import Path

import pytest

from server.excel_reader import read_workbook
from tests.test_app import row
from tests.test_roster_removal_ui import seed
from tests.test_ui_shell import browser as follow_browser
from tests.test_ui_shell import playwright
from tests.test_ui_shell import ui_url as follow_url

browser=follow_browser
ui_url=follow_url


def select_class(page,url,sid):
    student=page.request.get(url+f'/api/students/{sid}').json()
    page.goto(url+'/#scores')
    page.locator(f'[data-action="select-college"][data-id="{student["college_id"]}"]').click()
    page.locator(f'[data-action="select-class"][data-id="{student["class_id"]}"]').click()
    playwright.expect(page.locator('.score-table-scroll')).to_have_attribute('aria-busy','false')


@pytest.mark.parametrize('width',[1920,1440,1024,768,390,320])
def test_follow_up_scope_categories_60_is_qualified_and_downloads(browser,ui_url,width,tmp_path):
    page=browser.new_page(viewport={'width':width,'height':1000},accept_downloads=True)
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    ids=[f'S{width}{suffix}' for suffix in ['M','N','P','L','Z','E','T','O','H','R']]
    cases=[('已完成',None),('未开始',0),('进行中',95),('已完成',59),('已录入',0),
           ('已完成',60),('进行中',60),('缺考',20),('已录入',100),('进行中',None)]
    try:
        seed(page,ui_url,[row(sid,name=f'合成跟进{suffix}',cls=f'跟进{width}班',status=status,rate=rate)
                         for suffix,(sid,(status,rate)) in enumerate(zip(ids,cases,strict=True))])
        select_class(page,ui_url,ids[0])
        playwright.expect(page.locator('[data-stat="qualified"]')).to_have_text('4人')
        page.locator('[data-action="follow-open"]').click()
        dialog=page.locator('#follow-up-dialog')
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(8)
        playwright.expect(dialog.locator('.dialog-header p')).to_contain_text(f'跟进{width}班')
        playwright.expect(dialog.locator('#follow-kind-missing span')).to_have_text('2')
        dialog.locator('#follow-kind-at_60').click()
        assert dialog.locator('#follow-kind-at_90').count()==0
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(2)
        assert dialog.locator('#follow-content .badge.success').count()==2
        playwright.expect(dialog.locator('.issue-summary')).to_contain_text('恰好60已合格')
        with page.expect_download() as boundary_download:
            dialog.locator('[data-action="follow-export"][data-format="xlsx"]').click()
        assert '恰好60' in boundary_download.value.suggested_filename
        boundary_sheet=read_workbook(Path(boundary_download.value.path()).read_bytes(),'synthetic.xlsx')[0]
        assert boundary_sheet.rows==3
        assert {boundary_sheet.cell(i,0).text() for i in (1,2)}=={ids[5],ids[6]}
        assert all(boundary_sheet.cell(i,5).value==60 for i in (1,2))
        assert all('恰好60' in boundary_sheet.cell(i,7).text() for i in (1,2))
        dialog.locator('#follow-kind-in_progress').click()
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(3)
        with page.expect_download() as result:
            dialog.locator('[data-action="follow-export"][data-format="csv"]').click()
        records=list(csv.DictReader(io.StringIO(Path(result.value.path()).read_text(encoding='utf-8-sig'))))
        assert {record['学号'] for record in records}=={ids[2],ids[6],ids[9]}
        assert next(record for record in records if record['学号']==ids[6])['合格情况']=='已合格（≥60）'
        dialog.locator('#follow-kind-missing').click()
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(2)
        with page.expect_download() as result:
            dialog.locator('[data-action="follow-export"][data-format="xlsx"]').click()
        sheet=read_workbook(Path(result.value.path()).read_bytes(),'synthetic.xlsx')[0]
        assert sheet.rows==3 and all(sheet.cell(i,5).value is None for i in (1,2))
        dialog.locator('#follow-search').fill(ids[9])
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(1)
        playwright.expect(dialog.locator('#follow-search')).to_have_value(ids[9])
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        box=dialog.bounding_box()
        assert box['x']>=0 and box['width']<=width
        page.keyboard.press('Escape')
        playwright.expect(dialog).not_to_be_visible()
        playwright.expect(page.locator('[data-action="follow-open"]')).to_be_focused()
        assert not errors,errors
    finally:
        page.close()


def test_follow_up_export_contains_all_pages_and_legacy_error_is_not_zero(browser,ui_url):
    page=browser.new_page(viewport={'width':390,'height':900},accept_downloads=True)
    try:
        ids=[f'FOLLOWPAGE{i:03}' for i in range(25)]
        seed(page,ui_url,[row(sid,cls='跟进分页班',status='已完成',rate=None) for sid in ids])
        select_class(page,ui_url,ids[0])
        page.locator('[data-action="follow-open"]').click()
        dialog=page.locator('#follow-up-dialog')
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(20)
        dialog.locator('[data-action="follow-next"]').click()
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(5)
        with page.expect_download() as result:
            dialog.locator('[data-action="follow-export"][data-format="xlsx"]').click()
        assert read_workbook(Path(result.value.path()).read_bytes(),'synthetic.xlsx')[0].rows==26
        dialog.locator('.dialog-header [data-action="follow-close"]').click()
        def legacy(route):
            response=route.fetch()
            data=response.json()
            data.pop('follow_up_api_version',None)
            route.fulfill(response=response,json=data)
        page.route('**/api/health',legacy)
        page.locator('[data-action="follow-open"]').click()
        playwright.expect(dialog.locator('[role="alert"]')).to_contain_text('Ctrl+C')
        playwright.expect(dialog.locator('#follow-kind-pending span')).to_have_text('—')
        assert dialog.locator('#follow-content [data-row-key]').count()==0
        playwright.expect(dialog.locator('[data-action="follow-export"][data-format="xlsx"]')).to_be_disabled()
    finally:
        page.close()
