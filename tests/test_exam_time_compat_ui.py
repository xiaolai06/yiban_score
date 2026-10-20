"""Legacy files and grade-capable older services remain usable without time data."""
import csv
import io
from urllib.parse import urlsplit
from xml.etree.ElementTree import tostring
from zipfile import ZipFile

import pytest

from server.excel_reader import read_workbook, xml_parse
from server.table_export import column_name
from tests.test_app import row
from tests.test_exam_time_compat import source_bytes
from tests.test_roster_removal_ui import seed
from tests.test_ui_shell import browser as compat_browser
from tests.test_ui_shell import playwright
from tests.test_ui_shell import ui_url as compat_url

browser = compat_browser
ui_url = compat_url
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
TIME_KEYS = {'exam_started_at','exam_finished_at','answer_duration_seconds','raw_started_at','raw_finished_at',
             'best_exam_started_at','best_exam_finished_at','best_answer_duration_seconds','time_updates'}
TIME_LABELS = {'开始时间','结束时间','答题时长'}


def legacy_json(value):
    if isinstance(value,dict):
        return {key:legacy_json(child) for key,child in value.items() if key not in TIME_KEYS}
    if isinstance(value,list):
        return [legacy_json(child) for child in value]
    return value


def legacy_workbook(content):
    """Turn a synthetic current response into an actual old file, not just a version header."""
    buffer = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as source, ZipFile(buffer,'w') as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename.startswith('xl/worksheets/') and info.filename.endswith('.xml'):
                sheet = xml_parse(content)
                data = sheet.find(NS+'sheetData')
                assert data is not None
                first = data.find(NS+'row')
                assert first is not None
                headers = first.findall(NS+'c')
                omitted = {cell.attrib['r'].rstrip('0123456789') for cell in headers
                           if ''.join(node.text or '' for node in cell.iter(NS+'t')) in TIME_LABELS}
                if omitted:
                    for record in data.findall(NS+'row'):
                        for cell in list(record):
                            if cell.attrib['r'].rstrip('0123456789') in omitted:
                                record.remove(cell)
                    cols = sheet.find(NS+'cols')
                    assert cols is not None
                    for col in list(cols):
                        if column_name(int(col.attrib['min'])) in omitted:
                            cols.remove(col)
                    auto_filter = sheet.find(NS+'autoFilter')
                    assert auto_filter is not None
                    auto_filter.set('ref',f'A1:{column_name(len(headers)-len(omitted))}{len(data)}')
                    content = tostring(sheet,encoding='utf-8',xml_declaration=True)
            target.writestr(info.filename,content)
    return buffer.getvalue()


def legacy_csv(content):
    source = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
    assert source.fieldnames is not None
    headers = [header for header in source.fieldnames if header not in TIME_LABELS]
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output,fieldnames=headers)
    writer.writeheader()
    writer.writerows({key:value for key,value in record.items() if key in headers} for record in source)
    return ('\ufeff'+output.getvalue()).encode('utf-8')


def emulate_grade_compatible_old_service(page, preview_requests, *, missing_fields=False):
    def route_old(route):
        path = urlsplit(route.request.url).path
        if path == '/api/scores/preview':
            body = route.request.post_data_json
            preview_requests.append(body)
            assert not {'exam_started_at','exam_finished_at'} & body['mapping'].keys()
        response = route.fetch()
        headers = dict(response.headers)
        if path == '/api/students/export.xlsx':
            headers['x-export-workbook-version'] = '5'
            route.fulfill(response=response,headers=headers,body=legacy_workbook(response.body()))
        elif path.startswith('/api/follow-up/export.'):
            headers['x-follow-up-api-version'] = '3'
            content = legacy_csv(response.body()) if path.endswith('.csv') else legacy_workbook(response.body())
            route.fulfill(response=response,headers=headers,body=content)
        elif 'application/json' in headers.get('content-type',''):
            data = legacy_json(response.json())
            if path == '/api/scores/analyze' and missing_fields:
                assert isinstance(data,dict)
                data.pop('fields',None)
            if path == '/api/health':
                assert isinstance(data,dict)
                data.pop('exam_time_api_version',None)
                data['score_export_workbook_version'] = 5
                data['follow_up_api_version'] = 3
            route.fulfill(response=response,json=data)
        else:
            route.fulfill(response=response)
    page.route('**/api/**',route_old)


