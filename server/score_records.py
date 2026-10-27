"""Local, inert source-row snapshots; legacy records never invent missing columns."""
from __future__ import annotations

import math

from .excel_reader import Cell, Sheet
from .table_export import column_name


def source_cell(cell: Cell) -> dict:
    value = cell.value
    if isinstance(value, float) and not math.isfinite(value):
        value = str(value)  # JSON cannot represent NaN/Infinity; do not disguise as empty.
    return {'value': value, 'text': cell.text(), 'number_format': cell.number_format,
            'formula': cell.formula, 'error': cell.error}


def snapshot_row(sheet: Sheet, header_row: int, row_index: int) -> dict:
    width = max(sheet.cols, sheet.column_counts.get(header_row - 1, 0), sheet.column_counts.get(row_index, 0))
    return {'version': 1, 'sheet_name': sheet.name, 'header_row': header_row,
            'row_number': row_index + 1, 'row_columns': sheet.column_counts.get(row_index),
            'columns': [dict(index=index, column=column_name(index + 1),
                             label=sheet.cell(header_row - 1, index).text(),
                             **source_cell(sheet.cell(row_index, index)))
                        for index in range(width)]}


def recorded_text(value) -> str:
    """Real zero/False are not missing values."""
    return '' if value is None else str(value)


def identity_fields(record: dict) -> dict:
    # Optional metadata is deliberately not an identity match key.
    return {'yiban_id': recorded_text(record.get('yiban_id')),
            'raw_yiban_id': recorded_text(record.get('raw_yiban_id'))}
