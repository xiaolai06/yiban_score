"""Elapsed-time regressions use synthetic files and temporary databases only."""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.database import SCHEMA, connection, init_db
from server.exam_time import (
    TIME_FIELDS,
    clean_exam_time,
    elapsed_seconds,
    format_duration,
)
from server.excel_reader import Cell, read_workbook
from tests.test_app import HEADERS, row, xlsx
from tests.test_roster_removal import removal_preview
from tests.test_scores import HEAD, H, commit, detail, make_csv, preview
from tests.test_scores import client as score_client

client = score_client
START = '2026-10-10 11:02:15'
END = '2026-10-10 11:15:08'
TIME_HEAD = HEAD + ['开始时间', '结束时间']


def timed_preview(c, *, sid='0001', rate=92, start=START, end=END, status='已完成', **options):
    source = [sid, '', status, rate, '', '', '', start, end]
    return preview(c, data=make_csv([source], header=TIME_HEAD), **options)


def csv_records(response):
    assert response.status_code == 200, response.text
    return list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))


@pytest.mark.parametrize('text,expected', [
    (START, START), ('2026/10/10 11:02:15', START), ('2026年10月10日 11:02:15', START),
    ('2026-1-2 1:02', '2026-01-02 01:02:00'), ('2026-10-10T11:02:15', START),
    ('２０２６-１０-１０ １１：０２：１５', START), ('`'+START, START),
    ('2026-10-10T11:02:15Z', START+'+00:00'), ('2026-10-10T11:02:15+08:00', START+'+08:00'),
    ('', None), ('—', None), ('null', None), ('未结束', None), ('未正常交卷', None),
])
def test_time_cleaning_is_explicit(text, expected):
    assert clean_exam_time(Cell(text)) == expected


@pytest.mark.parametrize('cell', [Cell(True), Cell(123), Cell(float('nan')), Cell(float('inf')),
    Cell('2026-10-10 25:00:00'), Cell('2026-02-30 12:00:00'), Cell('2026-10-10'),
    Cell('11:02:15'), Cell('10/11/2026 11:02:15'), Cell('=NOW()'), Cell(START, formula=True),
    Cell(START, error=True), Cell('1791628800'), Cell('<img src=x onerror=alert(1)>')])
def test_invalid_time_is_not_guessed(cell):
    with pytest.raises(ValueError):
        clean_exam_time(cell)


@pytest.mark.parametrize('start,end,seconds,display', [
    (START, END, 773, '00:12:53'), (START, START, 0, '00:00:00'),
    ('2026-10-10 23:59:50', '2026-10-11 00:00:20', 30, '00:00:30'),
    (START, '2026-10-12 12:02:16', 176401, '49:00:01'),
    ('2026-10-10 11:02:15+08:00', '2026-10-10 03:15:08+00:00', 773, '00:12:53'),
    ('2026-10-10 11:02:15.100', '2026-10-10 11:02:16.900', 1, '00:00:01'),
    (None, END, None, ''), (START, None, None, ''), (None, None, None, ''),
])
def test_elapsed_zero_cross_day_long_and_missing(start, end, seconds, display):
    assert elapsed_seconds(start, end) == seconds
    assert format_duration(seconds) == display


@pytest.mark.parametrize('value', [None, '', '773', True, -1, float('nan'), float('inf')])
def test_display_never_coerces_missing_or_bad_values_to_zero(value):
    assert format_duration(value) == ''


def test_reversed_and_mixed_timezone_rejected():
    with pytest.raises(ValueError, match='早于'):
        elapsed_seconds(END, START)
    with pytest.raises(ValueError, match='时区'):
        elapsed_seconds(START, END+'+08:00')


@pytest.mark.parametrize('date_1904', [False, True])
def test_native_excel_serial_time_and_epoch(date_1904):
    base = datetime(1904, 1, 1) if date_1904 else datetime(1899, 12, 30)  # noqa: DTZ001 -- Synthetic Excel wall-time epoch.
    serial = (datetime.fromisoformat(START) - base).total_seconds() / 86400
    assert clean_exam_time(Cell(serial, 'yyyy-mm-dd hh:mm:ss'), date_1904=date_1904) == START
    with pytest.raises(ValueError):
        clean_exam_time(Cell(60, 'yyyy-mm-dd hh:mm:ss'))


