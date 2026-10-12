from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.excel_reader import Cell, WorkbookError
from server.score_import import (
    clean_identifier,
    clean_rate,
    read_score_file,
    result_rate,
    score_sheet_info,
)
from tests.test_app import xlsx

HEAD=['学号','姓名','状态','得分率','成绩 / 百分比','学院','班级']
H={'X-Requested-With':'StudentScore'}

def make_csv(rows,enc='utf-8-sig',header=None):
    out=io.StringIO(newline='')
    w=csv.writer(out)
    w.writerow(header or HEAD)
    w.writerows(rows)
    return out.getvalue().encode(enc)

@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path/'scores.sqlite3',testing=True)
    with TestClient(app) as c:
        with sqlite3.connect(app.state.db_path) as con:
            con.execute("INSERT INTO colleges(id,name) VALUES(1,'示例学院')")
            con.execute("INSERT INTO classes(id,college_id,name) VALUES(1,1,'示例一班')")
            for sid,name in [('0001','示例甲'),('0002','示例乙'),('0003','示例丙')]:
                con.execute('INSERT INTO students(student_id,name,class_id) VALUES(?,?,1)',(sid,name))
        yield c


def preview(c,rows=None,*,data=None,filename='scores.csv',**kwargs):
    data=data if data is not None else make_csv(rows or [['`0001','示例甲','已完成','92','92 / 92%','','']])
    a=c.post('/api/scores/analyze',files={'file':(filename,data)},headers=H)
    assert a.status_code==200,a.text
    a=a.json()
    sh=a['sheets'][a['selected_sheet']]
    body={'upload_id':a['upload_id'],'sheet_index':a['selected_sheet'],'header_row':sh['header_row'],'mapping':sh['mapping'],'batch_name':'单元测试'}|kwargs
    p=c.post('/api/scores/preview',json=body,headers=H)
    assert p.status_code==200,p.text
    return p.json()


def commit(c,p,**kw):
    return c.post('/api/scores/commit',json={'preview_id':p['preview_id']}|kw,headers=H)

def detail(c,sid='0001'):
    return c.get('/api/students/'+sid).json()


@pytest.mark.parametrize('raw,expected',[('`000123','000123'),("'000123",'000123'),('０００１２３','000123'),(' 000123\u200b ','000123'),('000123.0','000123'),('000123.00','000123'),('="000123"','000123'),('00 01 23','000123'),('2610500454','2610500454'),('261000001','261000001'),('ABC_01-1','ABC_01-1')])
def test_identifier_clean(raw,expected):
    assert clean_identifier(Cell(raw))[0]==expected

@pytest.mark.parametrize('raw',['','2.6E+09','=SUM(A1:A2)','`..123','<script>','123/456','张三'])
def test_bad_identifiers(raw):
    with pytest.raises(ValueError):
        clean_identifier(Cell(raw))

@pytest.mark.parametrize('cell',[Cell(True),Cell(1.2),Cell(float('nan')),Cell(1234567890123456),Cell('123',formula=True)])
def test_unsafe_identifier_cells(cell):
    with pytest.raises(ValueError):
        clean_identifier(cell)

@pytest.mark.parametrize('raw,expected',[('92',92),('９２．５％',92.5),(' 85.50 % ',85.5),('0',0),('0.85',.85),('—',None),('',None),('.5',.5)])
def test_clean_rate(raw,expected):
    assert clean_rate(Cell(raw))==expected

@pytest.mark.parametrize('raw',['-1','101','NaN','Inf','=1+1','90分','1,20','1e2'])
def test_bad_rate(raw):
    with pytest.raises(ValueError):
        clean_rate(Cell(raw))

def test_percent_excel_and_fraction():
    assert clean_rate(Cell(.92,number_format='0.00%'))==92
    assert clean_rate(Cell('.92'),'fraction')==92
    assert clean_rate(Cell('92%'),'fraction')==92
    with pytest.raises(ValueError):
        clean_rate(Cell('92'),'fraction')
    with pytest.raises(ValueError):
        clean_rate(Cell('92',formula=True))