@pytest.mark.parametrize('width',[1440,390])
@pytest.mark.parametrize('with_times',[False,True])
def test_older_service_without_time_capability_imports_views_and_downloads_normally(browser,ui_url,tmp_path,width,with_times):
    page = browser.new_page(viewport={'width':width,'height':1000},accept_downloads=True)
    errors,requests = [],[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    sid = f'OLD-SERVICE-{width}-{int(with_times)}'
    try:
        seed(page,ui_url,[row(sid,name='兼容示例',cls=f'旧服务{sid}班')])
        emulate_grade_compatible_old_service(page,requests,missing_fields=with_times)
        headers,record = ['学号','状态','得分率'],[sid,'已完成',59]
        if with_times:
            headers += ['开始时间','结束时间']
            record += ['2026-10-10 11:02:15','2026-10-10 11:15:08']
        page.goto(ui_url+'/#score-import')
        page.locator('#score-file').set_input_files({'name':'旧版成绩.csv','mimeType':'text/csv',
                                                     'buffer':source_bytes(headers,[record])})
        playwright.expect(page.locator('#score-time-note')).to_contain_text('兼容旧版服务')
        playwright.expect(page.locator('#score-map-exam_started_at')).to_be_disabled()
        playwright.expect(page.locator('#score-map-exam_finished_at')).to_be_disabled()
        assert page.locator('.info-box.error').count() == 0
        page.locator('[data-action="score-header"]').click()
        playwright.expect(page.locator('[data-action="score-preview"]')).to_be_enabled()
        page.locator('[data-action="score-preview"]').click()
        playwright.expect(page.locator('#score-preview .duration-cell strong')).to_have_text('—')
        playwright.expect(page.locator('[data-action="score-commit"]')).to_be_enabled()
        assert requests and page.locator('.info-box.error').count() == 0
        page.locator('[data-action="score-commit"]').click()
        page.locator('.success-panel').wait_for()
        page.locator('[data-nav="scores"]').click()
        page.locator('#student-search').fill(sid)
        playwright.expect(page.locator('#score-pagination')).to_contain_text('共 1 条')
        playwright.expect(page.locator('.students-table .duration-value')).to_have_text('—')
        playwright.expect(page.locator('.students-table .score-value')).to_have_text('59分')
        page.locator('[data-action="check-all-students"]').click()
        page.locator('#score-list-tools .export-menu summary').click()
        playwright.expect(page.locator('.export-popover small')).to_contain_text('兼容旧版格式')
        with page.expect_download() as result:
            page.locator('[data-action="score-export-students"]').click()
        path = tmp_path/'legacy-scores.xlsx'
        result.value.save_as(path)
        book = read_workbook(path.read_bytes(),path.name)
        assert book[0].cols == 7 and book[0].cell(1,0).text() == sid and book[0].cell(1,6).value == 59
        playwright.expect(page.locator('#toast')).to_contain_text('兼容旧版格式，不含答题时间')
        assert 'error' not in (page.locator('#toast').get_attribute('class') or '').split()
        page.locator('[data-action="follow-open"]').click()
        playwright.expect(page.locator('.follow-time-note')).to_contain_text('成绩查看与原格式导出正常可用')
        playwright.expect(page.locator('#follow-content .duration-cell')).to_have_text('—')
        assert page.locator('#follow-up-dialog [role="alert"]').count() == 0
        for file_type in ['csv','xlsx']:
            with page.expect_download() as result:
                page.locator(f'[data-action="follow-export"][data-format="{file_type}"]').click()
            path = tmp_path/f'legacy-follow.{file_type}'
            result.value.save_as(path)
            if file_type == 'csv':
                with path.open(encoding='utf-8-sig',newline='') as source:
                    records = list(csv.DictReader(source))
                assert len(records) == 1 and records[0]['学号'] == sid and '答题时长' not in records[0]
            else:
                sheet = read_workbook(path.read_bytes(),path.name)[0]
                assert '答题时长' not in sheet.texts(0) and sheet.cell(1,0).text() == sid
            playwright.expect(page.locator('#toast')).to_contain_text('兼容旧版格式，不含答题时间')
            assert page.locator('#follow-up-dialog [role="alert"]').count() == 0
        page.locator('[data-action="follow-close"]').first.click()
        page.locator('[data-nav="score-import"]').click()
        page.locator('[data-action="score-audit"]').first.click()
        playwright.expect(page.locator('#score-audit .duration-cell strong')).to_have_text('—')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(tmp_path/f'legacy-service-{width}.png'),full_page=True)
        assert not errors,errors
    finally:
        page.close()


