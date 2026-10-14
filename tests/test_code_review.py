"""Review regressions: malformed inputs, cleanup faults, snapshots and DB guards.

All writes target temporary SQLite databases; no real student data is read.
"""
import asyncio
import hashlib
import io
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from server import app as app_module
from server import database
from server.app import create_app
from server.database import (
    bump_revision,
    connection,
    init_db,
    read_snapshot,
    revision,
    schema_statements,
)
from server.excel_reader import Cell, Sheet, WorkbookError, read_workbook, xml_parse
from server.request_limits import RequestBodyLimit
from server.roster import rate_value
from server.score_import import clean_rate, result_rate
from tests.test_app import HEADERS, WRITE, add, commit, preview, row, xlsx
from tests.test_roster_removal import removal_preview, remove
from tests.test_scores import commit as score_commit
from tests.test_scores import preview as score_preview


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/'review.sqlite3',testing=True)) as value:
        yield value


def stage(client):
    return next(client.app.state.db_path.parent.glob('.staging/*'))


@pytest.mark.parametrize(('rate','expected'),[(89.995,90),(90.005,90.01),(.005,.01),(0,0),(100,100)])
def test_input_rounding_consistent_for_roster_score_file_and_manual_edit(client,rate,expected):
    assert rate_value(Cell(rate))==clean_rate(Cell(rate))==expected
    add(client,[row(rate=rate)])
    student=client.get('/api/students/000001').json()
    assert student['score_rate']==expected
    r=client.patch('/api/students/000001',headers=WRITE,json={'version':student['version'],'status':'已录入','score_rate':rate})
    assert r.status_code==200 and r.json()['score_rate']==expected


@pytest.mark.parametrize('cell',[Cell(90,formula=True),Cell(None,formula=True),Cell(None,error=True),Cell(float('nan')),Cell(float('inf'))])
def test_formula_error_and_nonfinite_scores_never_become_implicit_empty(cell):
    for reader in (rate_value,clean_rate):
        with pytest.raises(ValueError):
            reader(cell)
    if cell.formula or cell.error:
        with pytest.raises(ValueError):
            result_rate(cell)


def test_blank_formula_metadata_is_retained_and_row_is_not_skipped():
    sheet=Sheet('合成')
    sheet.put(0,0,Cell('学号'))
    sheet.put(1,0,Cell(None,formula=True))
    assert sheet.cell(1,0).formula and sheet.has_content(1)
    assert sheet.rows==2


@pytest.mark.parametrize('encoding',['utf-8','utf-16','utf-16-le','utf-16-be','utf-32','utf-32-le','utf-32-be'])
def test_xml_dtd_entity_encoding_bypass_rejected(encoding):
    source='<!DOCTYPE root [<!ENTITY sid "000001">]><root>&sid;</root>'
    with pytest.raises(WorkbookError,match='DTD'):
        xml_parse(source.encode(encoding))


def test_normal_utf16_xml_remains_supported():
    assert xml_parse('<root>合成正常内容</root>'.encode('utf-16')).text=='合成正常内容'


def test_duplicate_cell_coordinates_and_zip_members_rejected():
    original=xlsx([HEADERS,row()])
    for duplicate_member in (False,True):
        output=io.BytesIO()
        with ZipFile(io.BytesIO(original)) as source, ZipFile(output,'w',ZIP_DEFLATED) as target:
            for name in source.namelist():
                content=source.read(name)
                if not duplicate_member and name.endswith('sheet1.xml'):
                    content=content.replace(b'</sheetData>',b'<row r="2"><c r="A2" t="inlineStr"><is><t>OTHER</t></is></c></row></sheetData>')
                target.writestr(name,content)
            if duplicate_member:
                with pytest.warns(UserWarning):
                    target.writestr('xl/workbook.xml',source.read('xl/workbook.xml'))
        with pytest.raises(WorkbookError,match='重复|重名'):
            read_workbook(output.getvalue(),'synthetic.xlsx')


def test_unusual_zip_compression_rejected_before_unbounded_expansion():
    output=io.BytesIO()
    with ZipFile(output,'w',ZIP_BZIP2) as archive:
        archive.writestr('xl/workbook.xml',b'<workbook/>')
    with pytest.raises(WorkbookError,match='压缩'):
        read_workbook(output.getvalue(),'synthetic.xlsx')


@pytest.mark.parametrize('version',['999','broken','0','-1'])
def test_unsupported_database_is_rejected_before_journal_mode_change(tmp_path,version):
    path=tmp_path/'future.sqlite3'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('INSERT INTO metadata VALUES(?,?)',('schema_version',version))
        con.execute('CREATE TABLE unrelated(value TEXT)')
        con.execute("INSERT INTO unrelated VALUES('合成保留')")
    before=hashlib.sha256(path.read_bytes()).digest()
    with pytest.raises(RuntimeError,match='版本'):
        init_db(path)
    assert hashlib.sha256(path.read_bytes()).digest()==before
    with sqlite3.connect(path) as con:
        assert con.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
        assert con.execute('SELECT value FROM unrelated').fetchone()[0]=='合成保留'


