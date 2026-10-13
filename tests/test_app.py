from __future__ import annotations

import io
import sqlite3
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.excel_reader import Cell, StringChunks, read_workbook
from server.roster import identifier, rate_value

HEADERS=['学号','姓名','性别','院系','专业','班级','状态','得分率']
MAP={'student_id':0,'name':1,'gender':2,'college':3,'major':4,'class_name':5,'status':6,'score_rate':7}
WRITE={'X-Requested-With':'StudentScore'}
NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'

# Minimal standards-based fixture writer; no external Office installation.
def xlsx(rows, *, merges=()):
    root=ET.Element('worksheet',xmlns=NS)
    sheet_data=ET.SubElement(root,'sheetData')
    for r,row in enumerate(rows,1):
        node=ET.SubElement(sheet_data,'row',r=str(r))
        for c,value in enumerate(row):
            if value is None:
                continue
            col=''
            n=c+1
            while n:
                n,rem=divmod(n-1,26)
                col=chr(65+rem)+col
            if isinstance(value,(float,int)) and not isinstance(value,bool):
                cell=ET.SubElement(node,'c',r=f'{col}{r}',t='n')
                ET.SubElement(cell,'v').text=str(value)
            else:
                cell=ET.SubElement(node,'c',r=f'{col}{r}',t='inlineStr')
                ET.SubElement(ET.SubElement(cell,'is'),'t').text=str(value)
    if merges:
        merge_node=ET.SubElement(root,'mergeCells',count=str(len(merges)))
        for ref in merges:
            ET.SubElement(merge_node,'mergeCell',ref=ref)
    workbook=f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="名单" sheetId="1" r:id="rId1"/></sheets></workbook>'
    rels='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>'
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('xl/workbook.xml',workbook)
        z.writestr('xl/_rels/workbook.xml.rels',rels)
        z.writestr('xl/worksheets/sheet1.xml',ET.tostring(root,encoding='utf-8'))
    return out.getvalue()

def row(sid='000001', name='示例学生甲', college='示例学院A', cls='示例一班', status='', rate=None):
    return [sid,name,'女',college,'示例专业',cls,status,rate]

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/'students.sqlite3',testing=True)) as c:
        yield c

def preview(client, rows=None, *, data=None, filename='test.xlsx', mode='update', mapping=None):
    if data is None:
        data=xlsx([HEADERS,*(rows or [row()])])
    response=client.post('/api/imports/analyze',headers=WRITE,files={'file':(filename,data,'application/octet-stream')})
    assert response.status_code==200,response.text
    a=response.json()
    sh=a['sheets'][a['selected_sheet']]
    response=client.post('/api/imports/preview',headers=WRITE,json={'upload_id':a['upload_id'],
        'sheet_index':a['selected_sheet'],'header_row':sh['header_row'],'mapping':mapping or sh['mapping'],'mode':mode})
    assert response.status_code==200,response.text
    return response.json()

def commit(client, p, **kwargs):
    return client.post('/api/imports/commit',headers=WRITE,json={'preview_id':p['preview_id'],**kwargs})

def add(client, rows=None):
    p=preview(client,rows)
    r=commit(client,p)
    assert r.status_code==200,r.text
    return r.json()


def test_static_and_empty_database(client):
    assert client.get('/').status_code==200
    assert client.get('/static/app.js').status_code==200
    assert client.get('/api/health').json()['storage']=='SQLite'
    assert client.get('/api/stats').json()['average_rate'] is None
    assert client.get('/api/students').json()['pages']==1


