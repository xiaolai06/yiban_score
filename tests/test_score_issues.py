"""Exception summaries are read-only projections of durable, synthetic imports."""
import csv
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.excel_reader import read_workbook
from tests.test_scores import H, commit, detail, preview
from tests.test_scores import client as issue_client

client = issue_client


def outside(rate, sid='0099', name='名单外甲', status='已完成'):
    return [sid, name, status, str(rate), '', '报表学院', '报表一班']


def save_issues(client, rows):
    p = preview(client, rows)
    response = commit(client, p, skip_issues=True, save_issues_only=not p['can_commit'])
    assert response.status_code == 200, response.text
    return response.json()['batch']


def test_incremental_highest_score_counts_and_tied_source_are_retained(client):
    before = detail(client)
    batches = [save_issues(client, [outside(rate)]) for rate in [85, 70, 95, 95]]
    result = client.get('/api/scores/issues').json()
    assert result['total'] == 1 and result['occurrences'] == 4
    person = result['items'][0]
    assert person['student_id'] == '0099'
    assert person['best_score_rate'] == 95
    assert person['best_batch_id'] == batches[2]['id']
    assert person['occurrence_count'] == person['batch_count'] == 4
    assert detail(client) == before
    assert client.get('/api/stats').json()['students'] == 3
    assert client.get('/api/students/0099').status_code == 404
    rows = client.get('/api/scores/issues/occurrences', params={'key': person['key']}).json()['items']
    assert [row['raw_rate'] for row in rows] == ['95', '95', '70', '85']


def test_exception_only_save_requires_both_explicit_confirmation_flags(client):
    p = preview(client, [outside(88)])
    assert not p['can_commit'] and p['can_save_issues']
    assert commit(client, p, save_issues_only=True).status_code == 409
    assert commit(client, p, skip_issues=True).status_code == 400
    assert client.get('/api/scores/issues').json()['total'] == 0
    response = commit(client, p, skip_issues=True, save_issues_only=True)
    assert response.status_code == 200
    assert response.json()['batch']['updated_count'] == 0
    assert response.json()['batch']['options']['save_issues_only']
    assert client.get('/api/scores/issues').json()['occurrences'] == 1


def test_mixed_import_saves_anomalies_and_does_not_accept_exception_only_flag(client):
    p = preview(client, [['0001', '', '已完成', '90', '', '', ''], outside(88)])
    assert commit(client, p, skip_issues=True, save_issues_only=True).status_code == 400
    assert detail(client)['score_rate'] is None
    assert commit(client, p, skip_issues=True).status_code == 200
    assert detail(client)['score_rate'] == 90
    assert client.get('/api/scores/issues').json()['total'] == 1


def test_same_confirmation_is_idempotent_but_new_import_is_another_occurrence(client):
    p = preview(client, [outside(88)])
    first = commit(client, p, skip_issues=True, save_issues_only=True)
    repeated = commit(client, p, skip_issues=True, save_issues_only=True)
    assert first.status_code == repeated.status_code == 200
    assert repeated.json()['idempotent']
    assert client.get('/api/scores/issues').json()['occurrences'] == 1
    save_issues(client, [outside(88)])
    assert client.get('/api/scores/issues').json()['occurrences'] == 2


def test_invalid_identifiers_are_not_guessed_or_merged_by_name(client):
    save_issues(client, [outside(80, sid='bad/id'), outside(90, sid='', name='名单外甲')])
    save_issues(client, [outside(85, sid='bad/id')])
    result = client.get('/api/scores/issues').json()
    assert result['total'] == result['occurrences'] == 3
    assert len({row['key'] for row in result['items']}) == 3
    assert all(row['student_id'] == '' for row in result['items'])


