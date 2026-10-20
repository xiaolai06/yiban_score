"""Optional time fields must not break old score files, records or audit exports."""
from __future__ import annotations

import csv
import io
import json

import pytest

from server.database import connection
from server.exam_time import TIME_FIELDS
from server.excel_reader import read_workbook
from tests.test_app import xlsx
from tests.test_exam_time import END, START, csv_records, timed_preview
from tests.test_scores import H, commit, detail, preview
from tests.test_scores import client as score_client

client = score_client
MINIMAL = ['学号', '状态', '得分率']


def source_bytes(headers, rows, *, delimiter=',', encoding='utf-8-sig'):
    out = io.StringIO(newline='')
    writer = csv.writer(out, delimiter=delimiter)
    writer.writerow(headers)
    writer.writerows(rows)
    return out.getvalue().encode(encoding)


@pytest.mark.parametrize('file_type,encoding', [('csv','utf-8-sig'),('csv','gb18030'),
    ('csv','utf-16'),('tsv','utf-8-sig'),('xlsx','excel')])
def test_old_files_without_any_time_column_import_display_export_and_revert(client, file_type, encoding):
    rows = [['0001','已完成',59], ['0002','已完成',0], ['0003','未开始',0]]
    data = xlsx([MINIMAL,*rows]) if file_type == 'xlsx' else source_bytes(
        MINIMAL, rows, delimiter='\t' if file_type == 'tsv' else ',', encoding=encoding)
    p = preview(client, data=data, filename=f'legacy.{file_type}')
    assert p['can_commit'] and p['summary']['issue_rows'] == 0
    assert all(r['answer_duration_seconds'] is None for r in p['rows'])
    assert commit(client,p).status_code == 200
    assert detail(client)['score_rate'] == 59 and detail(client,'0002')['score_rate'] == 0
    assert detail(client,'0003')['score_rate'] is None
    assert all(detail(client)[key] is None for key in TIME_FIELDS)
    exported = csv_records(client.get('/api/students/export.csv'))
    assert len(exported) == 3 and all(row['答题时长'] == '' for row in exported)
    for scope in [{'all_students':True}, {'student_ids':['0001']}, {'all_students':True,'below_60':True}]:
        response = client.post('/api/students/export.xlsx', json=scope, headers=H)
        assert response.status_code == 200
        book = read_workbook(response.content, 'synthetic.xlsx')
        assert book[0].cell(0,9).text() == '答题时长'
        assert all(book[0].cell(index,9).value is None for index in range(1,book[0].rows))
    assert csv_records(client.get('/api/scores/batches/1/export.csv'))[0]['本次答题时长'] == ''
    assert csv_records(client.get('/api/follow-up/export.csv?q=0001'))[0]['答题时长'] == ''
    assert client.post('/api/scores/batches/1/revert', json={'confirm':True}, headers=H).status_code == 200
    assert detail(client)['score_rate'] is None


@pytest.mark.parametrize('placeholder', ['', '—', '--', '暂无', '暂无记录', '未提供', '未填写', '未录入', '未结算', '\u200b\ufeff'])
def test_empty_and_old_display_placeholders_are_optional_not_bad_dates(client, placeholder):
    data = source_bytes(MINIMAL+['开始时间','结束时间'], [['0001','已完成',92,placeholder,placeholder]])
    p = preview(client, data=data)
    assert p['summary']['issue_rows'] == 0 and p['can_commit']
    assert p['rows'][0]['answer_duration_seconds'] is None
    assert commit(client,p).status_code == 200
    assert detail(client)['score_rate'] == 92


@pytest.mark.parametrize('row,omit_mapping', [(['0001','已完成',92],False),
    (['0001','已完成',92,START],False), (['0001','已完成',92],True)])
def test_only_missing_trailing_optional_time_cells_can_be_omitted(client, row, omit_mapping):
    data = source_bytes(MINIMAL+['开始时间','结束时间'], [row])
    options = {'mapping':{'student_id':0,'status':1,'score_rate':2}} if omit_mapping else {}
    p = preview(client,data=data,**options)
    assert not p['summary']['issue_rows'] and p['rows'][0]['answer_duration_seconds'] is None
    assert any('末尾可选时间列' in note for note in p['rows'][0]['notes'])
    assert commit(client,p).status_code == 200 and detail(client)['score_rate'] == 92


@pytest.mark.parametrize('headers,row', [
    (MINIMAL+['开始时间','结束时间'], ['0001','已完成']),
    (['学号','开始时间','结束时间','状态','得分率'], ['0001','已完成',92]),
    (MINIMAL+['其他必核对列'], ['0001','已完成',92]),
    (MINIMAL, ['0001','已完成',92,'extra']),
])
def test_optional_time_compat_does_not_hide_shifted_identity_or_score_columns(client, headers, row):
    p = preview(client,data=source_bytes(headers,[row]))
    assert p['summary']['invalid'] == 1 and not p['can_commit']
    assert any('列数与表头不一致' in error for error in p['rows'][0]['errors'])
    assert detail(client)['score_rate'] is None


@pytest.mark.parametrize('rate,expected_time', [(92,773),(80,773),(95,None)])
def test_old_no_time_file_never_patches_wrong_attempt_duration(client, rate, expected_time):
    assert commit(client,timed_preview(client)).status_code == 200
    p = preview(client,data=source_bytes(MINIMAL,[['0001','已完成',rate]]))
    assert p['summary']['issue_rows'] == 0
    assert commit(client,p).status_code == 200
    assert detail(client)['score_rate'] == max(92,rate)
    assert detail(client)['answer_duration_seconds'] == expected_time


def test_legacy_audit_json_with_absent_time_keys_exports_and_reverts_normally(client):
    p = preview(client,data=source_bytes(MINIMAL,[['0001','已完成',92]]))
    assert commit(client,p).status_code == 200
    with connection(client.app.state.db_path) as con:
        record = json.loads(con.execute('SELECT record_json FROM score_import_rows WHERE batch_id=1').fetchone()[0])
        for field in (*TIME_FIELDS,'raw_started_at','raw_finished_at'):
            record.pop(field,None)
        for snapshot in ['before','after']:
            for field in TIME_FIELDS:
                record[snapshot].pop(field,None)
        con.execute('UPDATE score_import_rows SET record_json=? WHERE batch_id=1', (json.dumps(record),))
        con.commit()
    record = client.get('/api/scores/batches/1/rows').json()['items'][0]
    assert 'answer_duration_seconds' not in record
    exported = csv_records(client.get('/api/scores/batches/1/export.csv'))[0]
    assert exported['本次答题时长'] == exported['导入前答题时长'] == exported['合并后答题时长'] == ''
    assert client.post('/api/scores/batches/1/revert',json={'confirm':True},headers=H).status_code == 200
    assert detail(client)['score_rate'] is None


def test_time_columns_are_truly_optional_even_when_only_one_is_mapped(client):
    data = source_bytes(MINIMAL+['开始时间'], [['0001','已完成',92,START]])
    p = preview(client,data=data)
    assert p['summary']['issue_rows'] == 0
    assert commit(client,p).status_code == 200
    assert detail(client)['exam_started_at'] == START
    assert detail(client)['exam_finished_at'] is None and detail(client)['answer_duration_seconds'] is None
    # Conversely, a supplied but malformed time remains a real validation issue.
    p = timed_preview(client,start='not-a-date',end=END)
    assert p['summary']['invalid'] == 1
