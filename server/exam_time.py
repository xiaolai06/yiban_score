"""Optional exam timestamps and elapsed time; no clock, status, or score guessing."""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timedelta

from .excel_reader import Cell

TIME_FIELDS = ('exam_started_at', 'exam_finished_at', 'answer_duration_seconds')
MISSING_TIMES = {'', '-', '--', '—', '–', '无', 'null', 'none', 'n/a', 'na', '未开始', '未结束', '未正常交卷', '暂无', '暂无记录', '未提供', '未填写', '未录入', '未结算'}


def clean_exam_time(cell: Cell, *, date_1904: bool = False) -> str | None:
    if cell.formula or cell.error or isinstance(cell.value, bool):
        raise ValueError('时间不能是公式、错误或布尔值。')
    text = re.sub(r'[\ufeff\u200b\u200c\u200d\u2060]', '', unicodedata.normalize('NFKC', cell.text())).strip().lstrip("`'").strip()
    if isinstance(cell.value, (int, float)):
        # Only Excel cells with an explicit date format are serial dates, never CSV numbers.
        fmt = re.sub(r'"[^"]*"|\\.', '', cell.number_format.lower())
        if not math.isfinite(cell.value) or not ('y' in fmt and 'd' in fmt):
            raise ValueError('时间须包含完整日期和时分秒；不猜测普通数字或时间戳。')
        try:
            serial = cell.value
            if not date_1904 and 60 <= serial < 61:
                raise ValueError('Excel 的 1900-02-29 不是真实日期。')
            base = datetime(1904, 1, 1) if date_1904 else datetime(1899, 12, 30 if serial >= 61 else 31)  # noqa: DTZ001 -- Excel serials are timezone-free wall times.
            # Excel floats have tiny binary errors; normalize them to the nearest second.
            value = base + timedelta(seconds=round(serial * 86400))
        except (OverflowError, ValueError) as exc:
            raise ValueError('Excel 日期超出有效范围。') from exc
    else:
        if text.lower() in MISSING_TIMES:
            return None
        text = text.replace('/', '-').replace('年', '-').replace('月', '-').replace('日', ' ')
        if not re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}[ T]+\d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?', text):
            raise ValueError('时间格式无效，请使用 YYYY-MM-DD HH:MM:SS（须包含日期）。')
        try:
            # strptime allows non-zero-padded dates, while ISO parsing handles explicit offsets.
            if re.search(r'Z|[+-]\d{2}:\d{2}$', text):
                value = datetime.fromisoformat(text.replace('Z', '+00:00'))
            else:
                text = re.sub(r'[ T]+', ' ', text)
                fmt = '%Y-%m-%d %H:%M:%S.%f' if '.' in text else '%Y-%m-%d %H:%M:%S' if text.count(':') == 2 else '%Y-%m-%d %H:%M'
                value = datetime.strptime(text, fmt)  # noqa: DTZ007 -- Preserve an unspecified source timezone; never invent UTC.
        except ValueError as exc:
            raise ValueError('时间不是有效日期或时刻，请核对原值。') from exc
    return value.isoformat(sep=' ')


def elapsed_seconds(start: str | None, end: str | None) -> int | None:
    if start is None or end is None:
        return None
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if (a.tzinfo is None) != (b.tzinfo is None):
        raise ValueError('开始与结束时间的时区不一致，不能混用带时区与不带时区的时间。')
    difference = (b - a).total_seconds()
    if difference < 0:
        raise ValueError('结束时间早于开始时间，不能计算负的答题时长。')
    return int(difference)


def format_duration(seconds) -> str:
    """Elapsed hours never wrap at 24; missing is distinct from a real zero."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
        return ''
    hours, remainder = divmod(int(seconds), 3600)
    minutes, second = divmod(remainder, 60)
    return f'{hours:02}:{minutes:02}:{second:02}'
