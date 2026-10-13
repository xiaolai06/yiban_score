PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '2'), ('revision', '0');
CREATE TABLE IF NOT EXISTS colleges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE CHECK(length(trim(name)) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    college_id INTEGER NOT NULL REFERENCES colleges(id) ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(college_id, name)
);
CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY NOT NULL CHECK(length(student_id) BETWEEN 1 AND 64),
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 80),
    gender TEXT NOT NULL DEFAULT '',
    major TEXT NOT NULL DEFAULT '',
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT '待录入' CHECK(status IN ('待录入','已录入','未开始','进行中','已完成','缺考','免考')),
    score_rate REAL DEFAULT NULL CHECK(score_rate IS NULL OR (score_rate >= 0 AND score_rate <= 100)),
    score_batch_id INTEGER DEFAULT NULL REFERENCES score_batches(id),
    score_imported_at TEXT DEFAULT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_classes_college ON classes(college_id);
CREATE INDEX IF NOT EXISTS ix_students_class ON students(class_id);
CREATE INDEX IF NOT EXISTS ix_students_name ON students(name);
CREATE INDEX IF NOT EXISTS ix_students_status ON students(status);
CREATE INDEX IF NOT EXISTS ix_students_rate ON students(score_rate);
CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    preview_id TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('update', 'skip')),
    source_rows INTEGER NOT NULL,
    new_count INTEGER NOT NULL,
    updated_count INTEGER NOT NULL,
    unchanged_count INTEGER NOT NULL,
    skipped_count INTEGER NOT NULL,
    college_count INTEGER NOT NULL,
    class_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS student_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    action TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS score_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    preview_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    encoding TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    options_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    updated_count INTEGER NOT NULL,
    skipped_count INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'committed' CHECK(state IN ('committed','reverted')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    reverted_at TEXT DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS score_import_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES score_batches(id),
    row_number INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    action TEXT NOT NULL,
    record_json TEXT NOT NULL,
    after_version INTEGER DEFAULT NULL,
    UNIQUE(batch_id,row_number)
);
CREATE INDEX IF NOT EXISTS ix_score_rows_batch ON score_import_rows(batch_id);
CREATE INDEX IF NOT EXISTS ix_score_rows_student ON score_import_rows(student_id);

-- Removal stores only aggregate audit and exclusion IDs, not deleted identity/score snapshots.
CREATE TABLE IF NOT EXISTS removal_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    preview_id TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    source_rows INTEGER NOT NULL,
    unique_ids INTEGER NOT NULL,
    deleted_count INTEGER NOT NULL,
    unmatched_count INTEGER NOT NULL,
    duplicate_rows INTEGER NOT NULL,
    score_rows INTEGER NOT NULL,
    change_rows INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS excluded_students (
    student_id TEXT PRIMARY KEY NOT NULL,
    removal_batch_id INTEGER NOT NULL REFERENCES removal_batches(id)
);
CREATE TABLE IF NOT EXISTS removal_score_batches (
    removal_batch_id INTEGER NOT NULL REFERENCES removal_batches(id),
    score_batch_id INTEGER NOT NULL REFERENCES score_batches(id),
    PRIMARY KEY(removal_batch_id, score_batch_id)
);
