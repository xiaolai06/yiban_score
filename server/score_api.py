"""Score import endpoints: analyze -> preview -> explicit commit -> durable audit.

One SQLite transaction per commit/undo; preview revision prevents lost updates.
The preview is an immutable server-side payload and never trusted from the UI.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
import time
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Literal

from fastapi import File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .database import STUDENT_SELECT, bump_revision, connection, read_snapshot, revision
from .exam_time import format_duration
from .score_import import (
    ACTIONS,
    FIELDS,
    build_preview,
    read_score_file,
    score_sheet_info,
)
from .score_issues import install_issue_routes
from .staging import cleanup_upload, read_upload_bytes

ISSUES = {'unmatched', 'invalid', 'name_conflict', 'duplicate_conflict'}


class ScorePreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    upload_id: str
    sheet_index: StrictInt = Field(default=0, ge=0, le=49)
    header_row: StrictInt = Field(default=1, ge=1, le=60000)
    mapping: dict[str, StrictInt]
    batch_name: str = Field(min_length=1, max_length=120)
    scale: Literal['percent', 'fraction'] = 'percent'
    zero_policy: Literal['null_unfinished', 'keep'] = 'null_unfinished'
    # Explicit legacy modes remain supported for existing API integrations.
    # The UI and requests without a mode always use incremental highest-score merging.
    update_mode: Literal['keep_highest', 'replace', 'fill_missing'] = 'keep_highest'
    protect_completed: StrictBool = True


class ScoreCommit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: str
    skip_issues: StrictBool = False
    save_issues_only: StrictBool = False


class RevertInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: StrictBool = False


def saved_record(value: str) -> dict:
    try:
        record = json.loads(value)
        if not isinstance(record, dict):
            raise TypeError('Expected a saved object')
        return record
    except (TypeError, ValueError) as exc:
        raise HTTPException(503, '已保存的导入记录格式异常，请检查数据库备份；未修改任何记录。') from exc


def install_score_routes(app, db_path, stage, read_stage, stage_path, write_stage,
                         clean_expired, csv_response, safe_csv):
    max_upload = 20 * 1024 * 1024
    install_issue_routes(app, db_path, csv_response, safe_csv, saved_record)

    def read_source(token):
        meta = read_stage(token, '.score-upload.json')
        data = read_upload_bytes(stage_path(token, '.score.bin'), meta)
        sheets, format_info = read_score_file(data, meta['filename'])
        return meta, sheets, format_info

    def public_batch(row):
        batch = dict(row)
        batch['summary'] = saved_record(batch.pop('summary_json'))
        batch['options'] = saved_record(batch.pop('options_json'))
        return batch

    def filter_rows(rows, kind):
        if kind == 'issues':
            return [r for r in rows if r['action'] in ISSUES]
        if kind != 'all':
            return [r for r in rows if r['action'] == kind]
        return rows

    def rows_csv(rows, filename):
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['记录行号', '原学号', '清洗后学号', '原姓名', '名单姓名', '名单学院', '名单班级',
                         '原学院', '原班级', '原状态', '原得分率', '原成绩/百分比', '清洗后状态',
                         '清洗后得分率(%)', '处理结果', '清洗说明', '异常说明', '导入前得分率(%)', '合并后得分率(%)',
                         '原开始时间', '原结束时间', '清洗后开始时间', '清洗后结束时间', '本次答题时长',
                         '导入前答题时长', '合并后答题时长'])
        for r in rows:
            before = r.get('before') or {}
            after = r.get('after') or {}
            if r['action'] in {'protected', 'unchanged'}:
                after = before  # Also correct the display of legacy retained-row snapshots.
            elif r['action'] in ISSUES:
                after = {}  # Invalid candidates are never a committed result.
            values = [r['row_number'], r['raw_id'], r['student_id'], r['source_name'], r['name'],
                      r['college'], r['class_name'], r.get('source_college', ''), r.get('source_class_name', ''),
                      r['raw_status'], r['raw_rate'], r['raw_result'], r['status'] or '',
                      r['score_rate'] if r['score_rate'] is not None else '', ACTIONS[r['action']],
                      '; '.join(r['notes']), '; '.join(r['errors']),
                      before.get('score_rate') if before.get('score_rate') is not None else '',
                      after.get('score_rate') if after.get('score_rate') is not None else '',
                      r.get('raw_started_at', ''), r.get('raw_finished_at', ''),
                      r.get('exam_started_at'), r.get('exam_finished_at'), format_duration(r.get('answer_duration_seconds')),
                      format_duration(before.get('answer_duration_seconds')), format_duration(after.get('answer_duration_seconds'))]
            writer.writerow([safe_csv(value) for value in values])
        return csv_response(out.getvalue(), filename)

    @app.post('/api/scores/analyze')
    def analyze_scores(file: Annotated[UploadFile, File(...)]):
        clean_expired()
        if len(list(stage.glob('*.score-upload.json'))) >= 20:
            raise HTTPException(429, '成绩临时上传数量过多，请先完成已有导入或等待过期。')
        try:
            data = file.file.read(max_upload + 1)
        finally:
            file.file.close()
        if len(data) > max_upload:
            raise HTTPException(413, '成绩文件不能超过 20 MB。')
        filename = Path((file.filename or 'scores.csv').replace('\\', '/')).name[:240]
        sheets, fmt = read_score_file(data, filename)
        infos = [score_sheet_info(sheet, i) for i, sheet in enumerate(sheets)]
        selected = max(range(len(infos)), key=lambda i: (infos[i]['recognized'], not infos[i]['hidden'], infos[i]['rows']))
        token = secrets.token_hex(16)
        path = stage_path(token, '.score.bin')
        path.write_bytes(data)
        with suppress(OSError):
            path.chmod(0o600)
        write_stage(stage_path(token, '.score-upload.json'), {
            'created_at': time.time(), 'filename': filename,
            'sha256': hashlib.sha256(data).hexdigest(), 'format': fmt,
        })
        return {'upload_id': token, 'filename': filename, 'size': len(data), 'sheets': infos,
                'selected_sheet': selected, 'fields': FIELDS, 'format': fmt}

    @app.get('/api/scores/uploads/{upload_id}/sheet')
    def inspect_score_sheet(upload_id: str, sheet_index: int = Query(0, ge=0, le=49),
                            header_row: int = Query(1, ge=1, le=60000)):
        _, sheets, _ = read_source(upload_id)
        if sheet_index >= len(sheets):
            raise HTTPException(400, '工作表不存在。')
        return score_sheet_info(sheets[sheet_index], sheet_index, header_row)

    @app.post('/api/scores/preview')
    def score_preview(body: ScorePreview):
        meta, sheets, fmt = read_source(body.upload_id)
        if not body.batch_name.strip():
            raise HTTPException(400, '请输入批次名称。')
        if body.sheet_index >= len(sheets):
            raise HTTPException(400, '工作表不存在。')
        with connection(db_path) as con:
            con.execute('BEGIN')
            rev = revision(con)
            existing = {r['student_id']: dict(r) for r in con.execute(STUDENT_SELECT)}
            prior = [dict(r) for r in con.execute(
                'SELECT id,name,created_at,state FROM score_batches WHERE sha256=? ORDER BY id DESC LIMIT 5',
                (meta['sha256'],))]
            con.rollback()
        options = {key: getattr(body, key) for key in ('scale', 'zero_policy', 'update_mode', 'protect_completed')}
        result = build_preview(sheets[body.sheet_index], body.header_row, body.mapping, existing, **options)
        token = secrets.token_hex(16)
        payload = dict(result, created_at=time.time(), upload_id=body.upload_id, meta=meta,
                       preview_id=token, revision=rev, batch_name=body.batch_name.strip(), options=options,
                       sheet_name=sheets[body.sheet_index].name, mapping=body.mapping, format=fmt)
        write_stage(stage_path(token, '.score-preview.json'), payload)
        return {'preview_id': token, 'summary': result['summary'], 'rows': result['rows'][:20],
                'warnings': result['warnings'], 'prior_batches': prior, 'action_labels': ACTIONS, 'revision': rev,
                'can_commit': bool(result['summary']['update'] + result['summary']['unchanged'] + result['summary']['protected']),
                'can_save_issues': bool(result['summary']['issue_rows']),
                'requires_skip_confirmation': bool(result['summary']['issue_rows'])}

    @app.get('/api/scores/previews/{preview_id}/rows')
    def preview_rows(preview_id: str, kind: str = 'all', page: int = Query(1, ge=1),
                     page_size: int = Query(20, ge=1, le=100)):
        if kind not in {'all', 'issues'} | ACTIONS.keys():
            raise HTTPException(400, '筛选类型无效。')
        preview = read_stage(preview_id, '.score-preview.json')
        rows = filter_rows(preview['rows'], kind)
        return {'items': rows[(page - 1) * page_size:page * page_size],
                'total': len(rows), 'page': page, 'page_size': page_size}

    @app.get('/api/scores/previews/{preview_id}/export.csv')
    def export_preview(preview_id: str, kind: str = 'all'):
        if kind not in {'all', 'issues'} | ACTIONS.keys():
            raise HTTPException(400, '筛选类型无效。')
        preview = read_stage(preview_id, '.score-preview.json')
        return rows_csv(filter_rows(preview['rows'], kind), 'score-cleaning-' + kind + '.csv')

    @app.post('/api/scores/commit')
    def commit_scores(body: ScoreCommit):
        stage_path(body.preview_id, '.score-preview.json')
        with connection(db_path) as con:
            prior = con.execute('SELECT * FROM score_batches WHERE preview_id=?', (body.preview_id,)).fetchone()
            if prior:
                return {'ok': True, 'idempotent': True, 'batch': public_batch(prior)}
        preview = read_stage(body.preview_id, '.score-preview.json')
        summary = preview['summary']
        if summary['issue_rows'] and not body.skip_issues:
            raise HTTPException(409, f'有 {summary["issue_rows"]} 行异常。请修正文件，或明确确认“跳过异常，仅导入可匹配记录”。当前未写入。')
        has_scores = bool(summary['update'] + summary['unchanged'] + summary['protected'])
        if body.save_issues_only and has_scores:
            raise HTTPException(400, '本批有可匹配的成绩，请按普通导入确认，不能声明为仅保存异常。')
        if not has_scores and not (body.save_issues_only and summary['issue_rows'] and body.skip_issues):
            raise HTTPException(400, '没有可处理的匹配记录；如需保留异常，请明确选择仅保存异常记录。')
        with connection(db_path) as con:
            try:
                con.execute('BEGIN IMMEDIATE')
                prior = con.execute('SELECT * FROM score_batches WHERE preview_id=?', (body.preview_id,)).fetchone()
                if prior:
                    con.rollback()
                    return {'ok': True, 'idempotent': True, 'batch': public_batch(prior)}
                if revision(con) != preview['revision']:
                    raise HTTPException(409, '预览后数据库发生变化，未覆盖任何成绩。请重新生成预览。')
                options = dict(preview['options'], skip_issues=body.skip_issues,
                               save_issues_only=body.save_issues_only, mapping=preview['mapping'])
                batch_id = con.execute('''INSERT INTO score_batches(preview_id,name,filename,sha256,encoding,sheet_name,
                    options_json,summary_json,updated_count,skipped_count) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (body.preview_id, preview['batch_name'], preview['meta']['filename'], preview['meta']['sha256'],
                     preview['format']['encoding'], preview['sheet_name'], json.dumps(options, ensure_ascii=False),
                     json.dumps(summary, ensure_ascii=False), summary['update'],
                     summary['issue_rows'] + summary['protected'] + summary['duplicate'])).lastrowid
                for row in preview['rows']:
                    after_version = None
                    if row['action'] == 'update':
                        after_version = row['before']['version'] + 1
                        after = row['after']
                        con.execute('''UPDATE students SET status=?,score_rate=?,score_batch_id=?,
                            exam_started_at=?,exam_finished_at=?,answer_duration_seconds=?,
                            score_imported_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),version=version+1,
                            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE student_id=?''',
                            (after['status'], after['score_rate'], batch_id, after.get('exam_started_at'),
                             after.get('exam_finished_at'), after.get('answer_duration_seconds'), row['student_id']))
                        con.execute('INSERT INTO student_changes(student_id,action,before_json,after_json) VALUES(?,?,?,?)',
                            (row['student_id'], 'score_import', json.dumps(row['before'], ensure_ascii=False),
                             json.dumps(dict(row['after'], score_batch_id=batch_id, version=after_version), ensure_ascii=False)))
                    con.execute('''INSERT INTO score_import_rows(batch_id,row_number,student_id,action,record_json,after_version)
                        VALUES(?,?,?,?,?,?)''', (batch_id, row['row_number'], row['student_id'], row['action'],
                                                json.dumps(row, ensure_ascii=False, allow_nan=False), after_version))
                bump_revision(con)
                con.commit()
                batch = public_batch(con.execute('SELECT * FROM score_batches WHERE id=?', (batch_id,)).fetchone())
            except Exception:
                con.rollback()
                raise
        cleanup_upload(stage, preview['upload_id'], ('.score.bin', '.score-upload.json'), '.score-preview.json')
        return {'ok': True, 'idempotent': False, 'batch': batch}

    @app.get('/api/scores/batches')
    def score_batches(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=50)):
        with connection(db_path) as con, read_snapshot(con):
            total = con.execute('SELECT COUNT(*) FROM score_batches').fetchone()[0]
            page = min(page, max(1, (total + page_size - 1) // page_size))
            rows = con.execute('SELECT * FROM score_batches ORDER BY id DESC LIMIT ? OFFSET ?',
                               (page_size, (page - 1) * page_size))
            return {'items': [public_batch(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}

    @app.get('/api/scores/batches/{batch_id}/rows')
    def batch_rows(batch_id: int, kind: str = 'all', page: int = Query(1, ge=1),
                   page_size: int = Query(20, ge=1, le=100)):
        if kind not in {'all', 'issues'} | ACTIONS.keys():
            raise HTTPException(400, '筛选类型无效。')
        with connection(db_path) as con, read_snapshot(con):
            batch = con.execute('SELECT * FROM score_batches WHERE id=?', (batch_id,)).fetchone()
            if not batch:
                raise HTTPException(404, '批次不存在。')
            params = (batch_id, kind, kind, kind)
            total = con.execute('''SELECT COUNT(*) FROM score_import_rows WHERE batch_id=?
                AND (?='all' OR (?='issues' AND action IN ('unmatched','invalid','name_conflict','duplicate_conflict'))
                     OR action=?)''', params).fetchone()[0]
            rows = con.execute('''SELECT record_json FROM score_import_rows WHERE batch_id=?
                AND (?='all' OR (?='issues' AND action IN ('unmatched','invalid','name_conflict','duplicate_conflict'))
                     OR action=?) ORDER BY row_number LIMIT ? OFFSET ?''',
                               params + (page_size, (page - 1) * page_size))
            return {'batch': public_batch(batch), 'items': [saved_record(row[0]) for row in rows],
                    'total': total, 'page': page, 'page_size': page_size}

    @app.get('/api/scores/batches/{batch_id}/export.csv')
    def export_batch(batch_id: int, kind: str = 'all'):
        if kind not in {'all', 'issues'} | ACTIONS.keys():
            raise HTTPException(400, '筛选类型无效。')
        with connection(db_path) as con:
            if not con.execute('SELECT 1 FROM score_batches WHERE id=?', (batch_id,)).fetchone():
                raise HTTPException(404, '批次不存在。')
            rows = [saved_record(row[0]) for row in con.execute(
                'SELECT record_json FROM score_import_rows WHERE batch_id=? ORDER BY row_number', (batch_id,))]
        return rows_csv(filter_rows(rows, kind), f'score-batch-{batch_id}-{kind}.csv')

    @app.post('/api/scores/batches/{batch_id}/revert')
    def revert_batch(batch_id: int, body: RevertInput):
        if not body.confirm:
            raise HTTPException(400, '撤销需要明确确认。')
        with connection(db_path) as con:
            try:
                con.execute('BEGIN IMMEDIATE')
                batch = con.execute('SELECT * FROM score_batches WHERE id=?', (batch_id,)).fetchone()
                if not batch:
                    raise HTTPException(404, '批次不存在。')
                if batch['state'] == 'reverted':
                    con.rollback()
                    return {'ok': True, 'idempotent': True, 'reverted_count': batch['updated_count']}
                if con.execute('SELECT 1 FROM removal_score_batches WHERE score_batch_id=?', (batch_id,)).fetchone():
                    raise HTTPException(409, '此批次包含已剔除并清理明细的人员，不能再整批撤销；未修改任何成绩。')
                changes = con.execute("SELECT * FROM score_import_rows WHERE batch_id=? AND action='update'",
                                      (batch_id,)).fetchall()
                # All-or-nothing: never undo later edits, even if only one student changed.
                for row in changes:
                    current = con.execute('SELECT version,score_batch_id FROM students WHERE student_id=?',
                                          (row['student_id'],)).fetchone()
                    if not current or current['version'] != row['after_version'] or current['score_batch_id'] != batch_id:
                        raise HTTPException(409, '批次中的学生已有后续修改，不能整批撤销以免覆盖新数据。未撤销任何行；请查看导出记录人工核对。')
                for row in changes:
                    record = saved_record(row['record_json'])
                    old = record['before']
                    con.execute('''UPDATE students SET status=?,score_rate=?,score_batch_id=?,score_imported_at=?,
                        exam_started_at=?,exam_finished_at=?,answer_duration_seconds=?,
                        version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE student_id=?''',
                        (old['status'], old['score_rate'], old.get('score_batch_id'), old.get('score_imported_at'),
                         old.get('exam_started_at'), old.get('exam_finished_at'), old.get('answer_duration_seconds'), row['student_id']))
                    con.execute('INSERT INTO student_changes(student_id,action,before_json,after_json) VALUES(?,?,?,?)',
                        (row['student_id'], 'score_import_revert', json.dumps(record['after'], ensure_ascii=False),
                         json.dumps(old, ensure_ascii=False)))
                con.execute("UPDATE score_batches SET state='reverted',reverted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                            (batch_id,))
                bump_revision(con)
                con.commit()
                return {'ok': True, 'idempotent': False, 'reverted_count': len(changes)}
            except Exception:
                con.rollback()
                raise
