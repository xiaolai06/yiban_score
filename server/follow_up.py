"""Read-only counselor follow-up lists; score qualification and progress are distinct."""
from __future__ import annotations

import csv
import io
from typing import Literal

from fastapi import HTTPException, Query, Response

from .database import connection, revision
from .exam_time import format_duration
from .grade_rules import PASS_SCORE, has_numeric_score, is_qualified
from .score_api import saved_record
from .score_issues import ISSUE_LABELS, filtered_groups, issue_snapshot
from .table_export import build_table_workbook

Kind = Literal['pending', 'missing', 'not_started', 'in_progress', 'completed_low', 'other_low', 'at_60', 'issues']
LABELS = {'pending': '全部待跟进', 'missing': '未录入成绩', 'not_started': '未开始', 'in_progress': '进行中',
          'completed_low': '已完成低于60', 'other_low': '其他低分需核对', 'at_60': f'恰好{PASS_SCORE}（已合格）', 'issues': '历史异常核对'}


def categories(student) -> list[str]:
    result = []
    rate, status = student['score_rate'], student['status']
    if not has_numeric_score(rate):
        result.append('missing')
    if status == '未开始':
        result.append('not_started')
    if status == '进行中':
        result.append('in_progress')
    if has_numeric_score(rate):
        if rate < PASS_SCORE:
            result.append('completed_low' if status in {'已完成', '已录入'} else 'other_low')
        elif rate == PASS_SCORE:
            result.append('at_60')
    return result


def student_item(student):
    kinds = categories(student)
    return {**student, 'key': student['student_id'], 'categories': kinds,
            'category_labels': [LABELS[kind] for kind in kinds],
            'qualified': is_qualified(student['score_rate']), 'record_type': 'student'}


def snapshot(con, q, college_id, class_id):
    # The scope is chosen by actual roster IDs, never guessed from source text.
    scoped = [dict(row) for row in con.execute('''
        SELECT s.student_id,s.name,s.class_id,s.status,s.score_rate,
               s.exam_started_at,s.exam_finished_at,s.answer_duration_seconds,
               c.name AS class_name,c.college_id,co.name AS college_name
        FROM students s JOIN classes c ON c.id=s.class_id JOIN colleges co ON co.id=c.college_id
        WHERE (? IS NULL OR c.college_id=?) AND (? IS NULL OR s.class_id=?)
        ORDER BY co.id,c.id,s.student_id''', (college_id, college_id, class_id, class_id))]
    by_id = {student['student_id']: student for student in scoped}
    needle = q.strip().casefold()
    students = [student_item(student) for student in scoped if not needle or any(
        needle in str(student[field]).casefold() for field in ('student_id', 'name', 'college_name', 'class_name'))]
    counts = dict.fromkeys(LABELS, 0)
    for student in students:
        for kind in student['categories']:
            counts[kind] += 1
        if any(kind != 'at_60' for kind in student['categories']):
            counts['pending'] += 1  # De-duplicate overlapping missing/progress/low categories.
    issues = []
    groups = issue_snapshot(con, saved_record, college_id=college_id, class_id=class_id, exclude_removed=True)
    for group in filtered_groups(groups, q, 'all'):
        sid = group['student_id']
        current = by_id.get(sid)
        if (college_id is not None or class_id is not None) and current is None:
            continue  # Unknown/out-of-scope IDs cannot reliably be assigned to this class.
        issues.append({'key': group['key'], 'student_id': sid, 'name': current['name'] if current else ' / '.join(group['names']),
            'college_name': current['college_name'] if current else ' / '.join(group['colleges']),
            'class_name': current['class_name'] if current else ' / '.join(group['classes']),
            'status': current['status'] if current else '名单外 / 无法绑定',
            'score_rate': current['score_rate'] if current else None, 'historical_rate': group['best_score_rate'],
            'qualified': is_qualified(current['score_rate']) if current else None,
            'exam_started_at': current['exam_started_at'] if current else None,
            'exam_finished_at': current['exam_finished_at'] if current else None,
            'answer_duration_seconds': current['answer_duration_seconds'] if current else None,
            'category_labels': [ISSUE_LABELS[kind] for kind in group['kinds']], 'reasons': group['reasons'],
            'occurrence_count': group['occurrence_count'], 'batch_count': group['batch_count'],
            'record_type': 'issue', 'latest_batch_id': group['latest_batch_id']})
    counts['issues'] = len(issues)
    return students, issues, counts


