"""One qualification rule everywhere; examination progress is a separate concern."""
from __future__ import annotations

import math

NUMERIC_SCORE_SQL = "(typeof(s.score_rate) IN ('integer','real') AND s.score_rate BETWEEN 0 AND 100)"
QUALIFIED_SQL = "(s.score_rate IS NOT NULL AND typeof(s.score_rate) IN ('integer','real') AND s.score_rate BETWEEN 90 AND 100)"
UNQUALIFIED_SQL = f'(NOT {QUALIFIED_SQL})'
# Settlement/average still ignores unfinished provisional scores, retaining real zero.
VALID_SCORE_SQL = f"({NUMERIC_SCORE_SQL} AND s.status NOT IN ('未开始','进行中'))"


def has_numeric_score(rate) -> bool:
    return isinstance(rate, (int, float)) and not isinstance(rate, bool) and math.isfinite(rate) and 0 <= rate <= 100


def is_qualified(rate) -> bool:
    return has_numeric_score(rate) and rate >= 90