@pytest.mark.parametrize('width',[1440,390])
@pytest.mark.parametrize('optional_header',[False,True])
def test_current_service_accepts_old_files_and_empty_optional_time_cells_without_errors(browser,ui_url,tmp_path,width,optional_header):
    page = browser.new_page(viewport={'width':width,'height':1000},accept_downloads=True)
    errors = []
    page.on('pageerror',lambda error:errors.append(str(error)))
    sid = f'OLD-FILE-{width}-{int(optional_header)}'
    try:
        seed(page,ui_url,[row(sid,name='无时间样例',cls=f'无时间{sid}班')])
        headers = ['学号','状态','得分率']+(['开始时间','结束时间'] if optional_header else [])
        page.goto(ui_url+'/#score-import')
        page.locator('#score-file').set_input_files({'name':'没有时间的旧成绩.csv','mimeType':'text/csv',
            'buffer':source_bytes(headers,[[sid,'已完成',59]])})
        playwright.expect(page.locator('#score-time-note')).to_contain_text('旧报表无需补列')
        page.locator('[data-action="score-preview"]').click()
        playwright.expect(page.locator('[data-action="score-commit"]')).to_be_enabled()
        playwright.expect(page.locator('#score-preview .duration-cell strong')).to_have_text('—')
        assert page.locator('.info-box.error').count() == 0
        page.locator('[data-action="score-commit"]').click()
        page.locator('.success-panel').wait_for()
        page.locator('[data-action="score-audit"]').first.click()
        playwright.expect(page.locator('#score-audit .duration-cell strong')).to_have_text('—')
        page.locator('[data-nav="scores"]').click()
        page.locator('#student-search').fill(sid)
        playwright.expect(page.locator('#score-pagination')).to_contain_text('共 1 条')
        playwright.expect(page.locator('.students-table .score-value')).to_have_text('59分')
        playwright.expect(page.locator('.students-table .duration-value')).to_have_text('—')
        page.locator('[data-action="check-all-students"]').click()
        page.locator('#score-list-tools .export-menu summary').click()
        with page.expect_download() as result:
            page.locator('[data-action="score-export-students"]').click()
        path = tmp_path/'empty-time.xlsx'
        result.value.save_as(path)
        sheet = read_workbook(path.read_bytes(),path.name)[0]
        assert sheet.cols == 10 and sheet.cell(1,0).text() == sid
        assert all(sheet.cell(1,column).value is None for column in [7,8,9])
        page.locator('[data-action="edit-student"]').first.click()
        playwright.expect(page.locator('.exam-time-detail')).to_contain_text('开始：未提供')
        playwright.expect(page.locator('.exam-time-detail')).to_contain_text('答题时长：—')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
    finally:
        page.close()
