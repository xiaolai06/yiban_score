"""Explicit, revision-checked removal by column A; no name-based identity guesses."""
from __future__ import annotations

import csv
import io
import json
import secrets
import time
from contextlib import suppress
from typing import Any

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .database import STUDENT_SELECT, bump_revision, connection, revision
from .roster import LOOKUP, header_key, identifier


class RemovalPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    upload_id: str
    sheet_index: int = Field(ge=0, le=49)
    header_row: int = Field(ge=1, le=60000)


class RemovalCommit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: str
    confirm: bool = False
    confirmation: str = ''
    expected_count: int = Field(ge=0)


def removal_rows(sheet, header_row) -> dict[str, Any]:
    if not 1 <= header_row <= sheet.rows:
        raise ValueError('表头行号超出工作表范围。')
    if LOOKUP.get(header_key(sheet.cell(header_row - 1, 0).text())) != 'student_id':
        raise ValueError('剔除名单的 A 列表头必须是“学号”；不会改用其他列删除。')
    rows, errors, seen = [], [], set()
    duplicates = blanks = repeated = source = 0
    for index in range(header_row, sheet.rows):
        if not any(sheet.cell(index, col).text() for col in range(sheet.cols)):
            blanks += 1
            continue
        cell = sheet.cell(index, 0)
        if LOOKUP.get(header_key(cell.text())) == 'student_id' and not cell.formula and not cell.error:
            repeated += 1
            continue
        source += 1
        try:
            sid = identifier(cell)
        except ValueError as exc:
            errors.append({'row_number': index + 1, 'student_id': cell.text(), 'message': str(exc)})
            continue
        if sid in seen:
            duplicates += 1
            continue
        seen.add(sid)
        rows.append({'row_number': index + 1, 'student_id': sid})
    if not rows and not errors:
        errors.append({'row_number': header_row, 'student_id': '', 'message': '表头后没有可剔除的学号。'})
    return {'rows': rows, 'errors': errors, 'source_rows': source, 'duplicate_rows': duplicates,
            'blank_rows': blanks, 'repeated_headers': repeated}