def test_schema_install_is_atomic_and_trigger_statements_are_preserved(tmp_path,monkeypatch):
    path=tmp_path/'broken.sqlite3'
    monkeypatch.setattr(database,'SCHEMA',database.SCHEMA+'\nCREATE TABLE invalid(\n')
    with pytest.raises(RuntimeError,match='不完整'):
        init_db(path)
    with sqlite3.connect(path) as con:
        assert not con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    script="CREATE TABLE sample(value TEXT);\nCREATE TRIGGER guard BEFORE INSERT ON sample BEGIN SELECT RAISE(ABORT,'x;y'); END;\n"
    assert len(list(schema_statements(script)))==2


def test_excluded_ids_cannot_be_recreated_by_old_or_direct_database_writes(client):
    add(client,[row(),row('000002')])
    p=removal_preview(client).json()
    assert remove(client,p).status_code==200
    init_db(client.app.state.db_path)
    with connection(client.app.state.db_path) as con:
        class_id=con.execute('SELECT class_id FROM students LIMIT 1').fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError,match='Excluded'):
            con.execute('INSERT INTO students(student_id,name,class_id) VALUES(?,?,?)',('000001','不能重建的合成学生',class_id))
        with pytest.raises(sqlite3.IntegrityError,match='Excluded'):
            con.execute('UPDATE students SET student_id=? WHERE student_id=?',('000001','000002'))
    assert client.get('/api/stats').json()['students']==1


def test_removal_rechecks_identity_without_trusting_only_global_revision(client):
    add(client)
    p=removal_preview(client).json()
    with sqlite3.connect(client.app.state.db_path) as con:
        con.execute('UPDATE students SET name=? WHERE student_id=?',('外部修改的合成姓名','000001'))
    assert remove(client,p).status_code==409
    assert client.get('/api/students/000001').status_code==200


@pytest.mark.parametrize(('route','body'),[
    ('/api/students/export.xlsx',{'all_students':1}),
    ('/api/students/export.xlsx',{'class_ids':[True]}),
    ('/api/students/export.xlsx',{'all_students':True,'expected_revision':False}),
    ('/api/students/000001',{'version':True,'status':'已完成','score_rate':90}),
    ('/api/scores/batches/1/revert',{'confirm':'yes'}),
    ('/api/removals/commit',{'preview_id':'a'*32,'confirm':'true','confirmation':'确认剔除','expected_count':0}),
])
def test_json_force_flags_and_ids_are_not_silently_coerced(client,route,body):
    response=client.patch(route,headers=WRITE,json=body) if '/000001' in route else client.post(route,headers=WRITE,json=body)
    assert response.status_code==422
    assert client.get('/api/stats').json()['students']==0


@pytest.mark.parametrize('kind',['roster','score'])
def test_cleanup_permission_errors_do_not_misreport_committed_transaction(client,monkeypatch,kind):
    if kind=='roster':
        p=preview(client)
    else:
        add(client)
        p=score_preview(client,rows=[['000001','','已完成','95','','','']])
    original=Path.unlink
    def blocked(path,*args,**kwargs):
        if path.parent==stage(client) and path.name.endswith(('.bin','.upload.json','-upload.json')):
            raise PermissionError('合成占用错误')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',blocked)
    response=commit(client,p) if kind=='roster' else score_commit(client,p)
    assert response.status_code==200,response.text
    repeated=commit(client,p) if kind=='roster' else score_commit(client,p)
    assert repeated.status_code==200 and repeated.json()['idempotent']
    assert client.get('/api/stats').json()['students']==1


@pytest.mark.parametrize('kind',['roster','score'])
def test_unrelated_corrupt_preview_does_not_hide_success(client,kind):
    if kind=='roster':
        p=preview(client)
        suffix='.preview.json'
    else:
        add(client)
        p=score_preview(client,rows=[['000001','','已完成','95','','','']])
        suffix='.score-preview.json'
    (stage(client)/('f'*32+suffix)).write_text('[]',encoding='utf-8')
    response=commit(client,p) if kind=='roster' else score_commit(client,p)
    assert response.status_code==200,response.text


@pytest.mark.parametrize('payload',[[],None,{}, {'created_at':False},{'created_at':'bad'},{'created_at':10**400}])
def test_corrupt_staging_records_return_controlled_errors(client,payload):
    p=preview(client)
    path=stage(client)/(p['preview_id']+'.preview.json')
    path.write_text(json.dumps(payload),encoding='utf-8')
    assert commit(client,p).status_code==400
    assert client.get('/api/stats').json()['students']==0


