from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
)
from starlette.background import BackgroundTask

from .database import (
    STUDENT_SELECT,
    bump_revision,
    connection,
    init_db,
    read_snapshot,
    revision,
)
from .exam_time import TIME_FIELDS, format_duration
from .excel_reader import WorkbookError, read_workbook
from .grade_rules import (
    NUMERIC_SCORE_SQL,
    PASS_SCORE,
    QUALIFIED_SQL,
    UNQUALIFIED_SQL,
    VALID_SCORE_SQL,
    round_score,
)
from .request_limits import RequestBodyLimit
from .roster import FIELDS, REQUIRED, STATUSES, sheet_info, validate_rows
from .score_export import build_score_workbook
from .staging import (
    cleanup_upload,
    read_stage_record,
    read_upload_bytes,
    unlink_quietly,
)

ROOT = Path(__file__).resolve().parent.parent
MAX_UPLOAD = 20 * 1024 * 1024
PREVIEW_TTL = 3600
# Qualification is numerical >=60 everywhere; progress is tracked separately.
# below_90 remains a legacy API key for the unqualified group (<60 or missing).

class PreviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    upload_id: str
    sheet_index: StrictInt = Field(ge=0, le=49)
    header_row: StrictInt = Field(ge=1, le=60000)
    mapping: dict[str, StrictInt]
    mode: Literal['update', 'skip'] = 'update'

class CommitInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: str
    allow_name_changes: StrictBool = False

class StudentPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: StrictInt = Field(ge=1)
    status: Literal['待录入', '已录入', '未开始', '进行中', '已完成', '缺考', '免考']
    score_rate: float | None = Field(default=None, ge=0, le=100)

    @field_validator('score_rate', mode='before')
    @classmethod
    def rate_is_number(cls, value):
        if isinstance(value, bool) or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError('得分率必须是 0–100 的有限数值，不能是布尔值。')
        return value


class StudentFilterInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    q: str = Field(default='', max_length=120)
    college_id: StrictInt | None = Field(default=None, ge=1)
    class_id: StrictInt | None = Field(default=None, ge=1)
    status: str = ''
    score_filter: Literal['all', 'recorded', 'missing', 'below_60', 'below_90'] = 'all'


class ScoreExportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    all_students: StrictBool = False
    college_ids: list[StrictInt] = Field(default_factory=list, max_length=500)
    class_ids: list[StrictInt] = Field(default_factory=list, max_length=2000)
    below_60: StrictBool = False
    below_90: StrictBool = False  # Deprecated alias for below_60.
    expected_revision: StrictInt | None = Field(default=None, ge=0)
    student_ids: list[str] = Field(default_factory=list, max_length=25000)
    excluded_student_ids: list[str] = Field(default_factory=list, max_length=25000)
    student_filter: StudentFilterInput | None = None

    @field_validator('student_ids', 'excluded_student_ids')
    @classmethod
    def valid_student_ids(cls, values):
        if any(not value or len(value) > 64 for value in values):
            raise ValueError('学号须为1–64字的文本。')
        return sorted(set(values))

    @field_validator('college_ids', 'class_ids')
    @classmethod
    def positive_ids(cls, values):
        if any(value < 1 for value in values):
            raise ValueError('学院和班级编号必须为正整数。')
        return sorted(set(values))


def safe_json_write(path: Path, obj: dict) -> None:
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(path)
    with suppress(OSError):
        path.chmod(0o600)


def validate_public_host(value: str) -> str:
    """Accept one DNS hostname, never a URL, wildcard, IP address or port."""
    host = value.strip().lower()
    if len(host) > 253 or not re.fullmatch(
        r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host
    ):
        raise ValueError('STUDENT_PUBLIC_HOST 必须是完整域名，例如 score-example.duckdns.org（不含 https:// 或端口）。')
    return host


