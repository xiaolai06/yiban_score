"""Synthetic source trace / current-person searches; never touch the real database."""
import csv
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.excel_reader import Cell, Sheet
from server.score_import import build_preview, clean_yiban_id
from tests.test_scores import H, commit, detail, make_csv, preview
from tests.test_scores import client as trace_client

client = trace_client


def trace_preview(client, records=None, **kwargs):
    headers = ['学号', '姓名', '状态', '得分率', '易班ID', '备注', '备注', '']
    data = make_csv(records or [['`0099', '原表甲', '已完成', 80, "'０００７７.０", '  第一行\n第二行  ', '=1+1', '']], header=headers)
    return preview(client, data=data, **kwargs)


def save(client, p):
    response = commit(client, p, skip_issues=True, save_issues_only=not p['can_commit'])
    assert response.status_code == 200, response.text
    return response.json()['batch']


@pytest.mark.parametrize(('raw', 'expected'), [('`00123', '00123'), ("'０００７７.０", '00077'),
    ('="000123"', '000123'), ('00 01 23', '000123'), ('0', '0'), ('12345678901234567890', '12345678901234567890')])
def test_yiban_digits_are_text_not_floating_point(raw, expected):
    assert clean_yiban_id(Cell(raw)) == expected


@pytest.mark.parametrize('cell', [Cell('bad/id'), Cell('1.23E+12'), Cell('1-23'), Cell(True),
                                  Cell(1234567890123456), Cell('123', formula=True), Cell('123', error=True)])
def test_unsafe_yiban_values_are_not_guessed(cell):
    with pytest.raises(ValueError):
        clean_yiban_id(cell)


def test_full_source_row_preserves_unmapped_duplicate_empty_and_multiline_cells(client):
    p = trace_preview(client)
    row = p['rows'][0]
    assert row['student_id'] == '0099' and row['yiban_id'] == '00077'
    assert row['action'] == 'unmatched'
    batch = save(client, p)
    response = client.get(f'/api/scores/batches/{batch["id"]}/rows/2')
    assert response.status_code == 200
    data = response.json()
    assert data['batch']['filename'] == 'scores.csv'
    assert data['batch']['sheet_name'] == '成绩数据'
    assert data['row'] == row
    source = data['row']['source_row']
    assert source['version'] == source['header_row'] == 1 and source['row_number'] == 2
    assert source['row_columns'] == len(source['columns']) == 8
    assert [cell['column'] for cell in source['columns']] == list('ABCDEFGH')
    assert [cell['label'] for cell in source['columns'][-3:]] == ['备注', '备注', '']
    assert source['columns'][5]['value'] == '  第一行\n第二行  '
    assert source['columns'][6]['value'] == '=1+1'
    assert source['columns'][7]['value'] is None
    # Source bytes/staging can be cleaned without losing the row projection.
    with TestClient(create_app(client.app.state.db_path, testing=True)) as reopened:
        assert reopened.get(f'/api/scores/batches/{batch["id"]}/rows/2').json() == data


def test_excel_source_metadata_and_real_zero_do_not_execute_formulas():
    sheet = Sheet('多列原始报表')
    for index, name in enumerate(['学号', '状态', '得分率', '易班ID', '附加公式', '错误值']):
        sheet.put(0, index, Cell(name))
    for index, cell in enumerate([Cell('0099'), Cell('已完成'), Cell(0), Cell(123, '000000'),
                                  Cell(12, formula=True), Cell('#DIV/0!', error=True)]):
        sheet.put(1, index, cell)
    p = build_preview(sheet, 1, {'student_id': 0, 'status': 1, 'score_rate': 2, 'yiban_id': 3}, {})
    record = p['rows'][0]
    assert record['score_rate'] == 0 and record['yiban_id'] == '000123'
    columns = record['source_row']['columns']
    assert columns[2]['value'] == 0
    assert columns[3]['number_format'] == '000000' and columns[3]['value'] == 123
    assert columns[4]['formula'] and columns[4]['value'] == 12
    assert columns[5]['error']


def test_optional_bad_account_does_not_block_valid_student_score(client):
    p = trace_preview(client, [['0001', '示例甲', '已完成', 70, 'not-an-id', '', '', '']])
    assert p['rows'][0]['yiban_id'] == '' and p['summary']['issue_rows'] == 0
    assert '易班 ID' in '；'.join(p['rows'][0]['notes'])
    save(client, p)
    assert detail(client)['score_rate'] == 70
    assert client.get('/api/scores/people?q=not-an-id').json()['total'] == 0


def test_issue_search_export_and_occurrences_include_cleaned_account_ids(client):
    batch = save(client, trace_preview(client))
    for query in ['00077', "'０００７７.０", '`0099', '００９９', 'scores.csv', '单元测试']:
        result = client.get('/api/scores/issues', params={'q': query}).json()
        assert result['total'] == 1
        assert result['items'][0]['yiban_ids'] == ['00077']
    occurrence = client.get('/api/scores/issues/occurrences', params={'key': 'student:0099'}).json()['items'][0]
    assert occurrence['yiban_id'] == '00077' and occurrence['raw_yiban_id'] == "'０００７７.０"
    assert occurrence['status'] == '已完成' and occurrence['sheet_name'] == '成绩数据'
    assert occurrence['source_row_available'] and occurrence['notes']
    assert 'source_row' not in occurrence  # Full cells are lazy-loaded, not repeated across a whole history page.
    records = csv.DictReader(io.StringIO(client.get('/api/scores/issues/export.csv?scope=occurrences').text.lstrip('\ufeff')))
    exported = next(records)
    assert exported['清洗后易班ID'] == '00077' and exported['原始整行快照'] == '已保存'
    assert int(exported['批次号']) == batch['id']
    assert '清洗后易班ID' in client.get(f'/api/scores/batches/{batch["id"]}/export.csv').text