def test_result_field_is_not_division():
    assert result_rate(Cell('75 / 62.5%'))==62.5
    assert result_rate(Cell('92 / 92.0%'))==92
    with pytest.raises(ValueError):
        result_rate(Cell('75 / 120'))

@pytest.mark.parametrize('enc',['utf-8-sig','gb18030','utf-16'])
def test_csv_encoding(enc):
    sheets,_fmt=read_score_file(make_csv([['`0001','示例甲','已完成','92','92 / 92%','','']],enc), 'a.csv')
    assert sheets[0].cell(1,1).text()=='示例甲'
    assert score_sheet_info(sheets[0],0)['mapping']['student_id']==0

def test_missing_name_uses_roster(client):
    p=preview(client,[['`0001','','已完成','92','92 / 92%','','']])
    assert p['rows'][0]['name']=='示例甲' and not p['summary']['issue_rows']
    assert p['summary']['blank_name_rows']==1
    assert commit(client,p).status_code==200
    s=detail(client)
    assert s['score_rate']==92 and s['status']=='已完成' and s['score_batch_id']==1


def test_unfinished_placeholder_and_true_zero(client):
    p=preview(client,[['`0001','示例甲','未开始','0','0 / 0%','',''],['`0002','示例乙','进行中','0.0','0 / 0%','',''],['`0003','示例丙','已完成','0.0','0 / 0%','','']])
    assert p['summary']['placeholder_zero_rows']==2
    assert commit(client,p).status_code==200
    assert detail(client,'0001')['score_rate'] is None
    assert detail(client,'0002')['score_rate'] is None
    assert detail(client,'0003')['score_rate']==0
    stats=client.get('/api/stats').json()
    assert stats['recorded']==1 and stats['average_rate']==0
    rows=client.get('/api/scores/batches/1/rows').json()['items']
    assert rows[0]['raw_rate']=='0' and rows[0]['score_rate'] is None


def test_keep_placeholders_but_exclude_average(client):
    p=preview(client,[['0001','','未开始','0','0 / 0%','',''],['0002','','已完成','92','92 / 92%','','']],zero_policy='keep')
    assert commit(client,p).status_code==200
    assert detail(client)['score_rate']==0
    stats=client.get('/api/stats').json()
    assert stats['recorded']==2 and stats['settled']==1 and stats['average_rate']==92
    assert client.get('/api/students').json()['average_rate']==92
    assert stats['colleges_detail'][0]['average_rate']==92


def test_unmatched_requires_explicit_skip_and_never_adds(client):
    p=preview(client,[['0001','','已完成','90','90 / 90%','',''],['9999','名单外','已完成','90','90 / 90%','','']])
    assert p['summary']['unmatched']==1
    assert commit(client,p).status_code==409
    assert detail(client)['score_rate'] is None
    assert commit(client,p,skip_issues=True).status_code==200
    assert client.get('/api/stats').json()['students']==3
    assert client.get('/api/students/9999').status_code==404


def test_name_conflict_does_not_update(client):
    p=preview(client,[['0001','另一个人','已完成','90','90 / 90%','','']])
    assert p['summary']['name_conflict']==1
    assert commit(client,p).status_code==409
    assert commit(client,p,skip_issues=True).status_code==400
    assert detail(client)['name']=='示例甲'


def test_college_class_are_roster_authoritative(client):
    p=preview(client,[['0001','','已完成','90','90 / 90%','报表学院','报表班级']])
    assert not p['summary']['issue_rows']
    assert any('保留名单归属' in n for n in p['rows'][0]['notes'])
    assert commit(client,p).status_code==200
    assert detail(client)['college_name']=='示例学院' and detail(client)['class_name']=='示例一班'


def test_conflicting_score_columns(client):
    p=preview(client,[['0001','','已完成','92','90 / 90%','','']])
    assert p['summary']['invalid']==1 and not p['can_commit']
    assert detail(client)['score_rate'] is None


def test_fallback_score_column(client):
    p=preview(client,[['0001','','已完成','','92 / 92%','','']])
    assert p['rows'][0]['score_rate']==92
    assert commit(client,p).status_code==200


