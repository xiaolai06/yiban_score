"""Roster mapping and validation. Student numbers are opaque text, never row IDs."""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from .excel_reader import Cell, Sheet
from .grade_rules import round_score

STATUSES = ['待录入', '未开始', '进行中', '已完成', '已录入', '缺考', '免考']
REQUIRED = ['student_id', 'name', 'college', 'class_name']
FIELDS = {
    'student_id': '学号', 'name': '姓名', 'gender': '性别',
    'college': '学院 / 院系', 'major': '专业', 'class_name': '班级',
    'status': '状态', 'score_rate': '得分率',
}
ALIASES = {
    'student_id': ['学号', '学生学号', '学生编号', 'studentid', 'studentno', 'studentnumber'],
    'name': ['姓名', '学生姓名', 'name', 'studentname'],
    'gender': ['性别', 'gender', 'sex'],
    'college': ['院系', '学院', '所属学院', '所属院系', '院系名称', '学院名称', '二级学院', 'college', 'department'],
    'major': ['专业', '专业名称', '所属专业', 'major'],
    'class_name': ['班级', '行政班', '行政班级', '班级名称', '所属班级', 'class', 'classname'],
    'status': ['状态', '登记状态', '成绩状态', 'status'],
    'score_rate': ['得分率', '得分率(%)', '得分率%', '得分率（%）', 'scorerate'],
}

def header_key(value: str) -> str:
    return re.sub(r'[\s_\-\ufeff\u200b]', '', unicodedata.normalize('NFKC', value)).lower()

LOOKUP = {header_key(alias): key for key, names in ALIASES.items() for alias in names}

def clean(value: str) -> str:
    return value.strip().strip('\ufeff\u200b').strip()

def guess_header(sheet: Sheet) -> int:
    best, best_score = 0, -1
    for r in range(min(30, sheet.rows)):
        keys = {LOOKUP.get(header_key(t)) for t in sheet.texts(r) if t}
        score = len(keys.intersection(REQUIRED)) * 100 + len(keys - {None}) * 5
        if score > best_score:
            best, best_score = r, score
    return best + 1

def sheet_info(sheet: Sheet, index: int, header_row: int | None = None) -> dict:
    header_row = header_row or guess_header(sheet)
    if not 1 <= header_row <= max(1, sheet.rows):
        raise ValueError('表头行号超出工作表范围。')
    labels = sheet.texts(header_row - 1)
    mapping = {}
    for i, text in enumerate(labels):
        key = LOOKUP.get(header_key(text))
        if key and key not in mapping:
            mapping[key] = i
    return {'index': index, 'name': sheet.name, 'hidden': sheet.hidden,
            'rows': max(0, sheet.rows - header_row), 'header_row': header_row,
            'headers': [{'index': i, 'label': text or f'第 {i + 1} 列（无表头）'} for i, text in enumerate(labels)],
            'mapping': mapping, 'recognized_required': sum(k in mapping for k in REQUIRED)}

def identifier(cell: Cell) -> str:
    if cell.error or cell.formula:
        raise ValueError('学号不能是公式或错误值，请粘贴为文本值。')
    if isinstance(cell.value, bool):
        # Cell is a valid argument; this is a user-facing invalid field value.
        raise ValueError('学号不能是布尔值。')  # noqa: TRY004 -- preserve row-validation ValueError contract.
    if isinstance(cell.value, (int, float)):
        if not math.isfinite(cell.value) or not float(cell.value).is_integer():
            raise ValueError('数字型学号必须是整数；建议将学号列设为文本。')
        if len(str(abs(int(cell.value)))) > 15:
            raise ValueError('数字型学号超过 15 位，原文件可能已丢失精度；请核对后改为文本。')
    text = clean(cell.text())
    if not text:
        raise ValueError('学号不能为空。')
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+', text):
        raise ValueError('学号不能使用科学计数法文本，请提供完整学号。')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', text):
        raise ValueError('学号须为 1–64 位字母、数字、点、下划线或短横线，不可含空格。')
    return text

