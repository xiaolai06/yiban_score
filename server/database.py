from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path

SCHEMA = Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')


def schema_statements(script: str):
    """Preserve complete SQL statements, including trigger bodies/quoted semicolons."""
    buffer = []
    for character in script:
        buffer.append(character)
        if character == ';':
            pending = ''.join(buffer)
            if sqlite3.complete_statement(pending):
                yield pending
                buffer.clear()
    trailing = re.sub(r'--[^\n]*(?:\n|$)|/\*.*?\*/', '', ''.join(buffer), flags=re.DOTALL)
    if trailing.strip():
        raise RuntimeError('数据库结构脚本不完整，未继续迁移。')


def init_db(path: Path) -> None:
    """Initialize or migrate recognized schemas atomically; preserve V1 backup.

    Unsupported metadata is rejected before persistent journal-mode changes.
    Exclusion guards and optional exam-time columns are additive; schema_version stays 2.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    try:
        old = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='students'").fetchone()
        meta = con.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone()
        if meta:
            version = con.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if version and str(version[0]) not in {'1', '2'}:
                raise RuntimeError('数据库版本不受此程序支持；为保护数据，请使用匹配版本的软件。')
        con.execute('PRAGMA journal_mode=WAL')
        migrate = bool(old and '未开始' not in old[0])
        if migrate:
            backup_dir = path.parent / 'backups'
            backup_dir.mkdir(exist_ok=True)
            stamp = datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f')
            backup_path = backup_dir / f'{path.stem}-before-v2-{stamp}.sqlite3'
            destination = sqlite3.connect(backup_path)
            try:
                con.backup(destination)
            finally:
                destination.close()
            with suppress(OSError):
                backup_path.chmod(0o600)
        con.execute('PRAGMA foreign_keys='+('OFF' if migrate else 'ON'))
        con.execute('BEGIN IMMEDIATE')
        try:
            if migrate:
                indexes = con.execute("SELECT sql FROM sqlite_master WHERE tbl_name='students' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
                definition = re.search(r'CREATE TABLE IF NOT EXISTS students \(.*?\n\);', SCHEMA, re.DOTALL)
                if definition is None:
                    raise RuntimeError('学生表结构定义缺失，已停止迁移。')
                con.execute(definition.group(0).replace('IF NOT EXISTS students', 'students_v2_migration'))
                columns = [record[1] for record in con.execute('PRAGMA table_info(students)')]
                # Database-owned identifiers are double-quoted; no request text is interpolated.
                joined = ','.join('"'+column.replace('"', '""')+'"' for column in columns)
                con.execute(f'INSERT INTO students_v2_migration({joined}) SELECT {joined} FROM students')
                con.execute('DROP TABLE students')
                con.execute('ALTER TABLE students_v2_migration RENAME TO students')
                for (statement,) in indexes:
                    con.execute(statement)
            for statement in schema_statements(SCHEMA):
                con.execute(statement)
            columns = {record[1] for record in con.execute('PRAGMA table_info(students)')}
            additions = {
                'exam_started_at': 'TEXT DEFAULT NULL',
                'exam_finished_at': 'TEXT DEFAULT NULL',
                'answer_duration_seconds': "INTEGER DEFAULT NULL CHECK(answer_duration_seconds IS NULL OR (typeof(answer_duration_seconds)='integer' AND answer_duration_seconds>=0))",
            }
            for name, definition in additions.items():
                if name not in columns:
                    con.execute(f'ALTER TABLE students ADD COLUMN {name} {definition}')
            if old and not set(additions).issubset(columns):
                bump_revision(con)  # Invalidate staged previews from the pre-time application.
            if migrate:
                con.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
                bump_revision(con)
            if con.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('数据库外键检查失败，已回滚初始化/迁移。')
            con.commit()
        except Exception:
            con.rollback()
            raise
        con.execute('PRAGMA foreign_keys=ON')
    finally:
        con.close()


@contextmanager
def connection(path: Path):
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=15000')
    try:
        yield con
    finally:
        con.close()


@contextmanager
def read_snapshot(con: sqlite3.Connection):
    owned = not con.in_transaction
    if owned:
        con.execute('BEGIN')
    try:
        yield con
    finally:
        if owned:
            con.rollback()


def revision(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
    try:
        if row is None:
            raise ValueError('Missing revision')
        value = int(row[0])
        if value < 0 or str(value) != str(row[0]):
            raise ValueError('Invalid revision')
        return value
    except (TypeError, ValueError, OverflowError) as exc:
        raise sqlite3.OperationalError('数据库修订号异常，请检查备份。') from exc


def bump_revision(con: sqlite3.Connection) -> None:
    # Never silently cast corrupt metadata to zero and invalidate concurrency guards.
    con.execute("UPDATE metadata SET value=? WHERE key='revision'", (str(revision(con)+1),))


STUDENT_SELECT = '''SELECT s.*, c.name AS class_name, c.college_id,
                          co.name AS college_name, sb.name AS score_batch_name,
                          sb.filename AS score_source_filename
                   FROM students s JOIN classes c ON s.class_id=c.id
                   JOIN colleges co ON c.college_id=co.id
                   LEFT JOIN score_batches sb ON sb.id=s.score_batch_id'''