def test_standard_report_l_m_mapping_not_system_evaluation_column(client):
    headers = ['序号', '用户名', '易班ID', '姓名', '学号', '昵称', '学校', '学院', '班级', '专业',
               '状态', '开始时间', '结束时间', '系统评定时间', '成绩 / 百分比', '得分率', '是否练习模式']
    row = [1, 'UNRELATED-ACCOUNT', 'UNRELATED-ACCOUNT-ID', '', '`0001', '', '', '', '', '',
           '已完成', START, END, '2026-10-20 23:59:59', '92 / 92%', 92, '否']
    p = preview(client, data=make_csv([row], enc='gb18030', header=headers))
    r = p['rows'][0]
    assert r['answer_duration_seconds'] == 773
    assert r['raw_started_at'] == START and r['raw_finished_at'] == END
    assert not p['summary']['issue_rows']
    assert commit(client, p).status_code == 200
    s = detail(client)
    assert s['exam_started_at'] == START and s['exam_finished_at'] == END and s['answer_duration_seconds'] == 773
    audit = client.get('/api/scores/batches/1/rows').json()['items'][0]
    assert audit['after']['answer_duration_seconds'] == 773
    # Full source rows are now explicitly retained for traceability, not copied
    # into the formal student identity or used as a match key.
    assert audit['source_row']['columns'][1]['value'] == 'UNRELATED-ACCOUNT'
    assert audit['source_row']['columns'][2]['value'] == 'UNRELATED-ACCOUNT-ID'
    assert not audit['yiban_id']  # Non-numeric account IDs are not guessed.
    assert 'UNRELATED-ACCOUNT' not in str(s)


def test_preview_commit_all_student_exports_and_summary_no_duration_statistics(client):
    p = timed_preview(client, rate=59)
    rows = csv_records(client.get(f'/api/scores/previews/{p["preview_id"]}/export.csv'))
    assert rows[0]['本次答题时长'] == rows[0]['合并后答题时长'] == '00:12:53'
    assert rows[0]['原开始时间'] == START and rows[0]['原结束时间'] == END
    assert rows[0]['导入前答题时长'] == ''
    assert commit(client, p).status_code == 200
    items = client.get('/api/students?q=0001').json()['items']
    assert items[0]['answer_duration_seconds'] == 773
    for scope in [{'student_ids':['0001']}, {'student_filter':{'q':'0001'}}, {'class_ids':[1]},
                  {'college_ids':[1]}, {'all_students':True}, {'all_students':True, 'below_60':True}]:
        response = client.post('/api/students/export.xlsx', json=scope, headers=H)
        assert response.headers['X-Export-Workbook-Version'] == '6'
        book = read_workbook(response.content, 'synthetic.xlsx')
        assert [book[0].cell(0, col).text() for col in range(7,10)] == ['开始时间','结束时间','答题时长']
        assert [book[0].cell(1, col).text() for col in range(7,10)] == [START, END, '00:12:53']
        assert all(not cell.formula for sheet in book for cell in sheet.cells.values())
        assert not any('时长' in book[-1].cell(0, col).text() for col in range(book[-1].cols))
    students_csv = csv_records(client.get('/api/students/export.csv?q=0001'))[0]
    assert students_csv['答题时长'] == '00:12:53' and students_csv['开始时间'] == START
    audit_csv = csv_records(client.get('/api/scores/batches/1/export.csv'))[0]
    assert audit_csv['本次答题时长'] == audit_csv['合并后答题时长'] == '00:12:53'
    assert not any('duration' in key or 'exam_' in key for key in client.get('/api/stats').json())


@pytest.mark.parametrize('start,end,status,expected', [('', '', '未开始', None),
    (START, '', '进行中', None), ('', END, '已完成', None), (START, START, '已完成', 0),
    (START, '未正常交卷', '已完成', None)])