def test_overview_qualified_counts_include_90_and_exclude_missing(client):
    empty = client.get('/api/stats').json()
    assert empty['qualified'] == 0
    assert empty['colleges_detail'] == []
    add(client, [
        row('000001', rate=89.99, status='已完成'),
        row('000002', rate=90, status='已完成'),
        row('000003', rate=90.01, status='已录入'),
        row('000004', rate=100, status='已完成'),
        row('000005', rate=0, status='已完成'),
        row('000006', rate=None, status='已完成'),
        row('000007', college='示例学院B', rate=95, status='进行中'),
        row('000008', college='示例学院B', rate=90, status='未开始'),
        row('000009', college='示例学院C', rate=None),
    ])
    stats = client.get('/api/stats').json()
    assert stats['students'] == 9
    assert stats['qualified'] == 5
    assert stats['recorded'] == 7
    colleges = {co['name']: co for co in stats['colleges_detail']}
    assert colleges['示例学院A']['qualified'] == 3
    assert colleges['示例学院B']['qualified'] == 2
    assert colleges['示例学院C']['qualified'] == 0
    assert sum(co['qualified'] for co in colleges.values()) == stats['qualified']
    # The overview follows the numeric >=90 rule; the existing details API is unchanged.
    assert client.get('/api/students').json()['qualified'] == 2
    student = client.get('/api/students/000002').json()
    response = client.patch('/api/students/000002', headers=WRITE, json={
        'version': student['version'], 'status': '已完成', 'score_rate': None,
    })
    assert response.status_code == 200
    stats = client.get('/api/stats').json()
    assert stats['qualified'] == 4
    assert sum(co['qualified'] for co in stats['colleges_detail']) == 4


def test_template_excel_and_leading_zero():
    path=Path(__file__).parents[1]/'web/templates/student_template.xlsx'
    sheets=read_workbook(path.read_bytes(),path.name)
    assert len(sheets)==2
    assert sheets[0].cell(1,0).text()=='0000000001'
    assert sheets[0].cell(2,0).text()=='000000002'


def test_valid_score_counts_exclude_missing_and_unfinished_values(client):
    add(client, [
        row('VALID001', rate=80, status='已完成'),
        row('VALID002', rate=0, status='已完成'),
        row('VALID003', rate=None, status='已完成'),
        row('VALID004', rate=None, status='待录入'),
        row('VALID005', rate=0, status='未开始'),
        row('VALID006', rate=20, status='进行中'),
        row('VALID007', rate=None, status='免考'),
        row('VALID008', rate=90.5, status='已录入'),
    ])
    stats = client.get('/api/stats').json()
    assert stats['recorded'] == 5
    assert stats['valid_scores'] == stats['settled'] == 3
    assert stats['average_rate'] == 56.83
    for page in (1, 2):
        result = client.get('/api/students', params={'page_size': 1, 'page': page}).json()
        assert len(result['items']) == 1
        assert result['valid_scores'] == result['settled'] == 3
        assert result['average_rate'] == 56.83
    for status in ('未开始', '进行中', '待录入', '免考'):
        result = client.get('/api/students', params={'status': status}).json()
        assert result['valid_scores'] == 0
        assert result['average_rate'] is None


def test_three_level_hierarchy_and_same_names(client):
    add(client,[row(),row('000002'),row('000003',college='示例学院B')])
    stats=client.get('/api/stats').json()
    assert (stats['students'],stats['colleges'],stats['classes'])==(3,2,2)
    hierarchy=client.get('/api/hierarchy').json()['colleges']
    assert hierarchy[0]['classes'][0]['name']==hierarchy[1]['classes'][0]['name']
    assert hierarchy[0]['classes'][0]['id']!=hierarchy[1]['classes'][0]['id']
    cl=hierarchy[0]['classes'][0]['id']
    assert client.get(f'/api/students?class_id={cl}').json()['total']==2
    assert client.get(f'/api/students?class_id={cl}&college_id={hierarchy[1]["id"]}').json()['total']==0


@pytest.mark.parametrize('column',[0,1,3,5])
def test_missing_required_blocks_entire_import(client,column):
    bad=row('000002')
    bad[column]=''
    p=preview(client,[row(),bad])
    assert p['summary']['error_count']==1
    assert commit(client,p).status_code==400
    assert client.get('/api/stats').json()['students']==0


def test_duplicate_student_id_blocks_import(client):
    p=preview(client,[row(),row()])
    assert p['summary']['error_count']==1
    assert commit(client,p).status_code==400
    assert client.get('/api/students').json()['total']==0


def test_reimport_preserves_rate_and_status(client):
    add(client)
    before=client.get('/api/students/000001').json()
    response=client.patch('/api/students/000001',headers=WRITE,json={'version':before['version'],'status':'已录入','score_rate':85.5})
    assert response.status_code==200
    p=preview(client,[row(cls='新班级',status='缺考',rate=0)])
    assert p['summary']['updated']==1
    assert commit(client,p).status_code==200
    result=client.get('/api/students/000001').json()
    assert result['class_name']=='新班级'
    assert result['status']=='已录入' and result['score_rate']==85.5
    assert client.get('/api/stats').json()['students']==1