def install_removal_routes(app, db_path, stage, read_upload, read_stage, stage_path,
                           write_stage, csv_response, safe_csv):
    def csv_rows(rows, errors, filename):
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['原表行号', 'A列学号', '数据库姓名', '数据库学院', '数据库班级', '当前状态', '当前得分率', '处理结果'])
        for row in rows:
            writer.writerow([safe_csv(v) for v in (row['row_number'], row['student_id'], row.get('name', ''),
                row.get('college_name', ''), row.get('class_name', ''), row.get('status', ''),
                row.get('score_rate'), '将删除' if row['matched'] else '不在当前名单，仅登记剔除学号')])
        for error in errors:
            writer.writerow([safe_csv(v) for v in (error['row_number'], error['student_id'], '', '', '', '', '', error['message'])])
        return csv_response(out.getvalue(), filename)

    @app.post('/api/removals/preview')
    def preview_removal(body: RemovalPreview):
        meta, sheets = read_upload(body.upload_id)
        if body.sheet_index >= len(sheets):
            raise HTTPException(400, '工作表不存在。')
        try:
            validated = removal_rows(sheets[body.sheet_index], body.header_row)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        with connection(db_path) as con:
            con.execute('BEGIN')
            rev = revision(con)
            existing = {row['student_id']: dict(row) for row in con.execute(STUDENT_SELECT)}
            excluded = {row[0] for row in con.execute('SELECT student_id FROM excluded_students')}
            con.execute('CREATE TEMP TABLE removal_ids(student_id TEXT PRIMARY KEY)')
            con.executemany('INSERT INTO removal_ids VALUES(?)', [(r['student_id'],) for r in validated['rows']])
            score_rows = con.execute('SELECT COUNT(*) FROM score_import_rows WHERE student_id IN (SELECT student_id FROM removal_ids)').fetchone()[0]
            changes = con.execute('SELECT COUNT(*) FROM student_changes WHERE student_id IN (SELECT student_id FROM removal_ids)').fetchone()[0]
            rows = [dict(r, matched=r['student_id'] in existing, **{k: existing.get(r['student_id'], {}).get(k)
                    for k in ('name', 'college_name', 'class_name', 'status', 'score_rate')}) for r in validated['rows']]
            matched = sum(1 for r in rows if r['matched'])
            summary = {k: v for k, v in validated.items() if k not in {'rows', 'errors'}}
            summary.update(unique_ids=len(rows), matched=matched, unmatched=len(rows)-matched,
                already_excluded=sum(r['student_id'] in excluded for r in rows),
                score_rows=score_rows, change_rows=changes, error_count=len(validated['errors']))
            con.rollback()
        token = secrets.token_hex(16)
        payload = {'created_at': time.time(), 'upload_id': body.upload_id, 'meta': meta,
                   'sheet_name': sheets[body.sheet_index].name, 'revision': rev,
                   'summary': summary, 'rows': rows, 'errors': validated['errors']}
        write_stage(stage_path(token, '.removal-preview.json'), payload)
        return {'preview_id': token, 'summary': summary, 'sample': rows[:25], 'errors': validated['errors'][:100],
                'can_commit': bool(rows) and not validated['errors']}

    @app.get('/api/removals/{preview_id}/preview.csv')
    def export_preview(preview_id: str):
        payload = read_stage(preview_id, '.removal-preview.json')
        return csv_rows(payload['rows'], payload['errors'], 'removal-preview.csv')

    @app.post('/api/removals/commit')
    def commit_removal(body: RemovalCommit):
        stage_path(body.preview_id, '.removal-preview.json')
        if not body.confirm or body.confirmation != '确认剔除':
            raise HTTPException(400, '请勾选永久删除确认，并输入“确认剔除”。')
        with connection(db_path) as con:
            try:
                con.execute('BEGIN IMMEDIATE')
                prior = con.execute('SELECT * FROM removal_batches WHERE preview_id=?', (body.preview_id,)).fetchone()
                if prior:
                    if body.expected_count != prior['deleted_count']:
                        raise HTTPException(409, '确认人数与剔除记录不一致。')
                    con.rollback()
                    return {'ok': True, 'idempotent': True, 'batch': dict(prior)}
                payload = read_stage(body.preview_id, '.removal-preview.json')
                if payload['errors'] or not payload['rows']:
                    raise HTTPException(400, '剔除名单存在错误或为空，未删除任何人员。')
                if body.expected_count != payload['summary']['matched']:
                    raise HTTPException(409, '确认删除人数与预览不一致，请重新核对。')
                if revision(con) != payload['revision']:
                    raise HTTPException(409, '预览后数据库已有变更，请重新生成剔除预览；未删除任何人员。')
                con.execute('CREATE TEMP TABLE removal_ids(student_id TEXT PRIMARY KEY)')
                con.executemany('INSERT INTO removal_ids VALUES(?)', [(r['student_id'],) for r in payload['rows']])
                summary = payload['summary']
                batch_id = con.execute('''INSERT INTO removal_batches(preview_id,filename,sha256,sheet_name,
                    source_rows,unique_ids,deleted_count,unmatched_count,duplicate_rows,score_rows,change_rows)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (body.preview_id, payload['meta']['filename'],
                    payload['meta']['sha256'], payload['sheet_name'], summary['source_rows'], summary['unique_ids'],
                    summary['matched'], summary['unmatched'], summary['duplicate_rows'], summary['score_rows'], summary['change_rows'])).lastrowid
                # Keep only minimal IDs to prevent accidental roster re-creation. No deleted names/scores are archived.
                con.execute('''INSERT OR IGNORE INTO excluded_students(student_id,removal_batch_id)
                    SELECT student_id,? FROM removal_ids''', (batch_id,))
                con.execute('''INSERT OR IGNORE INTO removal_score_batches(removal_batch_id,score_batch_id)
                    SELECT ?,batch_id FROM score_import_rows WHERE student_id IN (SELECT student_id FROM removal_ids)''', (batch_id,))
                con.execute('''CREATE TEMP TABLE removal_classes AS SELECT DISTINCT class_id FROM students
                    WHERE student_id IN (SELECT student_id FROM removal_ids)''')
                con.execute('''CREATE TEMP TABLE removal_colleges AS SELECT DISTINCT college_id FROM classes
                    WHERE id IN (SELECT class_id FROM removal_classes)''')
                con.execute('DELETE FROM score_import_rows WHERE student_id IN (SELECT student_id FROM removal_ids)')
                con.execute('DELETE FROM student_changes WHERE student_id IN (SELECT student_id FROM removal_ids)')
                con.execute('DELETE FROM students WHERE student_id IN (SELECT student_id FROM removal_ids)')
                # Prune only directories emptied by this removal, not unrelated empty directories.
                con.execute('''DELETE FROM classes WHERE id IN (SELECT class_id FROM removal_classes)
                    AND NOT EXISTS (SELECT 1 FROM students WHERE students.class_id=classes.id)''')
                con.execute('''DELETE FROM colleges WHERE id IN (SELECT college_id FROM removal_colleges)
                    AND NOT EXISTS (SELECT 1 FROM classes WHERE classes.college_id=colleges.id)''')
                bump_revision(con)
                con.commit()
                batch = dict(con.execute('SELECT * FROM removal_batches WHERE id=?', (batch_id,)).fetchone())
            except Exception:
                con.rollback()
                raise
        for suffix in ('.bin', '.upload.json', '.removal-preview.json'):
            token = body.preview_id if suffix == '.removal-preview.json' else payload['upload_id']
            with suppress(OSError):
                stage_path(token, suffix).unlink(missing_ok=True)
        # Obsolete preview snapshots can contain deleted personal records. Never
        # invalidate another window's freshly generated post-removal preview.
        for pattern in ('*.preview.json', '*.score-preview.json', '*.removal-preview.json'):
            for path in stage.glob(pattern):
                try:
                    saved = json.loads(path.read_text(encoding='utf-8'))
                    if saved.get('revision', -1) <= payload['revision']:
                        path.unlink(missing_ok=True)
                except (OSError, ValueError, TypeError, AttributeError):
                    # The database transaction already succeeded; cleanup is best effort.
                    pass
        return {'ok': True, 'idempotent': False, 'batch': batch}

    @app.get('/api/removals')
    def removal_history(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=50)):
        with connection(db_path) as con:
            total = con.execute('SELECT COUNT(*) FROM removal_batches').fetchone()[0]
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            rows = con.execute('SELECT * FROM removal_batches ORDER BY id DESC LIMIT ? OFFSET ?',
                               (page_size, (page - 1) * page_size))
            return {'items': [dict(row) for row in rows], 'page': page, 'pages': pages, 'total': total}