def test_missing_partial_and_true_zero_persist_distinctly(client, start, end, status, expected):
    p = timed_preview(client, start=start, end=end, status=status)
    assert p['summary']['issue_rows'] == 0
    assert p['rows'][0]['answer_duration_seconds'] == expected
    assert commit(client, p).status_code == 200
    assert detail(client)['answer_duration_seconds'] == expected
    value = csv_records(client.get('/api/students/export.csv?q=0001'))[0]['答题时长']
    assert value == ('00:00:00' if expected == 0 else '')


@pytest.mark.parametrize('start,end', [(END, START), ('bad', END), (START, '2026-13-10 00:00:00'),
                                     (START, END+'+08:00')])
def test_bad_time_is_auditable_and_never_writes_a_score(client, start, end):
    p = timed_preview(client, start=start, end=end)
    assert p['summary']['invalid'] == 1 and not p['can_commit']
    assert p['rows'][0]['errors']
    assert commit(client, p, skip_issues=True, save_issues_only=True).status_code == 200
    assert detail(client)['score_rate'] is None and detail(client)['answer_duration_seconds'] is None
    rows = csv_records(client.get('/api/scores/batches/1/export.csv'))
    assert rows[0]['原开始时间'] == start and rows[0]['原结束时间'] == end
    assert rows[0]['合并后答题时长'] == ''


def test_same_score_backfill_repeat_lower_score_protection_and_revert(client):
    assert commit(client, preview(client)).status_code == 200
    before = detail(client)
    p = timed_preview(client)
    assert p['summary']['update'] == 1 and p['rows'][0]['after']['answer_duration_seconds'] == 773
    assert p['summary']['time_updates'] == 1 and p['summary']['status_updates'] == 0
    assert commit(client, p).status_code == 200
    backfilled = detail(client)
    assert backfilled['score_rate'] == before['score_rate'] and backfilled['status'] == before['status']
    assert backfilled['version'] == before['version'] + 1
    p = timed_preview(client)
    assert p['summary']['unchanged'] == 1
    assert commit(client, p).status_code == 200
    assert detail(client) == backfilled
    lower = timed_preview(client, rate=80, end='2026-10-10 11:03:15')
    assert lower['summary']['protected'] == 1
    assert lower['rows'][0]['answer_duration_seconds'] == 60
    assert lower['rows'][0]['after']['answer_duration_seconds'] == 773
    protected_csv = csv_records(client.get(f'/api/scores/previews/{lower["preview_id"]}/export.csv'))[0]
    assert protected_csv['本次答题时长'] == '00:01:00' and protected_csv['合并后答题时长'] == '00:12:53'
    assert commit(client, lower).status_code == 200
    assert detail(client) == backfilled
    response = client.post('/api/scores/batches/2/revert', json={'confirm':True}, headers=H)
    assert response.status_code == 200
    assert detail(client)['answer_duration_seconds'] is None
    assert detail(client)['exam_started_at'] is None and detail(client)['score_rate'] == 92


def test_higher_score_owns_its_time_no_stale_pair_and_no_time_tie_preserves(client):
    assert commit(client, timed_preview(client)).status_code == 200
    same = preview(client, rows=[['0001', '', '已完成', 92, '', '', '']])
    assert same['summary']['unchanged'] == 1
    assert commit(client, same).status_code == 200
    assert detail(client)['answer_duration_seconds'] == 773
    higher = preview(client, rows=[['0001', '', '已完成', 95, '', '', '']])
    assert commit(client, higher).status_code == 200
    assert detail(client)['score_rate'] == 95
    assert all(detail(client)[key] is None for key in TIME_FIELDS)
    p = timed_preview(client, rate=96, end='2026-10-10 11:03:15')
    assert commit(client, p).status_code == 200
    assert detail(client)['answer_duration_seconds'] == 60
    assert client.post('/api/scores/batches/4/revert', json={'confirm':True}, headers=H).status_code == 200
    assert detail(client)['score_rate'] == 95 and detail(client)['answer_duration_seconds'] is None