def test_no_change_reimport_and_idempotent_commit(client):
    p=preview(client)
    first=commit(client,p).json()
    repeated=commit(client,p)
    assert repeated.status_code==200 and repeated.json()['idempotent'] is True
    assert first['batch']['id']==repeated.json()['batch']['id']
    p2=preview(client)
    assert p2['summary']['unchanged']==1
    assert commit(client,p2).status_code==200
    assert client.get('/api/stats').json()['students']==1


def test_name_change_requires_confirmation(client):
    add(client)
    p=preview(client,[row(name='核对后的姓名')])
    assert p['summary']['name_conflict_count']==1
    assert commit(client,p).status_code==409
    assert commit(client,p,allow_name_changes=True).status_code==200
    assert client.get('/api/students/000001').json()['name']=='核对后的姓名'


def test_skip_existing_does_not_change_identity(client):
    add(client)
    p=preview(client,[row(name='另一姓名',cls='另一班'),row('000002')],mode='skip')
    assert p['summary']['skipped_existing']==1 and p['summary']['name_conflict_count']==0
    assert commit(client,p).status_code==200
    assert client.get('/api/students/000001').json()['name']=='示例学生甲'
    assert client.get('/api/stats').json()['students']==2


def test_missing_from_new_roster_not_deleted(client):
    add(client,[row(),row('000002')])
    add(client,[row()])
    assert client.get('/api/stats').json()['students']==2


def test_stale_preview_cannot_override_edit(client):
    add(client)
    p=preview(client,[row(cls='新版班级')])
    before=client.get('/api/students/000001').json()
    assert client.patch('/api/students/000001',headers=WRITE,json={'version':before['version'],'status':'已录入','score_rate':90}).status_code==200
    assert commit(client,p).status_code==409
    assert client.get('/api/students/000001').json()['score_rate']==90


def test_optimistic_edit_conflict(client):
    add(client)
    body={'version':1,'status':'已录入','score_rate':90}
    assert client.patch('/api/students/000001',headers=WRITE,json=body).status_code==200
    body['score_rate']=10
    assert client.patch('/api/students/000001',headers=WRITE,json=body).status_code==409
    assert client.get('/api/students/000001').json()['score_rate']==90


@pytest.mark.parametrize('value',[-0.01,100.01,True,'nan'])
def test_invalid_score_rejected(client,value):
    add(client)
    response=client.patch('/api/students/000001',headers=WRITE,json={'version':1,'status':'已录入','score_rate':value})
    assert response.status_code==422
    assert client.get('/api/students/000001').json()['score_rate'] is None


def test_null_distinct_from_zero_and_filters(client):
    add(client,[row(rate=0),row('000002',rate=80),row('000003')])
    stats=client.get('/api/stats').json()
    assert stats['recorded']==2 and stats['average_rate']==40
    assert client.get('/api/students?score_filter=missing').json()['total']==1
    assert client.get('/api/students?score_filter=recorded').json()['total']==2
    assert client.get('/api/students?sort=score_rate&order=desc').json()['items'][0]['score_rate']==80
    assert client.get('/api/students?sort=score_rate&order=asc').json()['items'][-1]['score_rate'] is None


def test_pagination_and_literal_search(client):
    add(client,[row(f'{i:05d}') for i in range(1,24)])
    result=client.get('/api/students?page=2&page_size=20').json()
    assert result['total']==23 and len(result['items'])==3
    assert client.get('/api/students?page=999').json()['page']==2
    for q in ['%','_',"' OR 1=1 --"]:
        assert client.get('/api/students',params={'q':q}).json()['total']==0
    assert client.get('/api/students?q=00001').json()['total']==1
    assert client.get('/api/students?page=0').status_code==422
    assert client.get('/api/students?page_size=101').status_code==422