def test_real_zero_and_placeholder_missing_are_distinct(client):
    save_issues(client, [outside(0)])
    save_issues(client, [outside(0, status='未开始')])
    person = client.get('/api/scores/issues').json()['items'][0]
    assert person['best_score_rate'] == 0
    rows = client.get('/api/scores/issues/occurrences', params={'key': person['key']}).json()['items']
    assert rows[0]['raw_rate'] == '0' and rows[0]['score_rate'] is None
    assert rows[1]['score_rate'] == 0


def test_invalid_score_is_preserved_but_not_used_as_highest(client):
    save_issues(client, [outside(90), outside(130, sid='0100')])
    save_issues(client, [outside(101), outside('NaN', sid='0100')])
    people = {person['student_id']: person for person in client.get('/api/scores/issues').json()['items']}
    assert people['0099']['best_score_rate'] == 90
    assert people['0100']['best_score_rate'] is None
    rows = client.get('/api/scores/issues/occurrences', params={'key': 'student:0100'}).json()['items']
    assert {row['raw_rate'] for row in rows} == {'130', 'NaN'}


def test_identity_conflicts_preserve_all_names_and_filter_does_not_reduce_best_score(client):
    save_issues(client, [['0001', '错误姓名', '已完成', '95', '', '', '']])
    save_issues(client, [['0001', '示例甲', '未知状态', '70', '', '', '']])
    result = client.get('/api/scores/issues', params={'kind': 'invalid', 'q': '错误姓名'}).json()
    assert result['total'] == 1 and result['occurrences'] == 2
    person = result['items'][0]
    assert set(person['names']) == {'错误姓名', '示例甲'}
    assert person['best_score_rate'] == 95
    assert set(person['kinds']) == {'name_conflict', 'invalid'}
    assert detail(client)['score_rate'] is None


def test_duplicate_conflict_preserves_every_original_row(client):
    save_issues(client, [outside(80), outside(95)])
    person = client.get('/api/scores/issues').json()['items'][0]
    assert person['kinds'] == ['duplicate_conflict']
    assert person['occurrence_count'] == 2 and person['batch_count'] == 1
    assert person['best_score_rate'] == 95


def test_correction_and_revert_do_not_erase_historical_anomalies(client):
    batch = save_issues(client, [['0001', '错误姓名', '已完成', '80', '', '', '']])
    assert client.post(f'/api/scores/batches/{batch["id"]}/revert', headers=H, json={'confirm': True}).status_code == 200
    commit(client, preview(client, [['0001', '示例甲', '已完成', '90', '', '', '']]))
    result = client.get('/api/scores/issues').json()
    assert result['total'] == result['occurrences'] == 1
    assert result['items'][0]['best_score_rate'] == 80
    assert detail(client)['score_rate'] == 90
    occurrence = client.get('/api/scores/issues/occurrences', params={'key': 'student:0001'}).json()['items'][0]
    assert occurrence['batch_state'] == 'reverted'


def test_exports_include_all_filtered_people_and_occurrences_not_only_page(client):
    save_issues(client, [outside(80, sid=f'{number:05}', name=f'名单外{number}') for number in range(100, 125)])
    save_issues(client, [outside(95, sid='00100', name='名单外100')])
    paged = client.get('/api/scores/issues?page_size=10&page=3').json()
    assert paged['total'] == 25 and len(paged['items']) == 5
    summary = client.get('/api/scores/issues/export.csv').text.lstrip('\ufeff')
    people = list(csv.DictReader(io.StringIO(summary)))
    assert len(people) == 25
    assert next(person for person in people if person['学号'] == '00100')['最高记录得分率(%)'] == '95.0'
    details = client.get('/api/scores/issues/export.csv?scope=occurrences').text.lstrip('\ufeff')
    assert len(list(csv.DictReader(io.StringIO(details)))) == 26
    selected = client.get('/api/scores/issues/export.csv?q=名单外100&kind=unmatched').text.lstrip('\ufeff')
    assert len(list(csv.DictReader(io.StringIO(selected)))) == 1
    per_person = client.get('/api/scores/issues/export.csv?key=student:00100&scope=occurrences').text.lstrip('\ufeff')
    assert len(list(csv.DictReader(io.StringIO(per_person)))) == 2