def test_status_advance_without_times_keeps_duration_manual_edit_preserves_and_blocks_undo(client):
    assert commit(client, timed_preview(client, status='进行中')).status_code == 200
    p = preview(client, rows=[['0001', '', '已完成', 92, '', '', '']])
    assert p['rows'][0]['after']['answer_duration_seconds'] == 773
    assert commit(client, p).status_code == 200
    student = detail(client)
    response = client.patch('/api/students/0001', json={'version':student['version'], 'status':'已录入', 'score_rate':90}, headers=H)
    assert response.status_code == 200 and response.json()['answer_duration_seconds'] == 773
    assert client.post('/api/scores/batches/2/revert', json={'confirm':True}, headers=H).status_code == 409


def test_same_score_other_time_keeps_provenance_unless_explicit_replace(client):
    assert commit(client, timed_preview(client)).status_code == 200
    p = timed_preview(client, end='2026-10-10 11:03:15')
    assert p['summary']['unchanged'] == 1
    assert p['rows'][0]['after']['answer_duration_seconds'] == 773
    assert commit(client, p).status_code == 200
    p = timed_preview(client, end='2026-10-10 11:03:15', update_mode='replace')
    assert p['summary']['update'] == 1
    assert commit(client, p).status_code == 200
    assert detail(client)['answer_duration_seconds'] == 60


@pytest.mark.parametrize('other_rate,other_end,expected', [(92, END, 'duplicate'),
    (92, '2026-10-10 11:03:15', 'duplicate_conflict'), (90, '2026-10-10 11:03:15', 'duplicate')])
def test_duplicate_attempts_keep_matching_score_and_time_or_request_review(client, other_rate, other_end, expected):
    rows = [['0001', '', '已完成', 92, '', '', '', START, END],
            ['0001', '', '已完成', other_rate, '', '', '', START, other_end]]
    p = preview(client, data=make_csv(rows, header=TIME_HEAD))
    assert p['summary'][expected] == (2 if expected == 'duplicate_conflict' else 1)
    if expected == 'duplicate':
        assert commit(client, p).status_code == 200
        assert detail(client)['score_rate'] == 92 and detail(client)['answer_duration_seconds'] == 773
    else:
        assert not p['can_commit'] and detail(client)['score_rate'] is None


def test_followup_and_historical_issue_tables_and_both_export_types(client):
    assert commit(client, timed_preview(client, rate=59)).status_code == 200
    follow = client.get('/api/follow-up?q=0001').json()
    assert follow['items'][0]['answer_duration_seconds'] == 773
    for file_type in ['csv', 'xlsx']:
        response = client.get(f'/api/follow-up/export.{file_type}?q=0001')
        if file_type == 'csv':
            assert csv_records(response)[0]['答题时长'] == '00:12:53'
        else:
            sheet = read_workbook(response.content, 'synthetic.xlsx')[0]
            col = sheet.texts(0).index('答题时长')
            assert sheet.cell(1, col).text() == '00:12:53'
    p = timed_preview(client, sid='OUTSIDE')
    assert commit(client, p, skip_issues=True, save_issues_only=True).status_code == 200
    person = client.get('/api/scores/issues').json()['items'][0]
    assert person['best_answer_duration_seconds'] == 773
    events = client.get('/api/scores/issues/occurrences', params={'key':person['key']}).json()['items']
    assert events[0]['raw_started_at'] == START and events[0]['answer_duration_seconds'] == 773
    for scope, header in [('summary','最高分记录答题时长'), ('occurrences','答题时长')]:
        for file_type in ['csv','xlsx']:
            response = client.get(f'/api/scores/issues/export.{file_type}?scope={scope}')
            if file_type == 'csv':
                assert csv_records(response)[0][header] == '00:12:53'
            else:
                sheet = read_workbook(response.content, 'synthetic.xlsx')[0]
                assert sheet.cell(1, sheet.texts(0).index(header)).text() == '00:12:53'


