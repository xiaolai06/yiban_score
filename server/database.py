from __future__ import annotations
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')


def init_db(path: Path) -> None:
    """Initialize V2 or migrate the supplied V1, preserving identifiers/audit.

    Uses SQLite's generalized ALTER TABLE sequence. The original database gets
    an online-backup snapshot BEFORE changes. Never changes an unrecognized
    future schema. Migration, checks and revision bump form one transaction.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    try:
        con.execute('PRAGMA journal_mode=WAL')
        old = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='students'").fetchone()
        meta = con.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone()
        if meta:
            version = con.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if version and int(version[0])>2:
                raise RuntimeError('数据库版本高于此程序；为保护数据，请使用匹配版本的软件。')
        if old and '未开始' not in old[0]:
            backup_dir=path.parent/'backups';backup_dir.mkdir(exist_ok=True)
            backup_path=backup_dir/(path.stem+'-before-v2-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
            dest=sqlite3.connect(backup_path)
            try:con.backup(dest)
            finally:dest.close()
            try:backup_path.chmod(0o600)
            except OSError:pass
            con.execute('PRAGMA foreign_keys=OFF')
            con.execute('BEGIN IMMEDIATE')
            try:
                indexes=con.execute("SELECT sql FROM sqlite_master WHERE tbl_name='students' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
                new_sql=re.search(r'CREATE TABLE IF NOT EXISTS students \(.*?\n\);',SCHEMA,re.S).group(0)
                con.execute(new_sql.replace('IF NOT EXISTS students','students_v2_migration'))
                columns=[r[1] for r in con.execute('PRAGMA table_info(students)')]
                # The known V1 column names are safe to quote from schema metadata.
                joined=','.join('"'+c.replace('"','""')+'"' for c in columns)
                con.execute(f'INSERT INTO students_v2_migration({joined}) SELECT {joined} FROM students')
                con.execute('DROP TABLE students')
                con.execute('ALTER TABLE students_v2_migration RENAME TO students')
                for (sql,) in indexes:con.execute(sql)
                for statement in SCHEMA.split(';'):
                    if statement.strip():con.execute(statement)
                con.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
                bump_revision(con)
                if con.execute('PRAGMA foreign_key_check').fetchone():
                    raise RuntimeError('数据库迁移外键检查失败，已回滚。')
                con.commit()
            except Exception:
                con.rollback();raise
        else:
            con.executescript(SCHEMA)
            con.commit()
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


def revision(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0])


def bump_revision(con: sqlite3.Connection) -> None:
    con.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


STUDENT_SELECT = '''SELECT s.*, c.name AS class_name, c.college_id,
                          co.name AS college_name, sb.name AS score_batch_name,
                          sb.filename AS score_source_filename
                   FROM students s JOIN classes c ON s.class_id=c.id
                   JOIN colleges co ON c.college_id=co.id
                   LEFT JOIN score_batches sb ON sb.id=s.score_batch_id'''