def test_duplicates_collapsed_or_blocked(client):
    p=preview(client,[['`0001','','已完成','92','92 / 92%','',''],['0001.0','','已完成','92','92 / 92%','','']])
    assert p['summary']['update']==1 and p['summary']['duplicate']==1
    assert commit(client,p).status_code==200
    assert detail(client)['version']==2
    p=preview(client,[['0002','','已完成','92','92 / 92%','',''],['0002','','已完成','90','90 / 90%','','']],update_mode='replace')
    assert p['summary']['duplicate_conflict']==2 and p['summary']['update']==0


def test_duplicate_with_invalid_sibling_never_selects_valid_row(client):
    p=preview(client,[['0001','','已完成','92','92 / 92%','',''],['0001','','未知状态','92','92 / 92%','','']])
    assert p['summary']['duplicate_conflict']==2 and p['summary']['update']==0


def test_idempotency_and_repeated_file_no_duplicate_update(client):
    p=preview(client)
    first=commit(client,p).json()
    second=commit(client,p).json()
    assert not first['idempotent'] and second['idempotent']
    assert detail(client)['version']==2
    p=preview(client)
    assert p['summary']['unchanged']==1 and p['prior_batches']
    assert commit(client,p).status_code==200
    assert detail(client)['version']==2
    assert client.get('/api/scores/batches').json()['total']==2


def test_stale_preview_rejected(client):
    p=preview(client)
    assert client.patch('/api/students/0002',json={'version':1,'status':'已录入','score_rate':20},headers=H).status_code==200
    assert commit(client,p).status_code==409
    assert detail(client)['score_rate'] is None


def test_protect_completed_and_explicit_override(client):
    commit(client,preview(client))
    p=preview(client,[['0001','','未开始','0','0 / 0%','','']])
    assert p['summary']['protected']==1
    assert commit(client,p).status_code==200
    assert detail(client)['score_rate']==92
    p=preview(client,[['0001','','未开始','0','0 / 0%','','']],protect_completed=False,update_mode='replace')
    assert p['summary']['update']==1
    assert commit(client,p).status_code==200
    assert detail(client)['score_rate'] is None and detail(client)['status']=='未开始'


def test_fill_missing(client):
    commit(client,preview(client))
    p=preview(client,[['0001','','已完成','80','80 / 80%','','']],update_mode='fill_missing')
    assert p['summary']['protected']==1
    assert commit(client,p).status_code==200
    assert detail(client)['score_rate']==92


def test_audit_export_and_preview_cleanup(client):
    p=preview(client)
    export=client.get('/api/scores/previews/'+p['preview_id']+'/export.csv')
    assert export.status_code==200 and '清洗后学号' in export.text
    commit(client,p)
    assert client.get('/api/scores/previews/'+p['preview_id']+'/rows').status_code==404
    assert client.get('/api/scores/batches/1/export.csv').status_code==200
    assert client.get('/api/scores/batches/1/rows').json()['items'][0]['raw_id']=='`0001'
    assert client.get('/api/scores/batches/1/rows?kind=issues').json()['total']==0
    assert client.get('/api/scores/batches/1/rows?kind=foo').status_code==400


def test_revert_restores_and_is_idempotent(client):
    commit(client,preview(client))
    assert client.post('/api/scores/batches/1/revert',json={'confirm':False},headers=H).status_code==400
    r=client.post('/api/scores/batches/1/revert',json={'confirm':True},headers=H)
    assert r.status_code==200 and r.json()['reverted_count']==1
    s=detail(client)
    assert s['status']=='待录入' and s['score_rate'] is None and s['score_batch_id'] is None and s['version']==3
    assert client.post('/api/scores/batches/1/revert',json={'confirm':True},headers=H).json()['idempotent']
    assert client.get('/api/scores/batches').json()['items'][0]['state']=='reverted'