@pytest.mark.parametrize('date_1904', [False, True])
def test_xlsx_native_date_cells_use_workbook_epoch(client, date_1904):
    base = datetime(1904,1,1) if date_1904 else datetime(1899,12,30)  # noqa: DTZ001 -- Synthetic Excel wall-time epoch.
    values = [(datetime.fromisoformat(time)-base).total_seconds()/86400 for time in [START, END]]
    original = xlsx([TIME_HEAD, ['0001','','已完成',92,'','','', *values]])
    buffer = io.BytesIO()
    with ZipFile(io.BytesIO(original)) as source, ZipFile(buffer, 'w') as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == 'xl/workbook.xml' and date_1904:
                data = data.replace(b'<sheets>', b'<workbookPr date1904="1"/><sheets>')
            if info.filename == 'xl/worksheets/sheet1.xml':
                data = data.replace(b'r="H2"', b'r="H2" s="1"').replace(b'r="I2"', b'r="I2" s="1"')
            target.writestr(info.filename, data)
        target.writestr('xl/styles.xml', '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs></styleSheet>')
    p = preview(client, data=buffer.getvalue(), filename='synthetic.xlsx')
    assert not p['summary']['issue_rows'], p['rows']
    assert p['rows'][0]['exam_started_at'] == START and p['rows'][0]['answer_duration_seconds'] == 773


def test_additive_v2_upgrade_preserves_records_audits_and_is_idempotent(tmp_path):
    db = tmp_path/'old.sqlite3'
    legacy = '\n'.join(line for line in SCHEMA.splitlines() if not any(field in line for field in TIME_FIELDS))
    with sqlite3.connect(db) as con:
        con.executescript(legacy)
        con.execute("INSERT INTO colleges(id,name) VALUES(1,'合成学院')")
        con.execute("INSERT INTO classes(id,college_id,name) VALUES(1,1,'合成一班')")
        con.execute("INSERT INTO students(student_id,name,class_id,status,score_rate,version) VALUES('0001','合成甲',1,'已完成',92,7)")
        con.execute("INSERT INTO student_changes(student_id,action,before_json,after_json) VALUES('0001','score_edit','{}','{}')")
    init_db(db)
    with connection(db) as con:
        row = dict(con.execute('SELECT * FROM students').fetchone())
        assert row['score_rate'] == 92 and row['version'] == 7 and row['status'] == '已完成'
        assert all(row[field] is None for field in TIME_FIELDS)
        assert con.execute('SELECT COUNT(*) FROM student_changes').fetchone()[0] == 1
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
        rev = con.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]
        assert rev == '1'
    init_db(db)
    with connection(db) as con:
        assert con.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0] == rev
    with TestClient(create_app(db, testing=True)) as c:
        assert c.get('/api/health').json()['exam_time_api_version'] == 1
        assert c.get('/api/students/0001').json()['answer_duration_seconds'] is None


def test_roster_and_removal_previews_include_current_time_without_mutation(client):
    assert commit(client, timed_preview(client)).status_code == 200
    before = detail(client)
    data = xlsx([HEADERS, row('0001', name='示例甲')])
    upload = client.post('/api/imports/analyze', headers=H, files={'file':('roster.xlsx', data)}).json()
    sheet = upload['sheets'][upload['selected_sheet']]
    response = client.post('/api/imports/preview', headers=H, json={
        'upload_id':upload['upload_id'], 'sheet_index':upload['selected_sheet'],
        'header_row':sheet['header_row'], 'mapping':sheet['mapping']})
    assert response.status_code == 200
    assert response.json()['sample'][0]['answer_duration_seconds'] == 773
    result = removal_preview(client, ['0001']).json()
    assert result['sample'][0]['answer_duration_seconds'] == 773
    exported = csv_records(client.get(f'/api/removals/{result["preview_id"]}/preview.csv'))[0]
    assert exported['答题时长'] == '00:12:53' and exported['开始时间'] == START
    assert detail(client) == before  # No roster commit or deletion was authorized here.
