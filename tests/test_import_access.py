"""Import protection uses isolated synthetic records and never the real database."""
import json

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.import_access import SESSION_SECONDS, password_hash
from tests.test_app import HEADERS, WRITE, add, row, xlsx
from tests.test_scores import preview as score_preview

PASSWORD = 'Synthetic-only-password-2026!'


@pytest.fixture
def locked_app(tmp_path, monkeypatch):
    config = tmp_path / 'import-access.json'
    config.write_text(json.dumps({'password_hash': password_hash(PASSWORD)}))
    monkeypatch.setenv('STUDENT_IMPORT_AUTH_FILE', str(config))
    return create_app(tmp_path / 'students.sqlite3', testing=True)


def unlock(client, password=PASSWORD):
    return client.post('/api/import-access/unlock', headers=WRITE, json={'password': password})


def test_locked_apis_cannot_upload_preview_commit_remove_or_revert(locked_app):
    with TestClient(locked_app) as client:
        assert client.get('/api/import-access').json() == {'enabled': True, 'unlocked': False}
        assert 'app.js?v=edit-access-2' in client.get('/').text
        static = client.get('/static/app.js')
        assert static.headers['cache-control'] == 'no-cache'
        assert 'views.js?v=edit-access-2' in static.text
        assert 'import_access.js?v=edit-access-2' in static.text
        for path in ['/api/imports/analyze', '/api/imports/preview', '/api/imports/commit',
                     '/api/scores/analyze', '/api/scores/preview', '/api/scores/commit',
                     '/api/removals/preview', '/api/removals/commit', '/api/scores/batches/1/revert']:
            response = client.post(path, headers=WRITE, json={})
            assert response.status_code == 401, (path, response.text)
            assert response.json()['code'] == 'import_locked'
            assert response.headers['cache-control'] == 'no-store'
        assert client.patch('/api/students/0001', headers=WRITE, json={}).status_code == 401
        for path in ['/api/imports', '/api/imports/fake/sheet', '/api/removals',
                     '/api/scores/uploads/fake/sheet', '/api/scores/previews/fake/rows',
                     '/api/scores/previews/fake/export.csv', '/api/imports/analyze/']:
            assert client.get(path).status_code == 401
        for path in ['/', '/api/health', '/api/stats', '/api/students', '/api/hierarchy',
                     '/api/scores/people', '/api/scores/issues', '/api/scores/batches']:
            assert client.get(path).status_code == 200, path
        # Public exports are still usable; validation, rather than authentication, rejects this empty request.
        assert client.post('/api/students/export.xlsx', headers=WRITE, json={}).status_code != 401


def test_verified_roster_and_score_import_round_trip_then_logout_revokes_cookie(locked_app):
    with TestClient(locked_app) as client:
        wrong = unlock(client, 'incorrect')
        assert wrong.status_code == 401
        assert not client.cookies
        result = unlock(client)
        assert result.status_code == 200
        assert 'HttpOnly' in result.headers['set-cookie']
        assert 'SameSite=strict' in result.headers['set-cookie']
        assert f'Max-Age={SESSION_SECONDS}' in result.headers['set-cookie']
        assert PASSWORD not in result.text
        add(client, [row('0001', name='示例甲')])
        preview = score_preview(client, [['0001', '示例甲', '已完成', '92', '', '', '']])
        saved = client.post('/api/scores/commit', headers=WRITE, json={
            'preview_id': preview['preview_id'], 'skip_issues': True})
        assert saved.status_code == 200, saved.text
        assert client.get('/api/stats').json()['qualified'] == 1
        cookie = client.cookies.get('student-import')
        assert client.post('/api/import-access/lock', headers=WRITE).status_code == 200
        client.cookies.set('student-import', cookie)
        assert client.get('/api/imports').status_code == 401
        assert client.get('/api/stats').json()['students'] == 1


def test_expired_tampered_and_pre_restart_sessions_fail(locked_app):
    with TestClient(locked_app) as client:
        assert unlock(client).status_code == 200
        token = client.cookies.get('student-import')
        for key in locked_app.state.import_access.sessions:
            locked_app.state.import_access.sessions[key] = 0
        assert client.get('/api/import-access').json()['unlocked'] is False
        response = client.post('/api/imports/analyze', headers=WRITE,
                               files={'file': ('synthetic.xlsx', xlsx([HEADERS, row()]))})
        assert response.status_code == 401
        assert client.get('/api/stats').json()['students'] == 0
        client.cookies.clear()
        client.cookies.set('student-import', 'forged-' + token)
        assert client.get('/api/imports').status_code == 401


def test_rate_limits_wrong_password_and_requires_write_marker(locked_app):
    with TestClient(locked_app) as client:
        assert client.post('/api/import-access/unlock', json={'password': PASSWORD}).status_code == 403
        assert client.post('/api/import-access/unlock', headers={**WRITE, 'Origin': 'https://evil.example'},
                           json={'password': PASSWORD}).status_code == 403
        for _ in range(5):
            assert unlock(client, 'wrong').status_code == 401
        response = unlock(client, 'wrong')
        assert response.status_code == 429
        assert response.headers['retry-after'] == '300'
        assert client.get('/api/students').status_code == 200


def test_edit_and_import_share_one_session_and_logout_protects_both(locked_app):
    with TestClient(locked_app) as client:
        assert unlock(client).status_code == 200
        add(client, [row('0001', name='合成编辑学生', rate=60, status='已完成')])
        original = client.get('/api/students/0001').json()
        body = {'version': original['version'], 'status': '已完成', 'score_rate': 88.5}
        client.post('/api/import-access/lock', headers=WRITE)
        assert client.get('/api/students/0001').status_code == 200
        assert client.patch('/api/students/0001', headers=WRITE, json=body).status_code == 401
        assert client.get('/api/students/0001').json() == original
        assert unlock(client).status_code == 200
        assert client.patch('/api/students/0001', headers=WRITE, json=body).status_code == 200
        assert client.get('/api/imports').status_code == 200
        saved = client.get('/api/students/0001').json()
        assert saved['score_rate'] == 88.5
        for key in locked_app.state.import_access.sessions:
            locked_app.state.import_access.sessions[key] = 0
        body.update(version=saved['version'], score_rate=100)
        assert client.patch('/api/students/0001', headers=WRITE, json=body).status_code == 401
        assert client.get('/api/students/0001').json() == saved


def test_https_cookie_is_secure_and_invalid_config_fails_closed(tmp_path, monkeypatch):
    config = tmp_path / 'access.json'
    config.write_text(json.dumps({'password_hash': password_hash(PASSWORD)}))
    monkeypatch.setenv('STUDENT_IMPORT_AUTH_FILE', str(config))
    app = create_app(tmp_path / 'db.sqlite3', public_host='score.example.org')
    with TestClient(app, base_url='https://score.example.org') as client:
        response = unlock(client)
        assert response.status_code == 200
        assert '__Host-student-import=' in response.headers['set-cookie']
        assert 'Secure' in response.headers['set-cookie']
        assert client.get('/api/imports').status_code == 200
    with TestClient(create_app(tmp_path / 'db.sqlite3', public_host='score.example.org'),
                    base_url='https://score.example.org') as client:
        client.cookies.set('__Host-student-import', response.cookies.get('__Host-student-import'))
        assert client.get('/api/imports').status_code == 401
    config.write_text('{"password_hash":"broken"}')
    with pytest.raises(RuntimeError, match='拒绝启动'):
        create_app(tmp_path / 'db.sqlite3')
