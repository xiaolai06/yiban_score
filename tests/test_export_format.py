"""Workbook styling/statistics regressions use only synthetic temporary records."""
import io
import math
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from server.database import connection
from server.excel_reader import read_workbook, xml_parse
from server.score_export import build_score_workbook, export_statistics
from server.table_export import build_table_workbook
from server.xlsx_styles import CHINESE_FONT, NUMBER_FONT
from tests.test_score_exports import client as export_client
from tests.test_score_exports import export

client = export_client
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def required(element: ET.Element, path: str) -> ET.Element:
    node = element.find(path, NS)
    assert node is not None, path
    return node


def summary(content):
    book = read_workbook(content, 'synthetic.xlsx')
    assert book[-1].name == '导出说明'
    assert book[-1].cell(0, 3).text() == '导出人数'
    sheet = book[-1]
    return book, [sheet.cell(1, col).value for col in range(3, 10)]


def test_total_summary_includes_90_and_positive_unfinished_score(client):
    before = client.get('/api/students').json()
    r = export(client, all_students=True)
    assert r.status_code == 200
    assert r.headers['X-Export-Workbook-Version'] == '6'
    assert client.get('/api/health').json()['score_export_workbook_version'] == 6
    book, counts = summary(r.content)
    assert len(book) == 4
    assert counts == pytest.approx([10, 7, 3, .7, 5, 5/7, .5])
    assert sum(sheet.rows-1 for sheet in book[:-1]) == counts[0]
    # Site qualification and export both use >=60, independent of progress.
    assert before['qualified'] == 5
    assert client.get('/api/students').json() == before
    assert book[-1].cell(2, 3).value == 8
    assert book[-1].cell(2, 4).value == 5
    assert book[-1].cell(2, 7).value == 3
    assert book[-1].cell(2, 8).value == .6


def test_college_scope_deduplicated_and_rates_weighted_by_people(client):
    r = export(client, college_ids=[1], class_ids=[1, 1])
    book, counts = summary(r.content)
    assert len(book) == 3
    assert counts == pytest.approx([9, 6, 3, 6/9, 4, 4/6, 4/9])
    assert counts[5] != (.6 + 1) / 2  # Never average unequal class rates.


def test_selected_students_and_filtered_export_statistics_do_not_broaden_scope(client):
    r = export(client, student_ids=['0001', '0003', '0009', '0003'])
    _, counts = summary(r.content)
    assert counts == pytest.approx([3, 2, 1, 2/3, 2, 1, 2/3])
    r = export(client, student_filter={'college_id':1}, excluded_student_ids=['0001','0009'])
    _, counts = summary(r.content)
    assert counts == pytest.approx([7, 5, 2, 5/7, 3, .6, 3/7])
    r = export(client, all_students=True, below_90=True)
    _, counts = summary(r.content)
    assert counts == pytest.approx([5, 2, 3, .4, 0, 0, 0])


def test_unfinished_high_score_is_qualified_everywhere_and_not_exported_as_unqualified(client):
    with connection(client.app.state.db_path) as con:
        con.execute('INSERT INTO students(student_id,name,class_id,status,score_rate) VALUES(?,?,?,?,?)',
                    ('SUMMARY-UNFINISHED', '合成未完成高分', 1, '未开始', 100))
        con.commit()
    before = client.get('/api/students/SUMMARY-UNFINISHED').json()
    book, counts = summary(export(client, all_students=True, below_90=True).content)
    assert counts == pytest.approx([5, 2, 3, .4, 0, 0, 0])
    assert not any(sheet.cell(index,0).text() == 'SUMMARY-UNFINISHED'
                   for sheet in book[:-1] for index in range(1,sheet.rows))
    assert client.get('/api/students?score_filter=below_90').json()['below_90'] == 5
    assert client.get('/api/students').json()['qualified'] == client.get('/api/stats').json()['qualified'] == 6
    assert client.get('/api/students/SUMMARY-UNFINISHED').json() == before


@pytest.mark.parametrize('rate',[None, 0])
def test_no_participants_show_undefined_pass_rate_not_false_zero(client, rate):
    ids = ['0006'] if rate is None else ['0001','0007']
    book, counts = summary(export(client, student_ids=ids).content)
    assert counts == [len(ids), 0, len(ids), 0, 0, '—', 0]
    assert book[-1].cell(1, 8).text() == '—'
    assert book[0].cell(1, 5).value == (None if rate is None else 0)
    assert '参与人数为0时显示“—”' in ' '.join(cell.text() for cell in book[-1].cells.values())


@pytest.mark.parametrize('status',['待录入','未开始','进行中','已完成','已录入','缺考','免考'])
@pytest.mark.parametrize('rate',[None, 0, .01, 59.99, 60, 60.01, 89.99, 90, 90.01, 100])
def test_export_summary_uses_numeric_rule_independent_of_status(status, rate):
    student = {'class_id':1, 'student_id':'000001', 'name':'合成姓名', 'college_name':'合成学院',
               'class_name':'2026级1班', 'status':status, 'score_rate':rate}
    book, values = summary(build_score_workbook([student]))
    participating = int(rate is not None and rate > 0)
    qualified = int(rate is not None and rate >= 60)
    assert values == [1, participating, 1-participating, participating, qualified,
                      qualified if participating else '—', qualified]
    assert book[0].cell(1, 0).text() == '000001'
    assert book[0].cell(1, 4).text() == status
    assert book[0].cell(1, 6).value == rate