def test_backup_is_complete_and_persistence(tmp_path):
    path=tmp_path/'original.sqlite3'
    with TestClient(create_app(path,testing=True)) as c:
        add(c)
        response=c.get('/api/database/backup')
        assert response.status_code==200 and response.content.startswith(b'SQLite format 3')
        backup=tmp_path/'restored.sqlite3'
        backup.write_bytes(response.content)
    with TestClient(create_app(backup,testing=True)) as c2:
        assert c2.get('/api/students/000001').json()['name']=='示例学生甲'
    with TestClient(create_app(path,testing=True)) as c3:
        assert c3.get('/api/stats').json()['students']==1
    with sqlite3.connect(backup) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_security_boundaries(client,tmp_path):
    assert client.post('/api/imports/preview',json={}).status_code==403
    assert client.post('/api/imports/preview',headers={**WRITE,'Origin':'https://evil.invalid'},json={}).status_code==403
    assert client.get('/api/stats',headers={'Host':'evil.invalid'}).status_code==403
    assert client.get('/api/stats').headers['cache-control']=='no-store'
    assert "frame-ancestors 'none'" in client.get('/').headers['content-security-policy']
    with TestClient(create_app(tmp_path/'strict.sqlite3')) as strict:
        assert strict.get('/api/stats').status_code==403


def test_bad_files_and_excessive_upload(client):
    response=client.post('/api/imports/analyze',headers=WRITE,files={'file':('bad.xls',b'not an excel file')})
    assert response.status_code==400
    response=client.post('/api/imports/analyze',headers=WRITE,files={'file':('bad.csv',b'a,b,c')})
    assert response.status_code==400
    response=client.post('/api/imports/analyze',headers=WRITE,files={'file':('big.xls',b'x'*(20*1024*1024+1))})
    assert response.status_code==413


def test_blank_and_repeated_header_rows(client):
    data=xlsx([HEADERS,row(),[None]*8,HEADERS,row('000002')])
    p=preview(client,data=data)
    assert p['summary']['source_rows']==2 and p['summary']['error_count']==0
    assert any('重复表头' in w for w in p['warnings'])
    assert commit(client,p).status_code==200


def test_only_actual_merged_ranges_filled(client):
    second=row('000002')
    second[3]=None
    second[5]=None
    good=xlsx([HEADERS,row(),second],merges=['D2:D3','F2:F3'])
    p=preview(client,data=good)
    assert p['summary']['error_count']==0
    bad=xlsx([HEADERS,row(),second])
    p2=preview(client,data=bad)
    assert p2['summary']['error_count']==1


@pytest.mark.parametrize('cell,expected',[(Cell('85.5%'),85.5),(Cell(.855,'0.0%'),85.5),(Cell(.85),.85),(Cell(0),0),(Cell(None),None)])
def test_percentage_semantics(cell,expected):
    assert rate_value(cell)==expected


@pytest.mark.parametrize('value',['1.23E+09','学号1','001 002','',10**16])
def test_bad_identifiers(value):
    with pytest.raises(ValueError):
        identifier(Cell(value))


def test_identifier_text_and_format():
    assert identifier(Cell('001234567'))=='001234567'
    assert identifier(Cell('A-00123'))=='A-00123'
    assert identifier(Cell(123,'000000'))=='000123'
    with pytest.raises(ValueError):
        identifier(Cell('123',formula=True))


def test_biff_unicode_continuation():
    raw1=struct.pack('<H',4)+b'\x00AB'
    assert StringChunks([raw1,b'\x00CD']).unicode()=='ABCD'
    assert StringChunks([struct.pack('<H',2)+b'\x01'+ '学'.encode('utf-16le'),b'\x01'+'生'.encode('utf-16le')]).unicode()=='学生'
    assert StringChunks([struct.pack('<H',3)+b'\x00A',b'\x01'+'学生'.encode('utf-16le')]).unicode()=='A学生'


def test_csv_formula_injection_protection(client):
    add(client,[row(name='=1+1')])
    response=client.get('/api/students/export.csv')
    assert response.status_code==200
    assert "'=1+1" in response.text


def test_invalid_column_mapping(client):
    data=xlsx([HEADERS,row()])
    a=client.post('/api/imports/analyze',headers=WRITE,files={'file':('x.xlsx',data)}).json()
    for mapping in [{'name':1},dict(MAP,name=0),dict(MAP,student_id=999)]:
        r=client.post('/api/imports/preview',headers=WRITE,json={'upload_id':a['upload_id'],'sheet_index':0,'header_row':1,'mapping':mapping})
        assert r.status_code==400