def create_app(db_path: str | Path | None = None, *, testing: bool = False,
               public_host: str | None = None) -> FastAPI:
    public_host = validate_public_host(public_host) if public_host else None
    db_path = Path(db_path or os.environ.get('STUDENT_DB_PATH', ROOT / 'data' / 'students.sqlite3')).resolve()
    stage = db_path.parent / '.staging' / hashlib.sha256(str(db_path).encode('utf-8')).hexdigest()[:16]
    stage.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    def clean_expired() -> None:
        now = time.time()
        for f in stage.iterdir():
            try:
                if f.is_file() and now - f.stat().st_mtime > PREVIEW_TTL:
                    f.unlink(missing_ok=True)
            except OSError:
                pass

    @asynccontextmanager
    async def lifespan(app):
        clean_expired()
        yield
        with connection(db_path) as con:
            con.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    app = FastAPI(title='学绩 · 学生成绩管理', version='2.0.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url='/api/openapi.json')
    app.state.db_path = db_path
    app.state.public_host = public_host
    app.add_middleware(RequestBodyLimit, max_bytes=MAX_UPLOAD+1024*1024)

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        # Public mode still requires a loopback-bound server and a trusted HTTPS
        # reverse proxy with an IP allowlist. This app has no user accounts.
        host = request.url.hostname or ''
        client = request.client.host if request.client else ''
        if public_host:
            if host != public_host or request.url.scheme != 'https':
                return JSONResponse({'detail': '仅允许通过已配置域名的 HTTPS 入口访问。'}, status_code=403)
        else:
            allowed_host = host in {'127.0.0.1', 'localhost', '::1'} or (testing and host == 'testserver')
            allowed_client = client in {'127.0.0.1', '::1'} or (testing and client == 'testclient')
            if not allowed_host or not allowed_client:
                return JSONResponse({'detail': '此版本仅允许本机访问。'}, status_code=403)
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': '拒绝跨站写入请求。'}, status_code=403)
            if request.headers.get('x-requested-with') != 'StudentScore':
                return JSONResponse({'detail': '写入请求缺少应用标记。'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(WorkbookError)
    async def workbook_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(sqlite3.OperationalError)
    async def sqlite_error(request, exc):
        # Do not expose queries, filesystem paths, or records in browser errors.
        return JSONResponse({'detail': '数据库暂时不可用或正被其他程序占用。请关闭数据库编辑器后重试。'}, status_code=503)

    def stage_path(token: str, suffix: str) -> Path:
        if not re.fullmatch(r'[0-9a-f]{32}', token):
            raise HTTPException(400, '临时导入标识无效。')
        return stage / f'{token}{suffix}'

    def read_stage(token: str, suffix: str) -> dict:
        return read_stage_record(stage_path(token, suffix), suffix, PREVIEW_TTL)

    def read_upload(upload_id: str):
        meta = read_stage(upload_id, '.upload.json')
        data = read_upload_bytes(stage_path(upload_id, '.bin'), meta)
        return meta, read_workbook(data, meta['filename'])

    def stats_data(con) -> dict:
        # Reuse a caller's transaction, or create a read snapshot for all four queries.
        with read_snapshot(con):
            return stats_snapshot(con)

    def stats_snapshot(con) -> dict:
        row = dict(con.execute(f'''SELECT COUNT(*) AS students,
            COUNT(DISTINCT s.class_id) AS classes, COUNT(DISTINCT c.college_id) AS colleges,
            COUNT(CASE WHEN {QUALIFIED_SQL} THEN 1 END) AS qualified,
            COUNT(s.score_rate) AS recorded, COUNT(CASE WHEN {VALID_SCORE_SQL} THEN s.score_rate END) AS settled, ROUND(AVG(CASE WHEN {VALID_SCORE_SQL} THEN s.score_rate END),2) AS average_rate
            FROM students s JOIN classes c ON c.id=s.class_id''').fetchone())
        counts = {r['status']: r['count'] for r in con.execute('SELECT status,COUNT(*) AS count FROM students GROUP BY status')}
        row['statuses'] = [{'status': x, 'count': counts.get(x, 0)} for x in STATUSES]
        row['revision'] = revision(con)
        row['valid_scores'] = row['settled']
        row['completed'] = row['settled']
        row['pass_score'] = PASS_SCORE
        last_import = con.execute('SELECT * FROM import_batches ORDER BY id DESC LIMIT 1').fetchone()
        row['last_import'] = dict(last_import) if last_import else None
        return row

    @app.get('/api/health')
    def health():
        with connection(db_path) as con:
            return {'ok': True, 'storage': 'SQLite', 'database_file': db_path.name,
                    'local_only': public_host is None, 'schema_version': 2, 'directory_api_version': 6, 'revision': revision(con),
                    'statuses': STATUSES, 'max_upload_mb': 20, 'preview_ttl_minutes': 60, 'roster_removal_api_version': 1, 'score_export_workbook_version': 6, 'follow_up_api_version': 4, 'exam_time_api_version': 1, 'score_trace_api_version': 1, 'score_people_api_version': 1, 'pass_score': PASS_SCORE, 'read_snapshot_api_version': 1}

    @app.get('/api/stats')
    def stats():
        with connection(db_path) as con, read_snapshot(con):
            result = stats_data(con)
            result['colleges_detail'] = [dict(r) for r in con.execute(f'''
                SELECT co.id, co.name, COUNT(s.student_id) AS students,
                       COUNT(DISTINCT c.id) AS classes, COUNT(s.score_rate) AS recorded, COUNT(CASE WHEN {VALID_SCORE_SQL} THEN s.score_rate END) AS settled,
                       COUNT(CASE WHEN {VALID_SCORE_SQL} THEN 1 END) AS completed,
                       COUNT(CASE WHEN {QUALIFIED_SQL} THEN 1 END) AS qualified,
                       ROUND(AVG(CASE WHEN {VALID_SCORE_SQL} THEN s.score_rate END),2) AS average_rate
                FROM colleges co JOIN classes c ON c.college_id=co.id
                JOIN students s ON s.class_id=c.id GROUP BY co.id
                ORDER BY students DESC, co.id''')]
            return result

    @app.get('/api/hierarchy')
    def hierarchy():
        with connection(db_path) as con, read_snapshot(con):
            rev = revision(con)
            rows = con.execute('''SELECT co.id AS college_id,co.name AS college_name,
                c.id,c.name,COUNT(s.student_id) AS count,COUNT(s.score_rate) AS recorded
                FROM colleges co JOIN classes c ON c.college_id=co.id
                JOIN students s ON s.class_id=c.id GROUP BY c.id ORDER BY co.id,c.name''').fetchall()
            colleges = {}
            for r in rows:
                co = colleges.setdefault(r['college_id'], {'id': r['college_id'], 'name': r['college_name'], 'count': 0, 'classes': []})
                co['count'] += r['count']
                co['classes'].append({'id': r['id'], 'name': r['name'], 'count': r['count'], 'recorded': r['recorded']})
            return {'colleges': list(colleges.values()), 'revision': rev}

    @app.post('/api/imports/analyze')
    def analyze(file: Annotated[UploadFile, File()]):
        clean_expired()
        if len(list(stage.glob('*.upload.json'))) >= 30:
            raise HTTPException(429, '临时上传过多；请先完成已有导入，或等待临时文件过期。')
        try:
            data = file.file.read(MAX_UPLOAD + 1)
        finally:
            file.file.close()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, '文件不能超过 20 MB。')
        filename = Path((file.filename or 'roster.xls').replace('\\', '/')).name[:240]
        sheets = read_workbook(data, filename)
        info = [sheet_info(sh, i) for i, sh in enumerate(sheets)]
        token = secrets.token_hex(16)
        meta = {'created_at': time.time(), 'filename': filename, 'sha256': hashlib.sha256(data).hexdigest()}
        stage_path(token, '.bin').write_bytes(data)
        with suppress(OSError):
            stage_path(token, '.bin').chmod(0o600)
        safe_json_write(stage_path(token, '.upload.json'), meta)
        best = max(range(len(info)), key=lambda i: (info[i]['recognized_required'], not info[i]['hidden'], info[i]['rows']))
        return {'upload_id': token, 'filename': filename, 'size': len(data), 'sheets': info,
                'selected_sheet': best, 'fields': FIELDS, 'required': REQUIRED}

    @app.get('/api/imports/{upload_id}/sheet')
    def inspect_sheet(upload_id: str, sheet_index: int = Query(ge=0, le=49), header_row: int = Query(ge=1, le=60000)):
        _, sheets = read_upload(upload_id)
        if sheet_index >= len(sheets):
            raise HTTPException(400, '工作表不存在。')
        try:
            return sheet_info(sheets[sheet_index], sheet_index, header_row)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post('/api/imports/preview')
    def preview(body: PreviewInput):
        meta, sheets = read_upload(body.upload_id)
        if body.sheet_index >= len(sheets):
            raise HTTPException(400, '工作表不存在。')
        sh = sheets[body.sheet_index]
        try:
            validated = validate_rows(sh, body.header_row, body.mapping)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        counts = {'new': 0, 'updated': 0, 'unchanged': 0, 'skipped_existing': 0, 'excluded': 0}
        sample, conflicts = [], []
        with connection(db_path) as con:
            # A single consistent snapshot underpins counts and optimistic commit.
            con.execute('BEGIN')
            rev = revision(con)
            existing = {r['student_id']: dict(r) for r in con.execute(STUDENT_SELECT)}
            excluded = {r[0] for r in con.execute('SELECT student_id FROM excluded_students')}
            for row in validated['rows']:
                old = existing.get(row['student_id'])
                action = 'new'
                changes = []
                if row['student_id'] in excluded:
                    action = 'excluded'
                elif old:
                    if body.mode == 'skip':
                        action = 'skipped_existing'
                    else:
                        for key, old_key in [('name','name'),('gender','gender'),('major','major'),('college','college_name'),('class_name','class_name')]:
                            if row[key] != old[old_key]:
                                changes.append({'field': FIELDS[key], 'before': old[old_key], 'after': row[key]})
                        action = 'updated' if changes else 'unchanged'
                        if old['name'] != row['name']:
                            conflicts.append({'row_number': row['row_number'], 'student_id': row['student_id'],
                                              'before': old['name'], 'after': row['name']})
                counts[action] += 1
                if len(sample) < 25:
                    shown: dict = dict(row, action=action, changes=changes)
                    if old:
                        shown['status'], shown['score_rate'] = old['status'], old['score_rate']
                        shown.update({key:old.get(key) for key in TIME_FIELDS})
                    sample.append(shown)
            con.rollback()
        summary = dict(counts, source_rows=validated['source_rows'], valid_rows=len(validated['rows']),
                       error_count=len(validated['errors']), name_conflict_count=len(conflicts),
                       colleges=len({r['college'] for r in validated['rows']}),
                       classes=len({(r['college'], r['class_name']) for r in validated['rows']}))
        if counts['excluded']:
            validated['warnings'].append(f'已剔除的 {counts["excluded"]} 个学号将跳过，不会重新加入名单或统计。')
        preview_id = secrets.token_hex(16)
        payload = {'created_at': time.time(), 'upload_id': body.upload_id, 'meta': meta,
                   'sheet_name': sh.name, 'header_row': body.header_row, 'mapping': body.mapping,
                   'mode': body.mode, 'revision': rev, 'summary': summary,
                   'rows': validated['rows'], 'errors': validated['errors'],
                   'warnings': validated['warnings'], 'name_conflicts': conflicts}
        safe_json_write(stage_path(preview_id, '.preview.json'), payload)
        return {'preview_id': preview_id, 'summary': summary, 'sample': sample,
                'errors': validated['errors'][:100], 'warnings': validated['warnings'],
                'name_conflicts': conflicts[:50], 'can_commit': not validated['errors'], 'revision': rev}

    @app.get('/api/imports/{preview_id}/errors.csv')
    def download_errors(preview_id: str):
        payload = read_stage(preview_id, '.preview.json')
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['Excel行号', '学号', '问题说明'])
        for row in payload['errors']:
            writer.writerow([row['row_number'], safe_csv(row['student_id']), safe_csv(row['message'])])
        return csv_response(stream.getvalue(), 'import-errors.csv')

    @app.post('/api/imports/commit')
    def commit(body: CommitInput):
        stage_path(body.preview_id, '.preview.json')  # validate token before DB lookup
        with connection(db_path) as con:
            prior = con.execute('SELECT * FROM import_batches WHERE preview_id=?', (body.preview_id,)).fetchone()
            if prior:
                return {'ok': True, 'idempotent': True, 'batch': dict(prior), 'stats': stats_data(con)}
        payload = read_stage(body.preview_id, '.preview.json')
        if payload['errors']:
            raise HTTPException(400, '存在校验错误，未写入任何学生。请修正原表后重新上传。')
        if payload['name_conflicts'] and not body.allow_name_changes:
            raise HTTPException(409, '存在同学号、不同姓名的记录。请核对姓名变更后勾选确认。')
        with connection(db_path) as con:
            try:
                con.execute('BEGIN IMMEDIATE')
                prior = con.execute('SELECT * FROM import_batches WHERE preview_id=?', (body.preview_id,)).fetchone()
                if prior:
                    con.rollback()
                    return {'ok': True, 'idempotent': True, 'batch': dict(prior), 'stats': stats_data(con)}
                if revision(con) != payload['revision']:
                    raise HTTPException(409, '预览后数据库已有变更。为避免覆盖新修改，请重新生成预览。')
                existing = {r['student_id']: dict(r) for r in con.execute(STUDENT_SELECT)}
                college_map = {r['name']: r['id'] for r in con.execute('SELECT id,name FROM colleges')}
                class_map = {(r['college_id'], r['name']): r['id'] for r in con.execute('SELECT * FROM classes')}
                excluded = {r[0] for r in con.execute('SELECT student_id FROM excluded_students')}
                for row in payload['rows']:
                    if row['student_id'] in excluded:
                        continue
                    old = existing.get(row['student_id'])
                    if old and payload['mode'] == 'skip':
                        continue
                    if old and all(row[k] == old[ok] for k, ok in [('name','name'),('gender','gender'),('major','major'),('college','college_name'),('class_name','class_name')]):
                        continue
                    co_id = college_map.get(row['college'])
                    if co_id is None:
                        co_id = con.execute('INSERT INTO colleges(name) VALUES(?)', (row['college'],)).lastrowid
                        college_map[row['college']] = co_id
                    class_id = class_map.get((co_id, row['class_name']))
                    if class_id is None:
                        class_id = con.execute('INSERT INTO classes(college_id,name) VALUES(?,?)', (co_id, row['class_name'])).lastrowid
                        class_map[co_id, row['class_name']] = class_id
                    if old:
                        con.execute('''UPDATE students SET name=?,gender=?,major=?,class_id=?,
                            version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                            WHERE student_id=?''', (row['name'], row['gender'], row['major'], class_id, row['student_id']))
                        after = dict(row, status=old['status'], score_rate=old['score_rate'])
                        con.execute('INSERT INTO student_changes(student_id,action,before_json,after_json) VALUES(?,?,?,?)',
                                    (row['student_id'], 'roster_import', json.dumps(old, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))
                    else:
                        con.execute('''INSERT INTO students(student_id,name,gender,major,class_id,status,score_rate)
                            VALUES(?,?,?,?,?,?,?)''', (row['student_id'], row['name'], row['gender'], row['major'], class_id, row['status'], row['score_rate']))
                s = payload['summary']
                batch_id = con.execute('''INSERT INTO import_batches(preview_id,filename,sha256,sheet_name,mode,
                    source_rows,new_count,updated_count,unchanged_count,skipped_count,college_count,class_count)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (body.preview_id,payload['meta']['filename'],payload['meta']['sha256'],
                    payload['sheet_name'],payload['mode'],s['source_rows'],s['new'],s['updated'],s['unchanged'],
                    s['skipped_existing']+s.get('excluded', 0),s['colleges'],s['classes'])).lastrowid
                bump_revision(con)
                con.commit()
                batch = dict(con.execute('SELECT * FROM import_batches WHERE id=?', (batch_id,)).fetchone())
                result = {'ok': True, 'idempotent': False, 'batch': batch, 'stats': stats_data(con)}
            except Exception:
                con.rollback()
                raise
        # Cleanup cannot turn an already committed transaction into a failed response.
        cleanup_upload(stage, payload['upload_id'], ('.bin', '.upload.json'), '.preview.json')
        return result

    @app.get('/api/imports')
    def history():
        with connection(db_path) as con:
            return {'items': [dict(r) for r in con.execute('SELECT * FROM import_batches ORDER BY id DESC LIMIT 50')]}

    def build_filter(q, college_id, class_id, status, score_filter):
        parts, params = [], []
        if q.strip():
            # Treat %, _ and backslash as literal search text, not SQL wildcards.
            needle = q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            parts.append("(s.student_id LIKE ? ESCAPE '\\' OR s.name LIKE ? ESCAPE '\\')")
            params.extend([f'%{needle}%'] * 2)
        if college_id is not None:
            parts.append('c.college_id=?')
            params.append(college_id)
        if class_id is not None:
            parts.append('s.class_id=?')
            params.append(class_id)
        if status:
            if status not in STATUSES:
                raise HTTPException(400, '状态筛选无效。')
            parts.append('s.status=?')
            params.append(status)
        if score_filter == 'recorded':
            parts.append('s.score_rate IS NOT NULL')
        elif score_filter == 'missing':
            parts.append('s.score_rate IS NULL')
        elif score_filter in {'below_60', 'below_90'}:
            parts.append(UNQUALIFIED_SQL)
        return (' WHERE ' + ' AND '.join(parts)) if parts else '', params

    @app.get('/api/students/export.csv')
    def export_students(q: str = Query('', max_length=120), college_id: int | None = Query(None, ge=1),
                        class_id: int | None = Query(None, ge=1), status: str = '',
                        score_filter: Literal['all', 'recorded', 'missing', 'below_60', 'below_90'] = 'all'):
        where, params = build_filter(q, college_id, class_id, status, score_filter)
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['学号', '姓名', '性别', '院系', '专业', '班级', '状态', '得分率(%)', '开始时间', '结束时间', '答题时长'])
        with connection(db_path) as con:
            for row in con.execute(STUDENT_SELECT + where + ' ORDER BY s.student_id', params):
                values = [row['student_id'], row['name'], row['gender'], row['college_name'], row['major'], row['class_name'], row['status'],
                          '' if row['score_rate'] is None else row['score_rate'],
                          row['exam_started_at'], row['exam_finished_at'], format_duration(row['answer_duration_seconds'])]
                writer.writerow([safe_csv(v) for v in values])
        return csv_response(out.getvalue(), 'students-export.csv')

    @app.post('/api/students/export.xlsx')
    def export_score_workbook(selection: ScoreExportInput):
        unqualified_only = selection.below_60 or selection.below_90
        has_scope = selection.all_students or bool(selection.college_ids or selection.class_ids)
        has_students = bool(selection.student_ids) or selection.student_filter is not None
        if not has_scope and not has_students:
            raise HTTPException(400, '请先选择学生、学院、班级或全校范围。')
        if has_scope and has_students or selection.student_ids and selection.student_filter is not None:
            raise HTTPException(400, '学生勾选与学院/班级范围须分别导出，不能混用。')
        if selection.excluded_student_ids and selection.student_filter is None:
            raise HTTPException(400, '取消勾选名单须与全选筛选范围一同提交。')
        parts, params = [], []
        with connection(db_path) as con:
            con.execute('BEGIN')
            rev = revision(con)
            if selection.expected_revision is not None and selection.expected_revision != rev:
                raise HTTPException(409, '导出前数据已有变更，请刷新成绩列表并重新核对选择。')
            # Single-row inserts avoid SQLite's per-statement variable limit even
            # with 25,000 selected/excluded IDs or 2,000 classes.
            con.execute('CREATE TEMP TABLE export_scope(kind TEXT,id INTEGER,PRIMARY KEY(kind,id))')
            con.executemany('INSERT INTO export_scope VALUES(?,?)',
                            [('college', value) for value in selection.college_ids]+
                            [('class', value) for value in selection.class_ids])
            college_count = con.execute("SELECT COUNT(*) FROM colleges WHERE id IN (SELECT id FROM export_scope WHERE kind='college')").fetchone()[0]
            class_count = con.execute("SELECT COUNT(*) FROM classes WHERE id IN (SELECT id FROM export_scope WHERE kind='class')").fetchone()[0]
            if college_count != len(selection.college_ids) or class_count != len(selection.class_ids):
                raise HTTPException(404, '选中的学院或班级已不存在，请刷新目录后重新选择。')
            con.execute('CREATE TEMP TABLE export_students(student_id TEXT PRIMARY KEY)')
            con.execute('CREATE TEMP TABLE export_excluded(student_id TEXT PRIMARY KEY)')
            con.executemany('INSERT INTO export_students VALUES(?)', [(value,) for value in selection.student_ids])
            con.executemany('INSERT INTO export_excluded VALUES(?)', [(value,) for value in selection.excluded_student_ids])
            if selection.student_ids:
                found = con.execute('SELECT COUNT(*) FROM students WHERE student_id IN (SELECT student_id FROM export_students)').fetchone()[0]
                if found != len(selection.student_ids):
                    raise HTTPException(404, '勾选的学生已不存在，请刷新成绩列表后重新选择。')
                parts.append('s.student_id IN (SELECT student_id FROM export_students)')
            elif selection.student_filter is not None:
                filters = selection.student_filter
                where, filter_params = build_filter(filters.q, filters.college_id, filters.class_id,
                                                    filters.status, filters.score_filter)
                if where:
                    parts.append('(' + where.removeprefix(' WHERE ') + ')')
                    params.extend(filter_params)
                if selection.excluded_student_ids:
                    parts.append('s.student_id NOT IN (SELECT student_id FROM export_excluded)')
            elif not selection.all_students:
                parts.append("(c.college_id IN (SELECT id FROM export_scope WHERE kind='college') OR s.class_id IN (SELECT id FROM export_scope WHERE kind='class'))")
            if unqualified_only:
                parts.append(UNQUALIFIED_SQL)
            where = ' WHERE ' + ' AND '.join(parts) if parts else ''
            rows = [dict(row) for row in con.execute(STUDENT_SELECT + where + ' ORDER BY co.id,c.id,s.student_id', params)]
            con.rollback()
        if not rows:
            raise HTTPException(400, '所选范围没有不合格人员（低于60分或无有效成绩）。' if unqualified_only else '所选范围没有学生。')
        try:
            content = build_score_workbook(rows)
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from exc
        filename = 'scores-unqualified.xlsx' if unqualified_only else 'scores-selected.xlsx'
        return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"',
                                 'X-Export-Students': str(len(rows)), 'X-Export-Workbook-Version': '6',
                                 'X-Data-Revision': str(rev)})

    @app.get('/api/students')
    def students(q: str = Query('', max_length=120), college_id: int | None = Query(None, ge=1),
                 class_id: int | None = Query(None, ge=1), status: str = '',
                 score_filter: Literal['all', 'recorded', 'missing', 'below_60', 'below_90'] = 'all',
                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 sort: Literal['student_id', 'name', 'score_rate', 'updated_at', 'college_name', 'class_name'] = 'student_id',
                 order: Literal['asc', 'desc'] = 'asc'):
        where, params = build_filter(q, college_id, class_id, status, score_filter)
        with connection(db_path) as con:
            con.execute('BEGIN')
            rev = revision(con)
            aggregate = dict(con.execute(f'''SELECT COUNT(*) AS total, COUNT(s.score_rate) AS recorded, COUNT(CASE WHEN {VALID_SCORE_SQL} THEN s.score_rate END) AS settled,
                COUNT(CASE WHEN {QUALIFIED_SQL} THEN 1 END) AS qualified,
                COUNT(CASE WHEN {NUMERIC_SCORE_SQL} AND s.score_rate = {PASS_SCORE} THEN 1 END) AS at_60,
                COUNT(CASE WHEN {NUMERIC_SCORE_SQL} AND s.score_rate = 90 THEN 1 END) AS at_90,
                ROUND(AVG(CASE WHEN {VALID_SCORE_SQL} THEN s.score_rate END),2) AS average_rate FROM students s
                JOIN classes c ON c.id=s.class_id''' + where, params).fetchone())
            total = aggregate['total']
            aggregate['valid_scores'] = aggregate['settled']
            # Boundary counts are qualified subsets, never a third group.
            aggregate['pass_score'] = PASS_SCORE
            aggregate['completed'] = aggregate['settled']
            aggregate['below_60'] = total - aggregate['qualified']
            aggregate['below_90'] = aggregate['below_60']  # Deprecated alias.
            pages = max(1, math.ceil(total / page_size))
            actual_page = min(page, pages)
            sort_columns = {'student_id': 's.student_id', 'name': 's.name', 'score_rate': 's.score_rate',
                            'updated_at': 's.updated_at', 'college_name': 'co.name', 'class_name': 'c.name'}
            ordering = ("s.score_rate IS NULL, " if sort == 'score_rate' else '') + f'{sort_columns[sort]} {order.upper()},s.student_id ASC'
            items = [dict(r) for r in con.execute(STUDENT_SELECT + where + f' ORDER BY {ordering} LIMIT ? OFFSET ?',
                                                 params + [page_size, (actual_page - 1) * page_size])]
            con.rollback()
            return dict(aggregate, page=actual_page, page_size=page_size, pages=pages, items=items, revision=rev)

    @app.get('/api/students/{student_id}')
    def student_detail(student_id: str):
        with connection(db_path) as con:
            row = con.execute(STUDENT_SELECT + ' WHERE s.student_id=?', (student_id,)).fetchone()
            if row is None:
                raise HTTPException(404, '学生不存在。')
            return dict(row)

    @app.patch('/api/students/{student_id}')
    def update_student(student_id: str, patch: StudentPatch):
        score_rate = None if patch.score_rate is None else round_score(patch.score_rate)
        with connection(db_path) as con:
            try:
                con.execute('BEGIN IMMEDIATE')
                before = con.execute(STUDENT_SELECT + ' WHERE s.student_id=?', (student_id,)).fetchone()
                if not before:
                    raise HTTPException(404, '学生不存在。')
                if before['version'] != patch.version:
                    raise HTTPException(409, '此学生已在其他窗口被修改，请关闭编辑框并重新打开。')
                if before['status'] == patch.status and before['score_rate'] == score_rate:
                    con.rollback()
                    return dict(before)
                con.execute('''UPDATE students SET status=?,score_rate=?,score_batch_id=NULL,score_imported_at=NULL,version=version+1,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE student_id=?''', (patch.status, score_rate, student_id))
                after = dict(con.execute(STUDENT_SELECT + ' WHERE s.student_id=?', (student_id,)).fetchone())
                con.execute('INSERT INTO student_changes(student_id,action,before_json,after_json) VALUES(?,?,?,?)',
                            (student_id, 'score_edit', json.dumps(dict(before), ensure_ascii=False), json.dumps(after, ensure_ascii=False)))
                bump_revision(con)
                con.commit()
                return after
            except Exception:
                con.rollback()
                raise

    @app.get('/api/database/backup')
    def backup_database():
        path = db_path.parent / f'.backup-{secrets.token_hex(8)}.sqlite3'
        try:
            with connection(db_path) as source:
                destination = sqlite3.connect(path)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
            return FileResponse(path, media_type='application/vnd.sqlite3',
                                filename='students-backup.sqlite3', background=BackgroundTask(unlink_quietly, path))
        except Exception:
            unlink_quietly(path)
            raise

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'web' / 'index.html', headers={'Cache-Control': 'no-cache'})

    from .roster_removal import install_removal_routes
    install_removal_routes(app, db_path, stage, read_upload, read_stage, stage_path, safe_json_write, csv_response, safe_csv)

    from .score_api import install_score_routes
    install_score_routes(app, db_path, stage, read_stage, stage_path, safe_json_write, clean_expired, csv_response, safe_csv)

    from .follow_up import install_follow_up_routes
    install_follow_up_routes(app, db_path, csv_response, safe_csv)

    app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')
    return app


def safe_csv(value) -> str:
    text = '' if value is None else str(value)
    # CSV must never execute spreadsheet formulas when opened in Excel/WPS.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text


def csv_response(text: str, filename: str) -> Response:
    return Response(content=('\ufeff' + text).encode('utf-8'), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})