def test_excel_exports_keep_leading_zero_ids_zero_score_and_literal_names(client):
    save_issues(client, [outside(0, name='=1+1')])
    result = client.get('/api/scores/issues/export.xlsx')
    assert result.status_code == 200
    sheet = read_workbook(result.content, 'issues.xlsx')[0]
    assert sheet.cell(1, 0).value == '0099'
    assert sheet.cell(1, 1).value == '=1+1' and not sheet.cell(1, 1).formula
    assert sheet.cell(1, 4).value == 0
    csv_result = client.get('/api/scores/issues/export.csv').text
    assert "'=1+1" in csv_result
    details = client.get('/api/scores/issues/export.xlsx?scope=occurrences')
    assert read_workbook(details.content, 'issues.xlsx')[0].rows == 2


def test_existing_durable_rows_are_available_after_restart_without_migration(client):
    batch = save_issues(client, [outside(80)])
    with TestClient(create_app(client.app.state.db_path, testing=True)) as reopened:
        person = reopened.get('/api/scores/issues').json()['items'][0]
        assert person['best_batch_id'] == batch['id']
        assert person['occurrence_count'] == 1


@pytest.mark.parametrize('url', [
    '/api/scores/issues?kind=wrong', '/api/scores/issues?page=0',
    '/api/scores/issues?page_size=101', '/api/scores/issues/occurrences',
    '/api/scores/issues/export.csv?scope=wrong', '/api/scores/issues/export.exe',
])
def test_invalid_query_is_rejected(client, url):
    assert client.get(url).status_code == 422


def test_read_only_endpoints_enforce_local_boundary_and_handle_empty_results(client):
    assert client.get('/api/scores/issues').json()['total'] == 0
    assert client.get('/api/scores/issues/export.csv').status_code == 200
    assert client.get('/api/scores/issues/export.xlsx').status_code == 200
    assert client.get('/api/scores/issues/occurrences?key=student:missing').status_code == 404
    assert client.get('/api/scores/issues', headers={'Host': 'example.com'}).status_code == 403
    assert client.get('/api/scores/issues/export.csv', headers={'Host': 'example.com'}).status_code == 403


def test_corrupt_saved_json_fails_without_silently_omitting_history(client):
    save_issues(client, [outside(80)])
    with sqlite3.connect(client.app.state.db_path) as con:
        con.execute("UPDATE score_import_rows SET record_json='not-json'")
    assert client.get('/api/scores/issues').status_code == 503


def test_oversized_excel_summary_is_rejected_without_truncating_history(client):
    raw_name = 'Ａ' * 32_000
    save_issues(client, [outside(80, name=raw_name)])
    summary = client.get('/api/scores/issues/export.xlsx')
    assert summary.status_code == 413
    assert '原始记录未截断' in summary.json()['detail']
    csv_result = client.get('/api/scores/issues/export.csv').text
    assert raw_name in csv_result and 'A' * 32_000 in csv_result
    occurrences = client.get('/api/scores/issues/export.xlsx?scope=occurrences')
    assert occurrences.status_code == 200
    assert read_workbook(occurrences.content, 'issues.xlsx')[0].cell(1, 2).value == raw_name


def test_nonfinite_or_out_of_range_legacy_rate_does_not_poison_summary(client):
    batch = save_issues(client, [outside(80)])
    with sqlite3.connect(client.app.state.db_path) as con:
        row = con.execute('SELECT id,record_json FROM score_import_rows WHERE batch_id=?', (batch['id'],)).fetchone()
        record = json.loads(row[1])
        record['score_rate'] = 130
        con.execute('UPDATE score_import_rows SET record_json=? WHERE id=?', (json.dumps(record), row[0]))
    assert client.get('/api/scores/issues').json()['items'][0]['best_score_rate'] is None