def test_upload_hash_change_blocks_preview_and_expiry_cleanup_is_best_effort(client,monkeypatch):
    p=preview(client)
    payload=json.loads((stage(client)/(p['preview_id']+'.preview.json')).read_text(encoding='utf-8'))
    binary=stage(client)/(payload['upload_id']+'.bin')
    binary.write_bytes(binary.read_bytes()+b' changed')
    response=client.post('/api/imports/preview',headers=WRITE,json={
        'upload_id':payload['upload_id'],'sheet_index':0,'header_row':1,'mapping':payload['mapping']})
    assert response.status_code==409
    path=stage(client)/(p['preview_id']+'.preview.json')
    payload['created_at']=time.time()-4000
    path.write_text(json.dumps(payload),encoding='utf-8')
    original=Path.unlink
    def blocked(target,*args,**kwargs):
        if target==path:
            raise PermissionError('合成占用错误')
        return original(target,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',blocked)
    assert commit(client,p).status_code==410


def test_corrupt_revision_cannot_silently_reset_to_zero(client):
    add(client)
    with sqlite3.connect(client.app.state.db_path) as con:
        con.execute("UPDATE metadata SET value='bad' WHERE key='revision'")
    student=client.get('/api/students/000001').json()
    response=client.patch('/api/students/000001',headers=WRITE,json={'version':student['version'],'status':'已完成','score_rate':95})
    assert response.status_code==503
    assert client.get('/api/students/000001').json()==student


def test_stats_read_one_consistent_snapshot_during_concurrent_write(client,monkeypatch):
    add(client)
    real_connection=connection
    fired=False
    class Proxy:
        def __init__(self,con): self.con=con
        def __getattr__(self,name): return getattr(self.con,name)
        def execute(self,sql,*args):
            nonlocal fired
            result=self.con.execute(sql,*args)
            if not fired and 'AS students,' in sql:
                fired=True
                with real_connection(client.app.state.db_path) as writer:
                    writer.execute('UPDATE students SET status=?,score_rate=?',('已完成',100))
                    bump_revision(writer)
                    writer.commit()
            return result
    @contextmanager
    def wrapped(path):
        with real_connection(path) as con:
            yield Proxy(con)
    before=client.get('/api/stats').json()
    monkeypatch.setattr(app_module,'connection',wrapped)
    snapshot=client.get('/api/stats').json()
    assert fired and snapshot==before
    latest=client.get('/api/stats').json()
    assert latest['qualified']==1 and latest['revision']==before['revision']+1


def test_export_over_small_sqlite_variable_limit_and_revision_conflict(client,monkeypatch):
    add(client,[row(f'LIMIT{i:04}',rate=90,status='已完成') for i in range(45)])
    real_connection=connection
    @contextmanager
    def limited(path):
        with real_connection(path) as con:
            con.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,10)
            yield con
    monkeypatch.setattr(app_module,'connection',limited)
    ids=[f'LIMIT{i:04}' for i in range(45)]
    response=client.post('/api/students/export.xlsx',headers=WRITE,json={'student_ids':ids})
    assert response.status_code==200 and response.headers['X-Export-Students']=='45'
    response=client.post('/api/students/export.xlsx',headers=WRITE,json={'student_filter':{},'excluded_student_ids':ids[:30]})
    assert response.status_code==200 and response.headers['X-Export-Students']=='15'
    rev=client.get('/api/students').json()['revision']
    assert client.post('/api/students/export.xlsx',headers=WRITE,json={'all_students':True,'expected_revision':rev-1}).status_code==409


def test_body_limit_counts_real_chunked_bytes_and_rejects_bad_lengths():
    async def scenario(headers,parts):
        messages=[]
        chunks=iter(parts)
        async def receive():
            try:
                return {'type':'http.request','body':next(chunks),'more_body':True}
            except StopIteration:
                return {'type':'http.request','body':b'','more_body':False}
        async def send(message): messages.append(message)
        async def consumer(scope,receive,send):
            while (await receive()).get('more_body'):
                pass
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        await RequestBodyLimit(consumer,10)({'type':'http','method':'POST','headers':headers},receive,send)
        return messages[0]['status']
    assert asyncio.run(scenario([], [b'12345',b'123456']))==413
    assert asyncio.run(scenario([(b'content-length',b'-1')],[]))==400
    assert asyncio.run(scenario([(b'content-length',b'9'*5000)],[]))==413
    assert asyncio.run(scenario([(b'content-length',b'0000004')],[b'1234']))==200


def test_read_snapshot_does_not_rollback_callers_write_transaction(client):
    with connection(client.app.state.db_path) as con:
        con.execute('BEGIN IMMEDIATE')
        before=revision(con)
        with read_snapshot(con):
            bump_revision(con)
        assert con.in_transaction
        con.commit()
        assert revision(con)==before+1