def rate_value(cell: Cell) -> float | None:
    if cell.error or cell.formula or isinstance(cell.value, bool):
        raise ValueError('得分率不能是公式、错误或布尔值。')
    if isinstance(cell.value, (int, float)) and not math.isfinite(cell.value):
        raise ValueError('得分率必须是有限数值。')
    text = clean(cell.text())
    if not text:
        return None
    percent_text = text.endswith(('%', '％'))
    text = text.rstrip('%％').strip()
    try:
        number = float(text)
    except (ValueError, TypeError):
        raise ValueError('得分率须为 0–100 的数值或如 85.5% 的文本。') from None
    if isinstance(cell.value, (int, float)) and '%' in cell.number_format and not percent_text:
        number *= 100
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError('得分率必须在 0–100% 之间。')
    return round_score(number)

def validate_rows(sheet: Sheet, header_row: int, mapping: dict[str, int]) -> dict:
    if not 1 <= header_row <= sheet.rows:
        raise ValueError('表头行号无效。')
    for key in REQUIRED:
        if key not in mapping:
            raise ValueError(f'请映射必填字段：{FIELDS[key]}。')
    if any(k not in FIELDS for k in mapping):
        raise ValueError('字段映射中存在不支持的字段。')
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('同一 Excel 列不能映射到多个字段。')
    if any(isinstance(c, bool) or not isinstance(c, int) or c < 0 or c >= sheet.cols for c in mapping.values()):
        raise ValueError('列映射超出范围。')
    rows, errors, warnings, seen = [], [], [], {}
    total, blank_rows, repeated_headers = 0, 0, 0
    for r in range(header_row, sheet.rows):
        if not sheet.has_content(r):
            blank_rows += 1
            continue
        if all(LOOKUP.get(header_key(sheet.cell(r, mapping[k]).text())) == k for k in REQUIRED):
            repeated_headers += 1
            continue
        total += 1
        obj: dict[str, Any] = {k: clean(sheet.cell(r, c).text()) for k, c in mapping.items()}
        row_errors = []
        try:
            obj['student_id'] = identifier(sheet.cell(r, mapping['student_id']))
        except ValueError as e:
            row_errors.append(str(e))
        for key, limit in [('name', 80), ('college', 120), ('class_name', 120)]:
            if not obj.get(key):
                row_errors.append(f'{FIELDS[key]}不能为空。')
            elif len(obj[key]) > limit:
                row_errors.append(f'{FIELDS[key]}超过 {limit} 个字符。')
            if sheet.cell(r, mapping[key]).error:
                row_errors.append(f'{FIELDS[key]}包含 Excel 错误值。')
        for key, limit in [('gender', 30), ('major', 160)]:
            obj.setdefault(key, '')
            if len(obj[key]) > limit:
                row_errors.append(f'{FIELDS[key]}过长。')
        obj['status'] = obj.get('status') or '待录入'
        if obj['status'] not in STATUSES:
            row_errors.append('状态须为：' + '、'.join(STATUSES) + '。')
        try:
            obj['score_rate'] = rate_value(sheet.cell(r, mapping['score_rate'])) if 'score_rate' in mapping else None
        except ValueError as e:
            obj['score_rate'] = None
            row_errors.append(str(e))
        sid = obj.get('student_id', '')
        if sid and sid in seen:
            row_errors.append(f'学号在本文件内重复；首次出现在第 {seen[sid]} 行。')
        elif sid:
            seen[sid] = r + 1
        obj['row_number'] = r + 1
        if row_errors:
            errors.append({'row_number': r + 1, 'student_id': sid, 'message': ' '.join(row_errors)})
        else:
            rows.append(obj)
    if blank_rows:
        warnings.append(f'跳过 {blank_rows} 个全空行。')
    if repeated_headers:
        warnings.append(f'跳过 {repeated_headers} 行重复表头。')
    if 'status' not in mapping:
        warnings.append('新增学生的状态初始化为“待录入”；这不是对实际考试或学籍状态的判断。')
    if 'score_rate' not in mapping:
        warnings.append('新增学生的得分率留空，显示为“未录入”，不当作 0 分。')
    if any(isinstance(sheet.cell(r, mapping['student_id']).value, (int, float)) for r in range(header_row, sheet.rows)):
        warnings.append('检测到数字型学号：可识别简单补零格式，但无法恢复 Excel 已经丢失的前导零或精度。请核对预览。')
    warnings.append('重复导入不会删除名单以外的学生；已有学生的状态和得分率始终保留。')
    if not rows and not errors:
        errors.append({'row_number': header_row, 'student_id': '', 'message': '表头之后没有可导入的学生。'})
    return {'rows': rows, 'errors': errors, 'warnings': warnings,
            'source_rows': total, 'blank_rows': blank_rows, 'repeated_headers': repeated_headers}
