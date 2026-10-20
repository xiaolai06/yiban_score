"""Excel scope exports and strict score-boundary statistics use temporary data."""
import io
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.database import connection
from server.excel_reader import read_workbook, xml_parse
from server.score_export import build_score_workbook

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
WRITE = {'X-Requested-With': 'StudentScore'}


@pytest.fixture
def client(tmp_path):
    path = tmp_path / 'students.sqlite3'
    app = create_app(path, testing=True)
    with connection(path) as con:
        con.execute("INSERT INTO colleges(id,name) VALUES(1,'测试学院甲'),(2,'测试学院乙')")
        con.execute("INSERT INTO classes(id,college_id,name) VALUES(1,1,'测试一班'),(2,1,'测试二班'),(3,2,'测试一班')")
        cases = [('已完成', 0), ('已完成', 59.99), ('已完成', 90), ('已完成', 90.01),
                 ('已完成', 100), ('待录入', None), ('未开始', 0), ('进行中', 20)]
        for i, (status, rate) in enumerate(cases, 1):
            name = '=1+1' if i == 1 else f'测试学生{i}'
            con.execute('INSERT INTO students(student_id,name,class_id,status,score_rate) VALUES(?,?,?,?,?)',
                        (f'000{i}', name, 1, status, rate))
        for i, class_id in [(9, 2), (10, 3)]:
            con.execute('INSERT INTO students(student_id,name,class_id,status,score_rate) VALUES(?,?,?,?,?)',
                        (f'00{i:02}', f'测试学生{i}', class_id, '已完成', 95))
        con.commit()
    with TestClient(app) as value:
        yield value


def sheets(content):
    with ZipFile(io.BytesIO(content)) as archive:
        workbook = xml_parse(archive.read('xl/workbook.xml'))
        names = [sheet.attrib['name'] for sheet in workbook.findall('s:sheets/s:sheet', NS)]
        assert names[-1] == '导出说明'
        result = []
        for i, name in enumerate(names, 1):
            if name == '导出说明':
                continue  # Student-scope assertions exclude the separate statistics sheet.
            root = xml_parse(archive.read(f'xl/worksheets/sheet{i}.xml'))
            records = []
            for row in root.findall('s:sheetData/s:row', NS)[1:]:
                record = {}
                for cell in row.findall('s:c', NS):
                    if cell.attrib.get('t') == 'inlineStr':
                        value = ''.join(node.text or '' for node in cell.findall('s:is//s:t', NS))
                    else:
                        node = cell.find('s:v', NS)
                        value = node.text if node is not None else None
                    if value is not None:
                        record[cell.attrib['r'].rstrip('0123456789')] = value
                records.append(record)
            result.append((name, records, root))
        return result


def export(client, **scope):
    return client.post('/api/students/export.xlsx', headers=WRITE, json=scope)