def test_revert_conflict_is_all_or_nothing(client):
    p=preview(client,[['0001','','已完成','90','90 / 90%','',''],['0002','','已完成','92','92 / 92%','','']])
    commit(client,p)
    assert client.patch('/api/students/0002',json={'version':2,'status':'已录入','score_rate':20},headers=H).status_code==200
    assert detail(client,'0002')['score_batch_id'] is None
    assert client.post('/api/scores/batches/1/revert',json={'confirm':True},headers=H).status_code==409
    assert detail(client)['score_rate']==90
    assert detail(client,'0002')['score_rate']==20


def test_xlsx_score_reader(client):
    data=xlsx([HEAD,['0001','示例甲','已完成',92,'92 / 92%','','']])
    p=preview(client,data=data,filename='exam.xlsx')
    assert p['summary']['update']==1 and commit(client,p).status_code==200


def test_unsupported_upload_and_invalid_encoding(client):
    r=client.post('/api/scores/analyze',files={'file':('a.txt',b'abc')},headers=H)
    assert r.status_code==400
    with pytest.raises(WorkbookError):
        read_score_file(b'\xff\x00\xff','a.csv')
    r=client.post('/api/scores/analyze',files={'file':('big.csv',b'a'*(20*1024*1024+1))},headers=H)
    assert r.status_code==413


def test_v1_migration_preserves_audit_and_creates_backup(tmp_path):
    schema=(Path(__file__).parent/'fixtures/v1_schema.sql').read_text(encoding='utf-8')
    db=tmp_path/'old.sqlite3'
    with sqlite3.connect(db) as con:
        con.executescript(schema)
        con.execute("INSERT INTO colleges(id,name) VALUES(1,'示例学院')")
        con.execute("INSERT INTO classes(id,college_id,name) VALUES(1,1,'示例班')")
        con.execute("INSERT INTO students(student_id,name,class_id,status,score_rate,version) VALUES('0001','示例甲',1,'已录入',81,7)")
        con.execute("INSERT INTO student_changes(student_id,action,before_json,after_json) VALUES('0001','score_edit','{}','{}')")
    app=create_app(db,testing=True)
    with TestClient(app) as c:
        s=detail(c)
        assert s['student_id']=='0001' and s['score_rate']==81 and s['version']==7
        assert c.get('/api/health').json()['schema_version']==2
    with sqlite3.connect(db) as con:
        assert con.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert con.execute('SELECT COUNT(*) FROM student_changes').fetchone()[0]==1
    assert len(list((tmp_path/'backups').glob('*.sqlite3')))==1
    create_app(db,testing=True)
    assert len(list((tmp_path/'backups').glob('*.sqlite3')))==1


def test_csv_misaligned_columns_never_import(client):
    p=preview(client,[['0001','','已完成','90','90 / 90%','','','unexpected_extra']])
    assert p['summary']['invalid']==1 and p['summary']['update']==0
    assert any('列数' in e for e in p['rows'][0]['errors'])


def test_repeated_header_blank_lines_and_multiline_fields(client):
    data=make_csv([[], HEAD, ['`0001','','已完成','90','90 / 90%','示例\n学院','']])
    p=preview(client,data=data)
    assert p['summary']['blank_rows']==1 and p['summary']['repeated_headers']==1
    assert p['summary']['update']==1
    assert p['rows'][0]['row_number']==4


def test_bundled_templates_work_together(tmp_path):
    from tests.test_app import commit as roster_commit
    from tests.test_app import preview as roster_preview
    root=Path(__file__).parents[1]
    with TestClient(create_app(tmp_path/'templates.sqlite3',testing=True)) as c:
        rp=roster_preview(c,data=(root/'web/templates/student_template.xlsx').read_bytes(),filename='student_template.xlsx')
        assert roster_commit(c,rp).status_code==200
        sp=preview(c,data=(root/'web/templates/score_template.csv').read_bytes(),filename='score_template.csv')
        assert sp['summary']['matched_students']==4 and sp['summary']['issue_rows']==0
        assert commit(c,sp).status_code==200
        assert detail(c,'0000000001')['score_rate']==92
        assert detail(c,'000000002')['score_rate'] is None
        assert detail(c,'0000000004')['score_rate']==0