def test_account_collisions_never_merge_anomaly_people_or_create_roster(client):
    save(client, trace_preview(client, [['0099', '同名', '已完成', 80, '777', '', '', ''],
                                       ['0100', '同名', '已完成', 90, '777', '', '', '']]))
    result = client.get('/api/scores/issues?q=777').json()
    assert result['total'] == 2 and {item['student_id'] for item in result['items']} == {'0099', '0100'}
    assert client.get('/api/scores/people?q=777').json()['total'] == 0
    assert client.get('/api/stats').json()['students'] == 3


def test_legacy_rows_stay_readable_without_fabricating_missing_source_fields(client):
    batch = save(client, trace_preview(client))
    with sqlite3.connect(client.app.state.db_path) as con:
        row_id, data = con.execute('SELECT id,record_json FROM score_import_rows').fetchone()
        record = json.loads(data)
        for key in ('yiban_id', 'raw_yiban_id', 'source_row'):
            record.pop(key)
        record['raw_rate'] = 0  # Support persisted numeric zero, not an empty string.
        con.execute('UPDATE score_import_rows SET record_json=? WHERE id=?', (json.dumps(record), row_id))
    result = client.get('/api/scores/issues').json()['items'][0]
    assert result['yiban_ids'] == [] and result['best_score_rate'] == 80
    event = client.get('/api/scores/issues/occurrences?key=student:0099').json()['items'][0]
    assert event['raw_rate'] == '0' and not event['source_row_available']
    record = client.get(f'/api/scores/batches/{batch["id"]}/rows/2').json()['row']
    assert 'source_row' not in record and 'yiban_id' not in record


def test_original_row_survives_revert_and_does_not_show_later_current_value(client):
    batch = save(client, trace_preview(client, [['0001', '错误姓名', '已完成', 80, '777', '', '', '']]))
    assert client.post(f'/api/scores/batches/{batch["id"]}/revert', headers=H, json={'confirm': True}).status_code == 200
    save(client, preview(client, [['0001', '示例甲', '已完成', 99, '', '', '']]))
    data = client.get(f'/api/scores/batches/{batch["id"]}/rows/2').json()
    assert data['batch']['state'] == 'reverted'
    assert data['row']['source_name'] == '错误姓名' and data['row']['score_rate'] == 80
    current = client.get('/api/scores/people?q=777').json()['items'][0]
    assert current['name'] == '示例甲' and current['score_rate'] == 99
    assert current['score_batch_id'] != batch['id'] and current['qualified']


def test_current_lookup_is_read_only_scoped_paginated_and_literal(client):
    save(client, trace_preview(client, [['0001', '示例甲', '进行中', 60, '00077', '', '', ''],
                                       ['0002', '示例乙', '已完成', 0, '00077', '', '', '']]))
    before = client.get('/api/health').json()['revision']
    data = client.get('/api/scores/people?q=０００７７&page_size=1&page=2').json()
    assert data['total'] == data['pages'] == 2 and data['items'][0]['student_id'] == '0002'
    assert data['items'][0]['settled'] and not data['items'][0]['qualified']
    data = client.get('/api/scores/people?q=00077&status=进行中').json()
    assert data['total'] == 1 and data['items'][0]['qualified'] and not data['items'][0]['settled']
    assert data['items'][0]['historical_yiban_ids'] == ['00077']
    for query in ['示例学院', '示例一班', '`0001']:
        assert client.get('/api/scores/people', params={'q': query}).json()['total'] >= 1
    for query in ['%', '_', "' OR 1=1 --"]:
        assert client.get('/api/scores/people', params={'q': query}).json()['total'] == 0
    assert client.get('/api/scores/people?page=999').json()['page'] == 1
    assert client.get('/api/health').json()['revision'] == before == data['revision']


@pytest.mark.parametrize(('url', 'status'), [('/api/scores/people?page=0', 422),
    ('/api/scores/people?page_size=101', 422), ('/api/scores/people?status=未知', 400),
    ('/api/scores/people?q=' + 'a' * 121, 422), ('/api/scores/batches/0/rows/2', 422),
    ('/api/scores/batches/1/rows/0', 422), ('/api/scores/batches/999/rows/2', 404)])
def test_new_query_validation(client, url, status):
    assert client.get(url).status_code == status


def test_missing_or_deleted_row_and_local_boundary(client):
    batch = save(client, trace_preview(client))
    url = f'/api/scores/batches/{batch["id"]}/rows/2'
    assert client.get(url, headers={'Host': 'example.com'}).status_code == 403
    assert client.get('/api/scores/people', headers={'Host': 'example.com'}).status_code == 403
    with sqlite3.connect(client.app.state.db_path) as con:
        con.execute('DELETE FROM score_import_rows WHERE batch_id=?', (batch['id'],))
    assert client.get(url).status_code == 404
