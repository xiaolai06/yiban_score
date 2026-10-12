"""Incremental score imports operate only on disposable, synthetic test data."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from tests.test_scores import H, commit, detail, preview
from tests.test_scores import client as score_client

client = score_client


def score_row(sid, rate, status='已完成'):
    return [sid, '', status, '' if rate is None else str(rate), '', '', '']


def test_default_mode_is_incremental_and_sources_change_only_for_higher_scores(client):
    first = commit(client, preview(client, [score_row('0001', 90)])).json()['batch']
    assert first['options']['update_mode'] == 'keep_highest'
    before = detail(client)
    lower = preview(client, [score_row('0001', 80)])
    assert lower['summary']['protected'] == 1
    assert lower['rows'][0]['after']['score_rate'] == 90
    assert commit(client, lower).status_code == 200
    assert detail(client) == before
    equal = preview(client, [score_row('0001', 90)])
    assert equal['summary']['unchanged'] == 1
    assert commit(client, equal).status_code == 200
    assert detail(client) == before
    higher = preview(client, [score_row('0001', 95)])
    assert higher['summary']['improved_scores'] == 1
    assert higher['summary']['new_scores'] == 0
    result = commit(client, higher).json()
    after = detail(client)
    assert after['score_rate'] == 95
    assert after['version'] == before['version'] + 1
    assert after['score_batch_id'] == result['batch']['id']
    assert client.get('/api/scores/batches').json()['total'] == 4


@pytest.mark.parametrize(('before', 'incoming', 'expected', 'action'), [
    (None, 0, 0, 'update'), (None, 85, 85, 'update'),
    (0, 0, 0, 'unchanged'), (0, 1, 1, 'update'),
    (85, 80, 85, 'protected'), (85, 85, 85, 'unchanged'),
    (85, 90, 90, 'update'), (100, 99, 100, 'protected'),
    (100, 100, 100, 'unchanged'),
])
def test_score_comparison_distinguishes_missing_and_real_zero(client, before, incoming, expected, action):
    if before is not None:
        response = client.patch('/api/students/0001', headers=H,
                                json={'version': 1, 'status': '已完成', 'score_rate': before})
        assert response.status_code == 200
    p = preview(client, [score_row('0001', incoming)])
    assert p['rows'][0]['action'] == action
    assert commit(client, p).status_code == 200
    assert detail(client)['score_rate'] == expected
    assert detail(client, '0002')['score_rate'] is None


def test_mixed_batch_adds_missing_improves_lower_and_retains_higher(client):
    commit(client, preview(client, [score_row('0001', 90), score_row('0002', 40)]))
    old = detail(client)
    p = preview(client, [score_row('0001', 80), score_row('0002', 70), score_row('0003', 60)])
    assert p['summary']['new_scores'] == 1
    assert p['summary']['improved_scores'] == 1
    assert p['summary']['protected'] == 1
    assert commit(client, p).json()['batch']['updated_count'] == 2
    assert detail(client) == old
    assert detail(client, '0002')['score_rate'] == 70
    assert detail(client, '0003')['score_rate'] == 60


@pytest.mark.parametrize('rates', [[80, 95, 90], [95, 90, 80], [90, 80, 95]])
def test_same_file_duplicates_keep_highest_independent_of_row_order(client, rates):
    p = preview(client, [score_row('0001', rate) for rate in rates])
    assert p['summary']['update'] == 1
    assert p['summary']['duplicate'] == 2
    assert p['summary']['issue_rows'] == 0
    result = commit(client, p).json()['batch']
    assert detail(client)['score_rate'] == 95
    assert detail(client)['version'] == 2
    audit = client.get(f'/api/scores/batches/{result["id"]}/rows').json()['items']
    assert len(audit) == 3
    assert [row['raw_rate'] for row in audit] == [str(rate) for rate in rates]
    assert all(row['after']['score_rate'] == 95 for row in audit)


def test_invalid_duplicate_blocks_entire_identity_even_with_valid_high_score(client):
    p = preview(client, [score_row('0001', 95), score_row('0001', 101)])
    assert p['summary']['duplicate_conflict'] == 2
    assert not p['can_commit']
    assert commit(client, p).status_code == 409
    assert detail(client)['score_rate'] is None


def test_incomplete_record_cannot_erase_or_downgrade_completed_score(client):
    commit(client, preview(client, [score_row('0001', 85)]))
    for rate in (None, 0, 99):
        before = detail(client)
        p = preview(client, [score_row('0001', rate, '进行中')])
        assert p['summary']['protected'] == 1
        assert commit(client, p).status_code == 200
        assert detail(client) == before


def test_equal_rate_can_finalize_status_without_lowering_score(client):
    assert client.patch('/api/students/0001', headers=H,
                        json={'version': 1, 'status': '进行中', 'score_rate': 88}).status_code == 200
    p = preview(client, [score_row('0001', 88)])
    assert p['summary']['status_updates'] == 1
    assert commit(client, p).status_code == 200
    assert detail(client)['score_rate'] == 88
    assert detail(client)['status'] == '已完成'


def test_retained_score_comparison_is_correct_in_export(client):
    commit(client, preview(client, [score_row('0001', 95)]))
    b = commit(client, preview(client, [score_row('0001', 80)])).json()['batch']
    export = client.get(f'/api/scores/batches/{b["id"]}/export.csv')
    rows = list(csv.DictReader(io.StringIO(export.text.lstrip('\ufeff'))))
    assert rows[0]['清洗后得分率(%)'] == '80.0'
    assert rows[0]['导入前得分率(%)'] == '95.0'
    assert rows[0]['合并后得分率(%)'] == '95.0'


def test_all_batches_and_rows_survive_pagination_restart_and_revert(client):
    for _ in range(55):
        response = commit(client, preview(client, [score_row('0001', 90)]))
        assert response.status_code == 200
    history = client.get('/api/scores/batches?page=6&page_size=10').json()
    assert history['total'] == 55
    assert [batch['id'] for batch in history['items']] == [5, 4, 3, 2, 1]
    db_path = client.app.state.db_path
    with TestClient(create_app(db_path, testing=True)) as reopened:
        assert reopened.get('/api/scores/batches').json()['total'] == 55
        assert reopened.get('/api/scores/batches/1/rows').json()['total'] == 1
        assert reopened.get('/api/scores/batches/1/export.csv').status_code == 200
        assert reopened.post('/api/scores/batches/55/revert', headers=H, json={'confirm': True}).status_code == 200
        assert reopened.get('/api/scores/batches').json()['total'] == 55
        assert reopened.get('/api/scores/batches/55/rows').json()['batch']['state'] == 'reverted'
        assert reopened.get('/api/scores/batches/55/export.csv').status_code == 200


def test_higher_import_revert_restores_previous_best_and_source(client):
    first = commit(client, preview(client, [score_row('0001', 90)])).json()['batch']
    commit(client, preview(client, [score_row('0001', 80)]))
    highest = commit(client, preview(client, [score_row('0001', 95)])).json()['batch']
    response = client.post(f'/api/scores/batches/{highest["id"]}/revert', headers=H, json={'confirm': True})
    assert response.status_code == 200
    assert detail(client)['score_rate'] == 90
    assert detail(client)['score_batch_id'] == first['id']
    assert client.get('/api/scores/batches').json()['total'] == 3
