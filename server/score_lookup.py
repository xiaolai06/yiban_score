"""Read-only current roster lookup; historic account IDs are only search hints."""
from __future__ import annotations

from fastapi import HTTPException, Query

from .database import STUDENT_SELECT, connection, read_snapshot, revision
from .grade_rules import PASS_SCORE, has_numeric_score, is_qualified
from .roster import STATUSES
from .score_import import search_needles
from .score_records import identity_fields


def install_lookup_routes(app, db_path, saved_record):
    @app.get('/api/scores/people')
    def people(q: str = Query('', max_length=120), status: str = '',
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        if status and status not in STATUSES:
            raise HTTPException(400, '状态筛选无效。')
        with connection(db_path) as con, read_snapshot(con):
            rev = revision(con)
            accounts = {}
            # Do not match names or import an account as a new student. Only an
            # exact existing student_id can attach a historical search hint.
            for row in con.execute('''SELECT r.student_id,r.record_json FROM score_import_rows r
                    JOIN students s ON s.student_id=r.student_id
                    WHERE (?='' OR s.status=?) AND r.record_json LIKE '%"yiban_id"%'
                    ORDER BY r.id''', (status, status)):
                value = identity_fields(saved_record(row['record_json']))['yiban_id']
                if value:
                    values = accounts.setdefault(row['student_id'], [])
                    if value not in values:
                        values.append(value)
            terms = search_needles(q)
            items = []
            for row in con.execute(STUDENT_SELECT):
                if status and row['status'] != status:
                    continue
                person = dict(row)
                yiban_ids = accounts.get(person['student_id'], [])
                texts = [person[key] for key in ('student_id', 'name', 'college_name', 'class_name', 'major')]
                texts += yiban_ids
                if terms and not any(term in text.casefold() for term in terms for text in texts):
                    continue
                person['historical_yiban_ids'] = yiban_ids
                person['qualified'] = is_qualified(person['score_rate'])
                person['settled'] = has_numeric_score(person['score_rate']) and person['status'] not in {'未开始', '进行中'}
                items.append(person)
            items.sort(key=lambda person: person['student_id'])
            total = len(items)
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            return {'items': items[(page - 1) * page_size:page * page_size], 'total': total,
                    'page': page, 'pages': pages, 'page_size': page_size, 'revision': rev, 'pass_score': PASS_SCORE}