def select(students, issues, kind):
    if kind == 'issues':
        return issues
    if kind == 'pending':
        return [row for row in students if any(value != 'at_60' for value in row['categories'])]
    return [row for row in students if kind in row['categories']]


def export_records(rows, kind):
    headers = ['学号', '姓名', '学院', '班级', '当前状态', '当前得分率(%)', '合格情况', '分类 / 跟进原因']
    if kind == 'issues':
        headers += ['历史最高记录得分率(%)', '历史异常原因', '出现次数', '涉及批次数', '最近批次号', '核对说明']
    headers += ['开始时间', '结束时间', '答题时长']
    records = []
    for row in rows:
        qualification = '非正式在册人员' if row['qualified'] is None else '已合格（≥60）' if row['qualified'] else '不合格（无有效数值或<60）'
        record = [row['student_id'], row['name'], row['college_name'], row['class_name'], row['status'],
                  row['score_rate'], qualification, '；'.join(row['category_labels'])]
        if kind == 'issues':
            record += [row['historical_rate'], '；'.join(row['reasons']), row['occurrence_count'], row['batch_count'],
                       row['latest_batch_id'], '历史异常仅供核对，不代表尚未解决；不计入正式待跟进人数。']
        record += [row.get('exam_started_at'), row.get('exam_finished_at'), format_duration(row.get('answer_duration_seconds')) or None]
        records.append(record)
    return headers, records


def install_follow_up_routes(app, db_path, csv_response, safe_csv):
    @app.get('/api/follow-up')
    def follow_up(kind: Kind = 'pending', q: str = Query('', max_length=120),
                  college_id: int | None = Query(None, ge=1), class_id: int | None = Query(None, ge=1),
                  page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        with connection(db_path) as con:
            con.execute('BEGIN')
            rev = revision(con)
            students, issues, counts = snapshot(con, q, college_id, class_id)
            rows = select(students, issues, kind)
            total = len(rows)
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            return {'items': rows[(page-1)*page_size:page*page_size], 'total': total, 'pages': pages,
                    'page': page, 'page_size': page_size, 'counts': counts, 'scope_students': len(students),
                    'revision': rev, 'kind': kind, 'q': q.strip(), 'labels': LABELS,
                    'college_id': college_id, 'class_id': class_id}

    @app.get('/api/follow-up/export.{file_type}')
    def export_follow_up(file_type: Literal['csv', 'xlsx'], kind: Kind = 'pending', q: str = Query('', max_length=120),
                         college_id: int | None = Query(None, ge=1), class_id: int | None = Query(None, ge=1),
                         expected_revision: int | None = Query(None, ge=0)):
        with connection(db_path) as con:
            con.execute('BEGIN')
            rev = revision(con)
            if expected_revision is not None and rev != expected_revision:
                raise HTTPException(409, '清单读取后数据库已有变化，请刷新待跟进清单再导出。')
            students, issues, _ = snapshot(con, q, college_id, class_id)
            rows = select(students, issues, kind)
            if not rows:
                raise HTTPException(400, '当前范围及分类没有可导出的记录。')
            headers, records = export_records(rows, kind)
        filename = f'follow-up-{kind}.{file_type}'
        if file_type == 'csv':
            out = io.StringIO(newline='')
            writer = csv.writer(out)
            writer.writerow(headers)
            writer.writerows([[safe_csv(value) for value in record] for record in records])
            response = csv_response(out.getvalue(), filename)
        else:
            try:
                content = build_table_workbook(headers, records, LABELS[kind])
            except ValueError as exc:
                raise HTTPException(413, str(exc)) from exc
            response = Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                headers={'Content-Disposition': f'attachment; filename="{filename}"'})
        response.headers['X-Export-Records'] = str(len(rows))
        response.headers['X-Follow-Up-Api-Version'] = '4'
        response.headers['X-Data-Revision'] = str(rev)
        return response
