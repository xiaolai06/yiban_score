"""One qualification rule everywhere; examination progress is a separate concern."""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

NUMERIC_SCORE_SQL = "(typeof(s.score_rate) IN ('integer','real') AND s.score_rate BETWEEN 0 AND 100)"
QUALIFIED_SQL = "(s.score_rate IS NOT NULL AND typeof(s.score_rate) IN ('integer','real') AND s.score_rate BETWEEN 90 AND 100)"
UNQUALIFIED_SQL = f'(NOT {QUALIFIED_SQL})'
# Settlement/average still ignores unfinished provisional scores, retaining real zero.
VALID_SCORE_SQL = f"({NUMERIC_SCORE_SQL} AND s.status NOT IN ('未开始','进行中'))"


def has_numeric_score(rate) -> bool:
    return isinstance(rate, (int, float)) and not isinstance(rate, bool) and 0 <= rate <= 100 and math.isfinite(rate)


def is_qualified(rate) -> bool:
    return has_numeric_score(rate) and rate >= 90


def round_score(value) -> float:
    """Canonical two-decimal input rounding; never re-round persisted exports."""
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not 0 <= number <= 100:
            raise ValueError('得分率必须在0–100之间。')
        return float(number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('得分率精度异常，请核对原值。') from exc
