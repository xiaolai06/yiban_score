"""Read-only, incremental exception summaries derived from every durable import row.

No new student/score records are created. Valid IDs group across batches; records
with invalid/missing IDs remain separate so unrelated people cannot be merged.
"""
from __future__ import annotations

import csv
import io
import math
import re
from typing import Literal

from fastapi import HTTPException, Query, Response

from .database import connection
from .table_export import build_table_workbook

ISSUE_LABELS = {'unmatched': '名单外学号', 'invalid': '数据错误',
                'name_conflict': '姓名冲突', 'duplicate_conflict': '重复学号冲突'}
IssueKind = Literal['all', 'unmatched', 'invalid', 'name_conflict', 'duplicate_conflict']


def recorded_rate(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100:
        return value
    return None


def add_unique(values: list, value: str):
    if value and value not in values:
        values.append(value)


def public_group(group: dict) -> dict:
    return {key: value for key, value in group.items() if not key.startswith('_')}


def group_issues(events: list[dict]) -> list[dict]:
    groups = {}
    # Ascending immutable row ID keeps the original highest-score source on ties.
    for event in events:
        key = event['key']
        if key not in groups:
            groups[key] = {
                'key': key, 'student_id': event['student_id'], 'raw_ids': [], 'names': [],
                'colleges': [], 'classes': [], 'kinds': [], 'reasons': [],
                'best_score_rate': None, 'best_status': '', 'best_batch_id': None,
                'best_batch_name': '', 'best_row_number': None, 'occurrence_count': 0,
                'batch_count': 0, 'first_seen': event['created_at'], 'last_seen': event['created_at'],
                'latest_batch_id': event['batch_id'], 'latest_batch_name': event['batch_name'],
                '_batches': set(), '_events': [], '_last_id': event['id'],
            }
        group = groups[key]
        for field, sources in [('raw_ids', ['raw_id']), ('names', ['source_name', 'name']),
                               ('colleges', ['source_college', 'college']), ('classes', ['source_class_name', 'class_name'])]:
            for source in sources:
                add_unique(group[field], event[source])
        add_unique(group['kinds'], event['action'])
        for reason in event['errors']:
            add_unique(group['reasons'], reason)
        rate = event['score_rate']
        if rate is not None and (group['best_score_rate'] is None or rate > group['best_score_rate']):
            group.update(best_score_rate=rate, best_status=event['status'], best_batch_id=event['batch_id'],
                         best_batch_name=event['batch_name'], best_row_number=event['row_number'])
        group['occurrence_count'] += 1
        group['_batches'].add(event['batch_id'])
        group['batch_count'] = len(group['_batches'])
        group['last_seen'] = event['created_at']
        group['latest_batch_id'] = event['batch_id']
        group['latest_batch_name'] = event['batch_name']
        group['_last_id'] = event['id']
        group['_events'].append(event)
    return sorted(groups.values(), key=lambda group: group['_last_id'], reverse=True)


def filtered_groups(groups, q: str, kind: str):
    needle = q.strip().casefold()
    result = []
    for group in groups:
        if kind != 'all' and kind not in group['kinds']:
            continue
        texts = [group['student_id'], *group['raw_ids'], *group['names'], *group['colleges'],
                 *group['classes'], *group['reasons']]
        if needle and not any(needle in text.casefold() for text in texts):
            continue
        result.append(group)
    return result


def export_table(groups, scope):
    if scope == 'summary':
        headers = ['学号', '姓名记录', '学院记录', '班级记录', '最高记录得分率(%)', '最高分批次号',
                   '最高分批次名称', '最高分原表行号', '出现次数', '涉及批次数', '异常类型', '异常原因', '首次出现', '最近出现',
                   '原学号记录', '最近批次号', '最近批次名称', '汇总标识']
        rows = [[g['student_id'] or '学号无效 / 未填写', ' / '.join(g['names']), ' / '.join(g['colleges']),
                 ' / '.join(g['classes']), g['best_score_rate'], g['best_batch_id'], g['best_batch_name'],
                 g['best_row_number'], g['occurrence_count'], g['batch_count'],
                 ' / '.join(ISSUE_LABELS[kind] for kind in g['kinds']), '；'.join(g['reasons']),
                 g['first_seen'], g['last_seen'], ' / '.join(g['raw_ids']), g['latest_batch_id'],
                 g['latest_batch_name'], g['key']] for g in groups]
        return headers, rows
    headers = ['学号', '原学号', '原姓名', '名单姓名', '原学院', '原班级', '批次号', '批次名称', '导入时间',
               '批次状态', '原表行号', '原状态', '原得分率', '原成绩/百分比', '可识别得分率(%)', '异常类型', '异常原因']
    events = sorted((event for group in groups for event in group['_events']), key=lambda event: event['id'], reverse=True)
    return headers, [[e['student_id'], e['raw_id'], e['source_name'], e['name'], e['source_college'], e['source_class_name'],
                      e['batch_id'], e['batch_name'], e['created_at'], '已撤销' if e['batch_state'] == 'reverted' else '已保存',
                      e['row_number'], e['raw_status'], e['raw_rate'], e['raw_result'], e['score_rate'],
                      ISSUE_LABELS[e['action']], '；'.join(e['errors'])] for e in events]


def issue_snapshot(con, saved_record, *, college_id=None, class_id=None, exclude_removed=False):
    """Share the projection within a caller's read transaction; never write audit."""
    events = []
    rows = con.execute('''SELECT r.id,r.batch_id,r.row_number,r.student_id,r.action,r.record_json,
        b.name AS batch_name,b.filename,b.created_at,b.state AS batch_state
        FROM score_import_rows r JOIN score_batches b ON b.id=r.batch_id
        LEFT JOIN students s ON s.student_id=r.student_id
        LEFT JOIN classes c ON c.id=s.class_id
        WHERE r.action IN ('unmatched','invalid','name_conflict','duplicate_conflict')
          AND (? IS NULL OR c.college_id=?) AND (? IS NULL OR c.id=?)
          AND (?=0 OR NOT EXISTS (SELECT 1 FROM excluded_students e WHERE e.student_id=r.student_id))
        ORDER BY r.id''', (college_id, college_id, class_id, class_id, int(exclude_removed)))
    for row in rows:
        record = saved_record(row['record_json'])
        sid = row['student_id']
        valid_id = bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', sid))
        event = dict(row)
        event.pop('record_json')
        event['student_id'] = sid if valid_id else ''
        event['key'] = f'student:{sid}' if valid_id else f'row:{row["batch_id"]}:{row["row_number"]}'
        for field in ('raw_id', 'source_name', 'name', 'source_college', 'college', 'source_class_name',
                      'class_name', 'raw_status', 'raw_rate', 'raw_result', 'status'):
            event[field] = str(record.get(field) or '')
        errors = record.get('errors') or []
        if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
            raise HTTPException(503, '已保存的异常明细格式异常，请检查数据库备份。')
        event['errors'] = errors
        event['score_rate'] = recorded_rate(record.get('score_rate'))
        events.append(event)
    return group_issues(events)


def install_issue_routes(app, db_path, csv_response, safe_csv, saved_record):
    def snapshot():
        with connection(db_path) as con:
            return issue_snapshot(con, saved_record)

    def select(groups, key):
        if not key:
            return groups
        chosen = [group for group in groups if group['key'] == key]
        if not chosen:
            raise HTTPException(404, '异常记录不存在，请刷新汇总。')
        return chosen

    @app.get('/api/scores/issues')
    def list_issues(q: str = Query('', max_length=120), kind: IssueKind = 'all',
                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        groups = filtered_groups(snapshot(), q, kind)
        total = len(groups)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        return {'items': [public_group(group) for group in groups[(page - 1) * page_size:page * page_size]],
                'total': total, 'occurrences': sum(group['occurrence_count'] for group in groups),
                'page': page, 'page_size': page_size, 'pages': pages, 'q': q.strip(), 'kind': kind}

    @app.get('/api/scores/issues/occurrences')
    def issue_occurrences(key: str = Query(..., min_length=1, max_length=96),
                          page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        group = select(snapshot(), key)[0]
        events = sorted(group['_events'], key=lambda event: event['id'], reverse=True)
        total = len(events)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        return {'person': public_group(group), 'items': events[(page - 1) * page_size:page * page_size],
                'total': total, 'page': page, 'page_size': page_size, 'pages': pages}

    @app.get('/api/scores/issues/export.{file_type}')
    def export_issues(file_type: Literal['csv', 'xlsx'], q: str = Query('', max_length=120),
                      kind: IssueKind = 'all', scope: Literal['summary', 'occurrences'] = 'summary',
                      key: str = Query('', max_length=96)):
        groups = select(filtered_groups(snapshot(), q, kind), key)
        headers, records = export_table(groups, scope)
        filename = f'score-issues-{scope}.{file_type}'
        if file_type == 'xlsx':
            try:
                content = build_table_workbook(headers, records, '异常人员汇总' if scope == 'summary' else '异常出现记录')
            except ValueError as exc:
                raise HTTPException(413, str(exc)) from exc
            return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            headers={'Content-Disposition': f'attachment; filename="{filename}"'})
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(headers)
        writer.writerows([[safe_csv(value) if value is not None else '' for value in record] for record in records])
        return csv_response(out.getvalue(), filename)