def test_invalid_numeric_candidates_and_empty_denominators_are_not_participation():
    counts = export_statistics([{'score_rate':value} for value in [None, 0, -1, '无成绩', True, math.nan, math.inf, 101]])
    assert counts['total'] == counts['not_participated'] == 8
    assert counts['participated'] == counts['qualified'] == 0
    assert counts['qualification_rate'] is None
    assert export_statistics([])['participation_rate'] is None
    with pytest.raises(ValueError, match='empty'):
        build_score_workbook([])


def test_styles_all_centered_and_mixed_text_digits_have_requested_fonts(client):
    content = export(client, all_students=True).content
    with ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        styles = xml_parse(archive.read('xl/styles.xml'))
        fonts = styles.findall('s:fonts/s:font', NS)
        assert [required(font, 's:name').attrib['val'] for font in fonts] == [CHINESE_FONT,CHINESE_FONT,NUMBER_FONT]
        xfs = styles.findall('s:cellXfs/s:xf', NS)
        for xf in xfs:
            assert xf.attrib['applyAlignment'] == '1'
            assert required(xf, 's:alignment').attrib['horizontal'] == 'center'
            assert required(xf, 's:alignment').attrib['vertical'] == 'center'
        formats = {item.attrib['numFmtId']:item.attrib['formatCode'] for item in styles.findall('s:numFmts/s:numFmt',NS)}
        assert formats['165'] == '0.00%'
        numeric_cells = []
        for name in archive.namelist():
            if not name.startswith('xl/worksheets/'):
                continue
            sheet = xml_parse(archive.read(name))
            assert sheet.find('.//s:f',NS) is None
            for cell in sheet.findall('s:sheetData/s:row/s:c',NS):
                xf = xfs[int(cell.attrib['s'])]
                if cell.find('s:v',NS) is not None:
                    numeric_cells.append(cell)
                    assert required(fonts[int(xf.attrib['fontId'])], 's:name').attrib['val'] == NUMBER_FONT
                for run in cell.findall('s:is/s:r',NS):
                    text = required(run, 's:t').text
                    font = required(run, 's:rPr/s:rFont').attrib['val']
                    if text and text[0].isdigit():
                        assert font == NUMBER_FONT
                    else:
                        assert font == CHINESE_FONT
        assert numeric_cells
        first_sheet = xml_parse(archive.read('xl/worksheets/sheet1.xml'))
        identifier = required(first_sheet, './/s:c[@r="A2"]')
        assert identifier.attrib['t'] == 'inlineStr'
        assert ''.join(node.text or '' for node in identifier.findall('s:is//s:t',NS)) == '0001'
        assert required(identifier, 's:is/s:r/s:rPr/s:rFont').attrib['val'] == NUMBER_FONT


def test_summary_relationships_unique_titles_and_no_formula_execution():
    rows = [{'class_id':i, 'student_id':f'00{i}', 'name':'=HYPERLINK("unsafe")',
             'college_name':'重复学院', 'class_name':'重复2026班:*?/[]\\', 'status':'已完成', 'score_rate':90}
            for i in range(4)]
    content = build_score_workbook(rows)
    book, counts = summary(content)
    assert len({sheet.name.casefold() for sheet in book}) == len(book) == 5
    assert counts == [4,4,0,1,4,1,1]
    assert book[0].cell(1,1).text() == rows[0]['name']
    with ZipFile(io.BytesIO(content)) as archive:
        workbook = xml_parse(archive.read('xl/workbook.xml'))
        sheets = workbook.findall('s:sheets/s:sheet',NS)
        assert sheets[-1].attrib['name'] == '导出说明'
        rel_ns = '{http://schemas.openxmlformats.org/package/2006/relationships}'
        relationships = xml_parse(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {item.attrib['Id']:item.attrib['Target'] for item in relationships.findall(rel_ns+'Relationship')}
        for sheet in sheets:
            rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            assert 'xl/'+targets[rid] in archive.namelist()
        summary_root = xml_parse(archive.read('xl/worksheets/sheet5.xml'))
        assert required(summary_root, 's:autoFilter').attrib['ref'] == 'A1:J6'
        assert len(summary_root.findall('s:mergeCells/s:mergeCell',NS)) == 8


def test_other_excel_table_exports_share_style_without_student_statistics():
    content = build_table_workbook(['学号','姓名2026','原得分率'], [['0000123','２０２６级1班',0],['X-1','=1+1',90]], '异常人员汇总')
    book = read_workbook(content,'issues.xlsx')
    assert len(book) == 1 and book[0].name == '异常人员汇总'
    assert book[0].cell(1,0).text() == '0000123'
    assert book[0].cell(2,1).text() == '=1+1'
    assert book[0].cell(1,2).value == 0
    with ZipFile(io.BytesIO(content)) as archive:
        styles = xml_parse(archive.read('xl/styles.xml'))
        assert all(required(xf, 's:alignment').attrib['horizontal']=='center' for xf in styles.findall('s:cellXfs/s:xf',NS))
        sheet = xml_parse(archive.read('xl/worksheets/sheet1.xml'))
        assert sheet.find('.//s:f',NS) is None
        runs = required(sheet, './/s:c[@r="B2"]').findall('s:is/s:r',NS)
        assert [(required(run, 's:t').text,required(run, 's:rPr/s:rFont').attrib['val']) for run in runs] == [
            ('２０２６',NUMBER_FONT),('级',CHINESE_FONT),('1',NUMBER_FONT),('班',CHINESE_FONT)]
