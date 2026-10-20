"""All qualification surfaces share the inclusive 60-point rule; synthetic DBs only."""
import csv
import io

import pytest

from server.database import connection
from server.excel_reader import read_workbook
from server.grade_rules import PASS_SCORE
from tests.test_score_exports import client as export_client
from tests.test_score_exports import export

client = export_client


@pytest.mark.parametrize('status', ['待录入', '未开始', '进行中', '已完成', '已录入', '缺考', '免考'])
@pytest.mark.parametrize('rate', [None, 0, 59.99, 60, 60.01, 89.99, 90, 90.01, 100])
def test_canonical_filters_counts_exports_and_legacy_alias_agree(client, status, rate):
    sid = 'BOUNDARY-60'
    with connection(client.app.state.db_path) as con:
        con.execute('INSERT INTO students(student_id,name,class_id,status,score_rate) VALUES(?,?,?,?,?)',
                    (sid, '及格边界合成学生', 3, status, rate))
        con.commit()
    before = client.get(f'/api/students/{sid}').json()
    qualified = rate is not None and rate >= PASS_SCORE
    finished = rate is not None and status not in {'未开始', '进行中'}
    all_rows = client.get('/api/students', params={'q': sid}).json()
    assert all_rows['pass_score'] == PASS_SCORE == 60
    assert all_rows['qualified'] == int(qualified)
    assert all_rows['below_60'] == all_rows['below_90'] == int(not qualified)
    assert all_rows['at_60'] == int(rate == 60)
    assert all_rows['at_90'] == int(rate == 90)  # Legacy value is not repurposed as 60.
    assert all_rows['completed'] == int(finished)

    filtered = client.get('/api/students', params={'q': sid, 'score_filter': 'below_60'}).json()
    legacy = client.get('/api/students', params={'q': sid, 'score_filter': 'below_90'}).json()
    assert filtered == legacy
    assert filtered['total'] == int(not qualified)
    csv_response = client.get('/api/students/export.csv', params={'q': sid, 'score_filter': 'below_60'})
    records = list(csv.DictReader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
    assert [record['学号'] for record in records] == ([] if qualified else [sid])

    modern_export = export(client, student_ids=[sid], below_60=True)
    legacy_export = export(client, student_ids=[sid], below_90=True)
    assert modern_export.status_code == legacy_export.status_code == (400 if qualified else 200)
    if not qualified:
        book = read_workbook(modern_export.content, 'synthetic.xlsx')
        assert book[0].cell(1, 0).text() == sid
        assert book[0].cell(1, 6).value == rate
        assert book[-1].cell(1, 7).value == 0

    book = read_workbook(export(client, student_ids=[sid]).content, 'synthetic.xlsx')
    assert book[-1].cell(0, 7).text() == '合格人数（≥60）'
    assert book[-1].cell(1, 7).value == int(qualified)
    assert book[0].cell(1, 6).value == rate
    assert client.get(f'/api/students/{sid}').json() == before


@pytest.mark.parametrize('kind,expected', [('at_60', ['PASS-60']), ('completed_low', ['LOW-60'])])
@pytest.mark.parametrize('file_type', ['csv', 'xlsx'])
def test_follow_up_boundary_and_low_score_exports_use_sixty(client, kind, expected, file_type):
    with connection(client.app.state.db_path) as con:
        for sid, rate in [('PASS-60', 60), ('LOW-60', 59.99), ('PASS-89', 89.99), ('PASS-90', 90)]:
            con.execute('INSERT INTO students(student_id,name,class_id,status,score_rate) VALUES(?,?,?,?,?)',
                        (sid, '边界分类合成学生', 3, '已完成', rate))
        con.commit()
    listing = client.get('/api/follow-up', params={'kind': kind, 'q': '-'}).json()
    assert [student['student_id'] for student in listing['items']] == expected
    assert 'at_60' in listing['counts'] and 'at_90' not in listing['counts']
    response = client.get(f'/api/follow-up/export.{file_type}', params={'kind': kind, 'q': '-'})
    assert response.status_code == 200
    assert int(response.headers['X-Follow-Up-Api-Version']) >= 3
    if file_type == 'csv':
        records = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        assert [record['学号'] for record in records] == expected
        text = response.content.decode('utf-8-sig')
    else:
        sheet = read_workbook(response.content, 'synthetic.xlsx')[0]
        assert [sheet.cell(i, 0).text() for i in range(1, sheet.rows)] == expected
        text = ' '.join(cell.text() for cell in sheet.cells.values())
    assert ('恰好60（已合格）' if kind == 'at_60' else '已完成低于60') in text
    assert '≥90' not in text and '低于90' not in text and '恰好90' not in text


@pytest.mark.parametrize('body', [{'below_60': 'yes'}, {'below_60': 1}, {'below_60': None}])
def test_new_export_flag_is_strict_boolean(client, body):
    assert export(client, all_students=True, **body).status_code == 422
