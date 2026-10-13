"""Counselor lists and unified qualification are tested only on temporary data."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.excel_reader import read_workbook
from tests.test_app import add, row
from tests.test_roster_removal import removal_preview, remove
from tests.test_scores import commit as score_commit
from tests.test_scores import preview as score_preview


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/'follow.sqlite3',testing=True)) as c:
        add(c,[row('0001',status='已完成',rate=None),row('0002',status='未开始',rate=0),
               row('0003',status='进行中',rate=95),row('0004',name='=1+1',status='已完成',rate=89.99),
               row('0005',status='已录入',rate=0),row('0006',status='已完成',rate=90),
               row('0007',status='进行中',rate=90),row('0008',status='缺考',rate=20),
               row('0009',status='已录入',rate=100),row('0010',status='进行中',rate=None),
               row('0011',college='其他示例学院',status='已完成',rate=90.01)])
        yield c


def test_categories_de_duplicate_without_mislabeling_90_or_progress(client):
    before=client.get('/api/students?page_size=100').json()
    result=client.get('/api/follow-up').json()
    assert result['total']==result['counts']['pending']==8
    assert result['scope_students']==11
    assert result['counts']=={'pending':8,'missing':2,'not_started':1,'in_progress':3,
                             'completed_low':2,'other_low':2,'at_90':2,'issues':0}
    assert {item['student_id'] for item in result['items']}=={'0001','0002','0003','0004','0005','0007','0008','0010'}
    assert next(item for item in result['items'] if item['student_id']=='0003')['qualified']
    ninety=client.get('/api/follow-up?kind=at_90').json()
    assert {item['student_id'] for item in ninety['items']}=={'0006','0007'}
    assert all(item['qualified'] for item in ninety['items'])
    assert client.get('/api/follow-up?kind=completed_low').json()['total']==2
    assert client.get('/api/students?page_size=100').json()==before


def test_all_views_and_workbook_agree_with_progress_kept_separate(client):
    stats=client.get('/api/stats').json()
    students=client.get('/api/students').json()
    assert stats['qualified']==students['qualified']==5
    assert students['at_90']==2 and students['below_90']==6
    assert students['total']==students['qualified']+students['below_90']
    assert students['settled']==6 and students['average_rate']==65
    response=client.post('/api/students/export.xlsx',headers={'X-Requested-With':'StudentScore'},json={'all_students':True})
    book=read_workbook(response.content,'synthetic.xlsx')
    assert book[-1].cell(1,7).value==5
    assert client.get('/api/health').json()['directory_api_version']==4
    assert client.get('/api/health').json()['follow_up_api_version']==1
    low=client.get('/api/students?score_filter=below_90&page_size=100').json()
    assert not {'0003','0006','0007','0009','0011'}&{item['student_id'] for item in low['items']}
    # Unfinished numeric values remain visible and unchanged, not settled for averages.
    assert client.get('/api/students/0007').json()['score_rate']==90


@pytest.mark.parametrize('kind,ids',[('missing',{'0001','0010'}),('not_started',{'0002'}),
    ('in_progress',{'0003','0007','0010'}),('completed_low',{'0004','0005'}),('other_low',{'0002','0008'}),
    ('at_90',{'0006','0007'})])
def test_category_filters_exact_people(client,kind,ids):
    result=client.get('/api/follow-up',params={'kind':kind}).json()
    assert {item['student_id'] for item in result['items']}==ids
    assert result['total']==len(ids)


def test_scope_search_and_pagination_are_independent_of_visible_page(client):
    scope=client.get('/api/students/0001').json()
    r=client.get('/api/follow-up',params={'college_id':scope['college_id'],'page_size':1,'page':2}).json()
    assert r['total']==8 and len(r['items'])==1 and r['page']==2 and r['pages']==8
    assert r['counts']['missing']==2
    r=client.get('/api/follow-up?q=0007').json()
    assert r['total']==1 and r['counts']['pending']==r['counts']['in_progress']==r['counts']['at_90']==1
    assert client.get('/api/follow-up?q=%27%20OR%201%3D1').json()['total']==0
    assert client.get('/api/follow-up?q=不存在').json()['counts']['pending']==0
    assert client.get('/api/follow-up?page=999').json()['page']==1
    other=client.get('/api/students/0011').json()
    assert client.get('/api/follow-up',params={'class_id':other['class_id']}).json()['total']==0


@pytest.mark.parametrize('format',['csv','xlsx'])
def test_full_export_has_scope_reasons_fonts_and_no_formula_execution(client,format):
    r=client.get('/api/follow-up?page_size=1').json()
    response=client.get(f'/api/follow-up/export.{format}',params={'expected_revision':r['revision']})
    assert response.status_code==200
    assert response.headers['X-Export-Records']=='8'
    assert response.headers['X-Follow-Up-Api-Version']=='1'
    if format=='csv':
        data=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        assert len(data)==8 and {entry['学号'] for entry in data}=={entry['student_id'] for entry in r['items']}|{'0002','0003','0004','0005','0007','0008','0010'}
        assert next(entry for entry in data if entry['学号']=='0004')['姓名']=="'=1+1"
        assert next(entry for entry in data if entry['学号']=='0007')['合格情况']=='已合格（≥90）'
    else:
        book=read_workbook(response.content,'follow.xlsx')
        assert len(book)==1 and book[0].rows==9
        assert book[0].cell(1,0).text()=='0001'
        assert book[0].cell(1,5).value is None
        assert book[0].cell(3,5).value==95
        assert any(book[0].cell(index,1).text()=='=1+1' for index in range(1,book[0].rows))
        assert all(not cell.formula for cell in book[0].cells.values())


def test_export_uses_current_revision_and_same_search_scope(client):
    first=client.get('/api/follow-up').json()
    student=client.get('/api/students/0001').json()
    client.patch('/api/students/0001',headers={'X-Requested-With':'StudentScore'},json={
        'version':student['version'],'status':'已完成','score_rate':95})
    assert client.get('/api/follow-up/export.xlsx',params={'expected_revision':first['revision']}).status_code==409
    response=client.get('/api/follow-up/export.csv?kind=in_progress&q=0007')
    assert response.headers['X-Export-Records']=='1'
    assert client.get('/api/follow-up/export.xlsx?q=不存在').status_code==400


def save_issues(client):
    preview=score_preview(client,rows=[['0004','另一姓名','已完成','95','','示例学院A','示例一班'],
        ['OUTSIDE','名单外样例','已完成','80','','示例学院A','示例一班'],
        ['0001','','已完成','201','','',''],['','无学号样例','已完成','95','','','']])
    response=score_commit(client,preview,skip_issues=True,save_issues_only=True)
    assert response.status_code==200,response.text


def test_anomalies_are_history_not_pending_and_unknown_scope_is_not_guessed(client):
    save_issues(client)
    data=client.get('/api/follow-up?kind=issues').json()
    assert data['total']==4 and data['counts']['pending']==8
    outside=next(item for item in data['items'] if item['student_id']=='OUTSIDE')
    assert outside['qualified'] is None and outside['score_rate'] is None and outside['historical_rate']==80
    current=client.get('/api/students/0001').json()
    scoped=client.get('/api/follow-up',params={'kind':'issues','class_id':current['class_id']}).json()
    assert scoped['total']==2
    assert {item['student_id'] for item in scoped['items']}=={'0001','0004'}
    response=client.get('/api/follow-up/export.csv?kind=issues')
    assert '历史异常仅供核对，不代表尚未解决' in response.content.decode('utf-8-sig')
    # Archived issues survive a normal reversion; they are not falsely labeled unresolved.
    p=removal_preview(client,['0004','OUTSIDE']).json()
    assert remove(client,p).status_code==200
    after=client.get('/api/follow-up?kind=issues').json()
    assert after['total']==2 and after['counts']['pending']==7
    assert not any(item['student_id'] in {'0004','OUTSIDE'} for item in after['items'])


@pytest.mark.parametrize('query',['kind=unknown','page=0','page_size=101','college_id=-1','class_id=abc'])
def test_follow_up_input_validation(client,query):
    assert client.get('/api/follow-up?'+query).status_code==422
