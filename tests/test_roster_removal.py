"""Destructive operations are tested only against temporary SQLite databases."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.excel_reader import Cell, Sheet, read_workbook
from server.roster_removal import removal_rows
from tests.test_app import WRITE, add, row, xlsx
from tests.test_app import commit as roster_commit
from tests.test_app import preview as roster_preview
from tests.test_scores import commit as score_commit
from tests.test_scores import preview as score_preview


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'removal.sqlite3', testing=True)) as c:
        yield c


def removal_preview(c, ids=None, *, rows=None, data=None, header_row=1):
    data = data if data is not None else xlsx(rows or [['学号'], *[[sid] for sid in (ids or ['000001'])]])
    a = c.post('/api/imports/analyze', headers=WRITE, files={'file': ('remove.xlsx', data)})
    assert a.status_code == 200, a.text
    response = c.post('/api/removals/preview', headers=WRITE, json={
        'upload_id': a.json()['upload_id'], 'sheet_index': 0, 'header_row': header_row})
    return response


def remove(c, p, **kw):
    return c.post('/api/removals/commit', headers=WRITE, json={
        'preview_id': p['preview_id'], 'expected_count': p['summary']['matched'],
        'confirm': True, 'confirmation': '确认剔除', **kw})


def test_removal_does_not_write_until_confirmed_and_does_not_match_names(client):
    add(client, [row(), row('000002', name='同名'), row('000003', name='同名')])
    before = client.get('/api/stats').json()
    p = removal_preview(client, rows=[['学号','姓名'], ['000001','错误姓名'], ['NOTFOUND','同名'], ['000001','重复']]).json()
    assert p['summary']['matched'] == 1
    assert p['summary']['unmatched'] == 1
    assert p['summary']['duplicate_rows'] == 1
    assert p['sample'][0]['name'] == '示例学生甲'
    assert client.get('/api/stats').json() == before
    r = remove(client, p)
    assert r.status_code == 200, r.text
    assert r.json()['batch']['deleted_count'] == 1
    assert client.get('/api/students/000001').status_code == 404
    assert client.get('/api/students').json()['total'] == 2
    assert client.get('/api/students/000002').status_code == 200
    assert client.get('/api/stats').json()['revision'] == before['revision'] + 1


@pytest.mark.parametrize('kw', [{'confirm':False}, {'confirmation':''}, {'confirmation':'确认删除'}, {'expected_count':2}])
def test_confirmation_is_required_and_count_bound(client, kw):
    add(client)
    p = removal_preview(client).json()
    assert remove(client, p, **kw).status_code in (400, 409)
    assert client.get('/api/stats').json()['students'] == 1


@pytest.mark.parametrize('bad', ['', '1e9', '坏学号', 1.5, 1234567890123456, '<script>'])
def test_invalid_ids_block_entire_batch(client, bad):
    add(client)
    p = removal_preview(client, rows=[['学号','姓名'], ['000001','可删'], [bad,'不能跳过']]).json()
    assert p['summary']['error_count'] == 1
    assert not p['can_commit']
    assert remove(client, p).status_code == 400
    assert client.get('/api/stats').json()['students'] == 1


def test_fixed_a_column_and_empty_sheet(client):
    assert removal_preview(client, rows=[['姓名','学号'], ['示例','000001']]).status_code == 400
    p = removal_preview(client, rows=[['学号']]).json()
    assert not p['can_commit']
    assert remove(client, p).status_code == 400


def test_formula_and_error_identifiers_rejected():
    for cell in (Cell('000001', formula=True), Cell('000001', error=True), Cell(True)):
        sheet = Sheet('合成', cells={(0,0): Cell('学号'), (1,0): cell}, rows=2, cols=1)
        assert removal_rows(sheet, 1)['errors']


def test_stale_preview_after_edit_is_atomic(client):
    add(client, [row(), row('000002')])
    p = removal_preview(client, ['000001', '000002']).json()
    current = client.get('/api/students/000002').json()
    assert client.patch('/api/students/000002', headers=WRITE, json={'version':current['version'],'status':'已完成','score_rate':95}).status_code == 200
    assert remove(client, p).status_code == 409
    assert client.get('/api/stats').json()['students'] == 2


def test_idempotency_concurrency_and_restart(client):
    add(client)
    p = removal_preview(client).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: remove(client, p), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert {r.json()['idempotent'] for r in responses} == {False, True}
    db = client.app.state.db_path
    with TestClient(create_app(db, testing=True)) as restarted:
        r = remove(restarted, p)
        assert r.status_code == 200 and r.json()['idempotent']
        assert restarted.get('/api/removals').json()['total'] == 1
        p2 = roster_preview(restarted)
        assert p2['summary']['excluded'] == 1
        assert p2['sample'][0]['action'] == 'excluded'
        assert roster_commit(restarted, p2).status_code == 200
        assert restarted.get('/api/stats').json()['students'] == 0


def test_cleanup_all_personal_history_and_revert_guard(client):
    add(client, [row(), row('000002'), row('000003',college='其他学院')])
    score = score_preview(client, rows=[['000001','','已完成','95','','',''], ['000002','','已完成','80','','',''], ['ABSENT','','已完成','90','','','']])
    result = score_commit(client, score, skip_issues=True)
    assert result.status_code == 200
    batch = result.json()['batch']['id']
    old_survivor = client.get('/api/students/000002').json()
    p = removal_preview(client, ['000001','ABSENT','000003']).json()
    assert p['summary']['score_rows'] == 2
    assert p['summary']['change_rows'] == 1
    assert remove(client, p).status_code == 200
    with sqlite3.connect(client.app.state.db_path) as con:
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
        ids = ('000001', 'ABSENT', '000003')
        assert not con.execute('SELECT 1 FROM students WHERE student_id IN (?,?,?)', ids).fetchall()
        assert not con.execute('SELECT 1 FROM student_changes WHERE student_id IN (?,?,?)', ids).fetchall()
        assert not con.execute('SELECT 1 FROM score_import_rows WHERE student_id IN (?,?,?)', ids).fetchall()
        assert con.execute('SELECT COUNT(*) FROM classes').fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM colleges').fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM excluded_students').fetchone()[0] == 3
    assert client.get('/api/students/000002').json() == old_survivor
    assert client.get(f'/api/scores/batches/{batch}/rows').json()['total'] == 1
    assert '000001' not in client.get(f'/api/scores/batches/{batch}/export.csv').text
    assert client.get('/api/scores/issues').json()['total'] == 0
    r = client.post(f'/api/scores/batches/{batch}/revert', headers=WRITE, json={'confirm':True})
    assert r.status_code == 409
    assert client.get('/api/students/000002').json() == old_survivor
    score2 = score_preview(client, rows=[['000001','','已完成','100','','','']])
    assert score2['summary']['update'] == 0
    assert client.get('/api/students').json()['total'] == 1


def test_zero_match_registers_ids_and_repeat_is_noop_for_students(client):
    add(client)
    p = removal_preview(client, ['ABSENT']).json()
    assert p['summary']['matched'] == 0 and p['can_commit']
    assert remove(client, p).status_code == 200
    before = client.get('/api/students/000001').json()
    p = removal_preview(client, ['ABSENT']).json()
    assert p['summary']['already_excluded'] == 1
    assert remove(client, p).status_code == 200
    assert client.get('/api/students/000001').json() == before
    p = roster_preview(client, [row('ABSENT')])
    assert p['summary']['excluded'] == 1
    assert roster_commit(client, p).status_code == 200
    assert client.get('/api/stats').json()['students'] == 1


def test_complete_csv_not_limited_to_sample_safe_and_header_controls(client):
    ids = [f'ID{i:04}' for i in range(130)]
    p = removal_preview(client, rows=[['标题'], ['学号'], [None], *[[sid] for sid in ids], ['学号']], header_row=2).json()
    assert len(p['sample']) == 25
    assert p['summary']['repeated_headers'] == 1
    assert p['summary']['blank_rows'] == 1
    content = client.get(f'/api/removals/{p["preview_id"]}/preview.csv')
    assert len(list(csv.reader(io.StringIO(content.content.decode('utf-8-sig'))))) == 131
    assert remove(client, p).status_code == 200
    assert client.get(f'/api/removals/{p["preview_id"]}/preview.csv').status_code == 404
    assert client.get('/api/removals?page=99&page_size=1').json()['page'] == 1


def test_transaction_rollback_on_sql_failure(client):
    add(client, [row(), row('000002')])
    p = removal_preview(client, ['000001','000002']).json()
    with sqlite3.connect(client.app.state.db_path) as con:
        con.execute("CREATE TRIGGER reject_removal BEFORE DELETE ON students WHEN OLD.student_id='000002' BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        remove(client, p)
    with sqlite3.connect(client.app.state.db_path) as con:
        assert con.execute('SELECT COUNT(*) FROM students').fetchone()[0] == 2
        assert con.execute('SELECT COUNT(*) FROM excluded_students').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM removal_batches').fetchone()[0] == 0


def test_expiry_and_boundary(client):
    p = removal_preview(client).json()
    assert client.post('/api/removals/commit', json={'preview_id':p['preview_id'],'expected_count':0,'confirm':True,'confirmation':'确认剔除'}).status_code == 403
    stage = next(client.app.state.db_path.parent.glob('.staging/*'))
    path = stage / (p['preview_id']+'.removal-preview.json')
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['created_at'] = time.time() - 3700
    path.write_text(json.dumps(payload),encoding='utf-8')
    assert remove(client, p).status_code == 410


def test_bulk_removal_over_sqlite_parameter_limit_and_no_unrelated_pruning(client):
    ids = [f'BULK{i:06}' for i in range(1205)]
    with sqlite3.connect(client.app.state.db_path) as con:
        con.execute("INSERT INTO colleges(id,name) VALUES(1,'批量学院'),(2,'原有空学院')")
        con.execute("INSERT INTO classes(id,college_id,name) VALUES(1,1,'批量班'),(2,2,'原有空班')")
        con.executemany('INSERT INTO students(student_id,name,class_id) VALUES(?,?,1)', [(sid, '@合成名字') for sid in ids])
    p = removal_preview(client, ids).json()
    assert p['summary']['matched'] == 1205
    content = client.get(f'/api/removals/{p["preview_id"]}/preview.csv').content.decode('utf-8-sig')
    assert "'@合成名字" in content
    assert remove(client, p).status_code == 200
    assert client.get('/api/stats').json()['students'] == 0
    assert client.get('/api/students/export.csv').text.count('BULK') == 0
    with sqlite3.connect(client.app.state.db_path) as con:
        assert con.execute('SELECT COUNT(*) FROM excluded_students').fetchone()[0] == 1205
        assert con.execute('SELECT id FROM classes').fetchall() == [(2,)]
        assert con.execute('SELECT id FROM colleges').fetchall() == [(2,)]


def test_removal_history_is_not_truncated(client):
    for index in range(12):
        p = removal_preview(client, [f'ABSENT{index}']).json()
        assert remove(client, p).status_code == 200
    first = client.get('/api/removals?page=1&page_size=10').json()
    last = client.get('/api/removals?page=2&page_size=10').json()
    assert first['total'] == 12 and len(first['items']) == 10
    assert last['total'] == 12 and len(last['items']) == 2
    assert len({b['id'] for b in first['items']+last['items']}) == 12


def test_supplied_private_xls_structure_only():
    path = Path(__file__).parents[2] / '学生名单列表 (29).xls'
    if not path.exists():
        pytest.skip('Private removal example not available')
    sheets = read_workbook(path.read_bytes(), path.name)
    result = removal_rows(sheets[0], 1)
    assert result['source_rows'] == 854
    assert not result['errors']
    assert len(result['rows']) + result['duplicate_rows'] == 854