def test_export_class_keeps_all_records_zero_null_and_text(client):
    before = client.get('/api/students').json()
    result = export(client, class_ids=[1])
    assert result.status_code == 200
    assert result.headers['x-export-students'] == '8'
    assert result.headers['content-type'].startswith('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    workbook = sheets(result.content)
    assert len(workbook) == 1
    records = workbook[0][1]
    assert records[0]['A'] == '0001' and records[0]['B'] == '=1+1'
    assert records[0]['F'] == '0.0' and records[0]['G'] == '0.0'
    assert 'F' not in records[5] and 'G' not in records[5]
    assert workbook[0][2].find('s:sheetViews/s:sheetView/s:pane', NS).attrib['state'] == 'frozen'
    assert client.get('/api/students').json() == before
    # Validate a real round trip through the existing independent Excel reader.
    parsed = read_workbook(result.content, 'scores.xlsx')
    assert len(parsed) == 2 and parsed[0].rows == 9
    assert parsed[-1].name == '导出说明'
    assert parsed[-1].cell(1, 3).value == 8


def test_college_export_splits_classes_and_deduplicates_overlapping_scopes(client):
    result = export(client, college_ids=[1], class_ids=[1, 1])
    assert result.status_code == 200
    workbook = sheets(result.content)
    assert len(workbook) == 2
    assert sum(len(rows) for _, rows, _ in workbook) == 9
    assert all('测试学院甲' in name for name, _, _ in workbook)
    assert [len(rows) for _, rows, _ in workbook] == [8, 1]
    all_result = export(client, all_students=True)
    assert len(sheets(all_result.content)) == 3
    assert all_result.headers['x-export-students'] == '10'


def test_unqualified_export_includes_missing_unfinished_but_not_settled_90(client):
    result = export(client, all_students=True, below_90=True)
    assert result.status_code == 200
    records = sheets(result.content)[0][1]
    assert [row['A'] for row in records] == ['0001', '0002', '0006', '0007', '0008']
    assert 'F' not in records[2] and 'G' not in records[2]
    assert records[2]['E'] == '待录入'
    assert records[3]['E'] == '未开始' and records[4]['E'] == '进行中'
    assert result.headers['x-export-students'] == '5'
    assert export(client, college_ids=[2], below_90=True).status_code == 400


def test_qualified_statistics_use_inclusive_thresholds_and_scope(client):
    result = client.get('/api/students?class_id=1').json()
    assert result['qualified'] == 3 and result['below_90'] == 5 and result['at_90'] == 1
    assert result['settled'] == 5 and result['total'] == 8
    assert result['average_rate'] == 68
    all_result = client.get('/api/students').json()
    assert all_result['qualified'] == 5 and all_result['below_90'] == 5
    missing = client.get('/api/students?score_filter=missing').json()
    assert missing['qualified'] == missing['settled'] == 0
    assert missing['below_90'] == missing['total'] == 1
    assert missing['average_rate'] is None


@pytest.mark.parametrize('body,code', [({}, 400), ({'college_ids': [99]}, 404),
                                     ({'class_ids': [99]}, 404), ({'class_ids': [0]}, 422),
                                     ({'class_ids': ['1 OR 1=1']}, 422), ({'page': 1}, 422)])
def test_scope_validation(client, body, code):
    assert export(client, **body).status_code == code


def test_exports_still_require_local_request_marker(client):
    assert client.post('/api/students/export.xlsx', json={'all_students': True}).status_code == 403
    assert client.post('/api/students/export.xlsx', headers={**WRITE, 'Origin': 'https://example.com'},
                       json={'all_students': True}).status_code == 403


def test_many_class_sheets_have_valid_unique_names():
    rows = [{'class_id': i, 'college_name': '同名学院', 'class_name': "'/超长同名班级:*?[\\]" * 4,
             'student_id': f'{i:05}', 'name': '测试', 'status': '已完成', 'score_rate': 96} for i in range(319)]
    workbook = sheets(build_score_workbook(rows))
    names = [name for name, _, _ in workbook]
    assert len(names) == len({name.casefold() for name in names}) == 319
    assert all(len(name) <= 31 and not any(ch in name for ch in ':*?/\\[]') for name in names)
    assert all(len(records) == 1 for _, records, _ in workbook)


def test_export_only_selected_students_not_their_whole_classes(client):
    result = export(client, student_ids=['0001', '0009', '0010', '0001'])
    assert result.status_code == 200
    workbook = sheets(result.content)
    assert len(workbook) == 3 and result.headers['x-export-students'] == '3'
    assert [record['A'] for _, rows, _ in workbook for record in rows] == ['0001', '0009', '0010']
    assert export(client, student_ids=['0001'], below_90=True).headers['x-export-students'] == '1'
    assert export(client, student_ids=['0009'], below_90=True).status_code == 400


def test_export_all_matching_students_and_exclude_unchecked_students(client):
    result = export(client, student_filter={'college_id': 1}, excluded_student_ids=['0001', '0009'])
    assert result.status_code == 200
    assert result.headers['x-export-students'] == '7'
    assert len(sheets(result.content)) == 1
    result = export(client, student_filter={'class_id': 1, 'score_filter': 'below_90'})
    assert result.status_code == 200
    assert [row['A'] for row in sheets(result.content)[0][1]] == ['0001', '0002', '0006', '0007', '0008']
    # Filtering by a name matches every page, not a page-sized subset.
    result = export(client, student_filter={'q': '测试学生'}, excluded_student_ids=['0002'])
    assert result.headers['x-export-students'] == '8'
    assert export(client, student_filter={'q': '不存在'}).status_code == 400


@pytest.mark.parametrize('scope,code', [({'student_ids': ['0001', 'not-existing']}, 404),
                                       ({'student_ids': ["0001' OR 1=1 --"]}, 404),
                                       ({'student_ids': ['']}, 422),
                                       ({'student_ids': ['0001'], 'all_students': True}, 400),
                                       ({'student_ids': ['0001'], 'student_filter': {}}, 400),
                                       ({'excluded_student_ids': ['0001'], 'all_students': True}, 400),
                                       ({'student_filter': {'score_filter': 'fake'}}, 422),
                                       ({'student_filter': {'status': 'fake'}}, 400),
                                       ({'student_filter': {'page': 1}}, 422)])
def test_student_selection_cannot_broaden_scope_or_inject_query(client, scope, code):
    assert export(client, **scope).status_code == code


def test_below_90_filter_and_statistics_match_export(client):
    result = client.get('/api/students?score_filter=below_90&page_size=1').json()
    assert result['total'] == result['below_90'] == 5
    assert result['settled'] == 2
    assert result['qualified'] == result['at_90'] == 0
    assert result['average_rate'] == pytest.approx(59.99 / 2, abs=.00501)
    exported = export(client, student_filter={'score_filter': 'below_90'})
    assert exported.headers['x-export-students'] == str(result['total'])
    full = client.get('/api/students?page_size=1').json()
    assert full['qualified'] == 5 and full['below_90'] == 5
    assert full['total'] == full['qualified'] + full['below_90']
    assert full['at_90'] <= full['qualified']
    assert client.get('/api/health').json()['directory_api_version'] >= 4


@pytest.mark.parametrize('sort', ['college_name', 'class_name'])
def test_students_can_sort_college_and_class_names(client, sort):
    ascending = client.get(f'/api/students?sort={sort}&order=asc').json()['items']
    descending = client.get(f'/api/students?sort={sort}&order=desc').json()['items']
    assert [row[sort] for row in ascending] == sorted(row[sort] for row in ascending)
    assert [row[sort] for row in descending] == sorted((row[sort] for row in descending), reverse=True)
    assert client.get('/api/students?sort=co.name;DROP+TABLE+students').status_code == 422


@pytest.mark.parametrize('status', ['待录入', '未开始', '进行中', '已完成', '已录入', '缺考', '免考'])
@pytest.mark.parametrize('rate', [None, 0, 59.99, 60, 60.01, 89.99, 90, 90.01, 100])
def test_unqualified_rule_is_shared_by_export_filter_and_count(client, status, rate):
    student_id = '0099'
    with connection(client.app.state.db_path) as con:
        con.execute('INSERT INTO students(student_id,name,class_id,status,score_rate) VALUES(?,?,?,?,?)',
                    (student_id, '状态分数边界测试', 3, status, rate))
        con.commit()
    before = client.get(f'/api/students/{student_id}').json()
    expected = rate is None or rate < 60
    result = client.get('/api/students?q=0099&score_filter=below_90').json()
    assert result['total'] == result['below_90'] == int(expected)
    assert result['qualified'] == result['at_90'] == 0
    full = client.get('/api/students?q=0099').json()
    assert full['below_90'] == int(expected)
    assert full['qualified'] == int(not expected)
    assert full['at_90'] == int(rate == 90)
    is_valid = rate is not None and status not in ['未开始', '进行中']
    assert full['settled'] == int(is_valid)
    assert full['average_rate'] == (rate if is_valid else None)
    overview = client.get('/api/stats').json()
    assert overview['pass_score'] == 60
    assert overview['completed'] == 7 + int(is_valid)
    college = next(co for co in overview['colleges_detail'] if co['id'] == 2)
    assert college['completed'] == 1 + int(is_valid)
    assert college['qualified'] == 1 + int(not expected)
    exported = export(client, student_ids=[student_id], below_90=True)
    assert exported.status_code == (200 if expected else 400)
    if expected:
        record = sheets(exported.content)[0][1][0]
        assert record['A'] == student_id and record['E'] == status
        if rate is None:
            assert 'F' not in record and 'G' not in record
        else:
            assert float(record['G']) == rate
    assert client.get(f'/api/students/{student_id}').json() == before


def test_all_missing_class_is_exported_in_full_and_not_turned_into_zero(client):
    path = client.app.state.db_path
    with connection(path) as con:
        con.execute("INSERT INTO classes(id,college_id,name) VALUES(4,2,'全员无成绩班')")
        for i in range(205):
            con.execute('INSERT INTO students(student_id,name,class_id,status,score_rate) VALUES(?,?,?,?,?)',
                        (f'missing-{i:04}', f'未录入测试{i}', 4, ['待录入', '未开始', '进行中', '缺考', '免考', '已完成'][i % 6], None))
        con.commit()
    first = client.get('/api/students?class_id=4&score_filter=below_90&page_size=8').json()
    assert first['total'] == first['below_90'] == 205
    assert first['settled'] == first['recorded'] == first['qualified'] == first['at_90'] == 0
    assert first['average_rate'] is None
    assert len(first['items']) == 8 and first['pages'] == 26
    result = export(client, class_ids=[4], below_90=True)
    assert result.status_code == 200 and result.headers['x-export-students'] == '205'
    workbook = sheets(result.content)
    assert len(workbook) == 1 and len(workbook[0][1]) == 205
    assert all('F' not in record and 'G' not in record for record in workbook[0][1])
    filtered = export(client, student_filter={'class_id': 4, 'score_filter': 'below_90'},
                      excluded_student_ids=['missing-0000', 'missing-0204'])
    assert filtered.headers['x-export-students'] == '203'
    partial = export(client, student_ids=['missing-0000', '0003', '0004'], below_90=True)
    assert [record['A'] for _, records, _ in sheets(partial.content) for record in records] == ['missing-0000']
    # A college with another qualified class must still include the no-score class.
    college = export(client, college_ids=[2], below_90=True)
    assert college.headers['x-export-students'] == '205'
    assert len(sheets(college.content)) == 1
    assert client.get('/api/students?class_id=4&score_filter=below_90&page_size=8').json() == first


def test_unqualified_csv_matches_xlsx_and_preserves_empty_rates(client):
    import csv
    result = client.get('/api/students/export.csv?class_id=1&score_filter=below_90')
    assert result.status_code == 200
    records = list(csv.DictReader(io.StringIO(result.content.decode('utf-8-sig'))))
    assert {record['学号'] for record in records} == {'0001', '0002', '0006', '0007', '0008'}
    assert next(record['得分率(%)'] for record in records if record['学号'] == '0006') == ''
