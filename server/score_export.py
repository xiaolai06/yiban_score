"""Dependency-free XLSX export: class sheets followed by an export summary."""
from __future__ import annotations

import io
import re
from collections import defaultdict
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

from .grade_rules import has_numeric_score, is_qualified
from .xlsx_styles import (
    HEADER_STYLE,
    NUMBER_STYLE,
    PERCENT_STYLE,
    SUMMARY_PERCENT_STYLE,
    TEXT_STYLE,
    inline_text,
    styles_xml,
)

SHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
TYPE_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
HEADERS = ['学号', '姓名', '学院', '班级', '状态', '得分率', '得分']
SUMMARY_TITLE = '导出说明'
SUMMARY_HEADERS = ['统计范围', '学院', '班级', '导出人数', '参与人数', '未参与人数',
                   '参与率', '合格人数（≥90）', '合格率（占参与人数）', '合格占导出人数比例']
SUMMARY_NOTES = [
    ('统计范围', '只统计本文件实际导出的学生，不是全库、整校或当前页面人数；各班级行也仅统计本次导出的人员。'),
    ('参与人数', '得分率为大于0且不超过100的有效数值即算参与。空值、0或无有效数值得分率均算未参与，不把空值改成0。'),
    ('参与率', '参与人数 ÷ 导出人数；未参与人数 = 导出人数 − 参与人数。'),
    ('合格人数', '得分率达到90（含90），即90≤得分率≤100的有效数值，计入合格人数。'),
    ('合格率', '合格人数 ÷ 参与人数；参与人数为0时显示“—”，表示没有可计算的分母，不表示0%合格率。'),
    ('合格占导出人数比例', '合格人数 ÷ 导出人数，与“合格率（占参与人数）”使用不同分母。'),
    ('状态与网站口径', '全站与本表统一按有效数值得分率≥90（含90）判断合格，不因考试进度排除高分。未开始/进行中另列待跟进；已结算成绩及平均值仍排除未完成暂存分值。'),
    ('显示与样式', '中文使用微软雅黑，数字使用Times New Roman，全部单元格水平/垂直居中。比例以百分比保留2位小数，汇总按人数相加后计算，不平均班级比例。字体未安装时由Excel/WPS替代。'),
]


def xml(element):
    return tostring(element, encoding='utf-8', xml_declaration=True)


def sheet_title(name, used):
    base = re.sub(r'[\[\]:*?/\\\x00-\x1f]', ' ', name).strip().strip("'") or '班级'
    title = base[:31]
    suffix = 1
    while title.casefold() in used:
        suffix += 1
        tail = f' ({suffix})'
        title = base[:31-len(tail)] + tail
    used.add(title.casefold())
    return title


def export_statistics(rows):
    total = participated = qualified = 0
    for student in rows:
        total += 1
        rate = student['score_rate']
        valid = has_numeric_score(rate)
        participated += int(valid and rate > 0)
        qualified += int(is_qualified(rate))
    return {'total': total, 'participated': participated, 'not_participated': total - participated,
            'participation_rate': participated / total if total else None,
            'qualified': qualified, 'qualification_rate': qualified / participated if participated else None,
            'qualified_total_rate': qualified / total if total else None}


def base_sheet(widths, column_styles):
    sheet = Element('worksheet', xmlns=SHEET_NS)
    view = SubElement(SubElement(sheet, 'sheetViews'), 'sheetView', workbookViewId='0')
    SubElement(view, 'pane', ySplit='1', topLeftCell='A2', activePane='bottomLeft', state='frozen')
    SubElement(sheet, 'sheetFormatPr', defaultRowHeight='25')
    cols = SubElement(sheet, 'cols')
    for i, (width, style) in enumerate(zip(widths, column_styles, strict=True), 1):
        # Empty cells inherit the same centered alignment as filled cells.
        SubElement(cols, 'col', min=str(i), max=str(i), width=str(width), customWidth='1', style=str(style))
    return sheet


def write_row(data, number, record, column_styles, *, header=False, height=None):
    row = SubElement(data, 'row', r=str(number))
    if height is not None:
        row.set('ht', str(height))
        row.set('customHeight', '1')
    for i, value in enumerate(record):
        style = HEADER_STYLE if header else column_styles[i]
        cell = SubElement(row, 'c', r=f'{chr(65+i)}{number}', s=str(style))
        if value is None:
            continue  # Styled blank cell, still genuinely empty rather than a zero.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            SubElement(cell, 'v').text = str(value)
        else:
            inline_text(cell, value, header=header)
    return row


def worksheet(rows):
    column_styles = [TEXT_STYLE] * 5 + [PERCENT_STYLE, NUMBER_STYLE]
    sheet = base_sheet([22, 18, 32, 30, 14, 14, 14], column_styles)
    data = SubElement(sheet, 'sheetData')
    write_row(data, 1, HEADERS, column_styles, header=True, height=30)
    for number, student in enumerate(rows, 2):
        rate = student['score_rate']
        values = [student['student_id'], student['name'], student['college_name'],
                  student['class_name'], student['status'], None if rate is None else rate / 100, rate]
        write_row(data, number, values, column_styles)
    SubElement(sheet, 'autoFilter', ref=f'A1:G{len(rows)+1}')
    return xml(sheet)


def summary_worksheet(groups):
    column_styles = [TEXT_STYLE] * 3 + [NUMBER_STYLE] * 3 + [SUMMARY_PERCENT_STYLE, NUMBER_STYLE,
                                                                     SUMMARY_PERCENT_STYLE, SUMMARY_PERCENT_STYLE]
    sheet = base_sheet([24, 32, 32, 14, 14, 14, 16, 20, 25, 27], column_styles)
    data = SubElement(sheet, 'sheetData')
    write_row(data, 1, SUMMARY_HEADERS, column_styles, header=True, height=40)
    all_rows = [student for students in groups.values() for student in students]
    scopes = [('本次导出合计', '—', '—', all_rows)]
    scopes.extend(('班级', students[0]['college_name'], students[0]['class_name'], students)
                  for students in groups.values())
    for number, (scope, college, class_name, students) in enumerate(scopes, 2):
        counts = export_statistics(students)
        values = [scope, college, class_name, counts['total'], counts['participated'], counts['not_participated'],
                  counts['participation_rate'], counts['qualified'],
                  counts['qualification_rate'] if counts['qualification_rate'] is not None else '—',
                  counts['qualified_total_rate']]
        write_row(data, number, values, column_styles)
    last_stat_row = len(scopes) + 1
    for number, (label, note) in enumerate(SUMMARY_NOTES, last_stat_row + 2):
        write_row(data, number, [label, note], [TEXT_STYLE, TEXT_STYLE], height=45)
    SubElement(sheet, 'autoFilter', ref=f'A1:J{last_stat_row}')
    merges = SubElement(sheet, 'mergeCells', count=str(len(SUMMARY_NOTES)))
    for number in range(last_stat_row + 2, last_stat_row + 2 + len(SUMMARY_NOTES)):
        SubElement(merges, 'mergeCell', ref=f'B{number}:J{number}')
    return xml(sheet)


def build_score_workbook(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['class_id']].append(row)
    if not groups:
        raise ValueError('Cannot export an empty workbook')
    workbook = Element('workbook', xmlns=SHEET_NS, attrib={'xmlns:r': REL_NS})
    sheets = SubElement(workbook, 'sheets')
    relationships = Element('Relationships', xmlns=PACKAGE_NS)
    types = Element('Types', xmlns=TYPE_NS)
    SubElement(types, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    SubElement(types, 'Default', Extension='xml', ContentType='application/xml')
    SubElement(types, 'Override', PartName='/xl/workbook.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml')
    SubElement(types, 'Override', PartName='/xl/styles.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml')
    used = {SUMMARY_TITLE.casefold()}
    content = io.BytesIO()
    with ZipFile(content, 'w', ZIP_DEFLATED) as archive:
        pages = []
        for students in groups.values():
            first = students[0]
            title = sheet_title(f"{first['class_name']}-{first['college_name']}", used)
            pages.append((title, worksheet(students)))
        pages.append((SUMMARY_TITLE, summary_worksheet(groups)))
        for i, (title, sheet_content) in enumerate(pages, 1):
            SubElement(sheets, 'sheet', name=title, sheetId=str(i), attrib={'r:id': f'rId{i}'})
            SubElement(relationships, 'Relationship', Id=f'rId{i}', Type=f'{REL_NS}/worksheet', Target=f'worksheets/sheet{i}.xml')
            SubElement(types, 'Override', PartName=f'/xl/worksheets/sheet{i}.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
            archive.writestr(f'xl/worksheets/sheet{i}.xml', sheet_content)
        SubElement(relationships, 'Relationship', Id='styles', Type=f'{REL_NS}/styles', Target='styles.xml')
        root_rels = Element('Relationships', xmlns=PACKAGE_NS)
        SubElement(root_rels, 'Relationship', Id='workbook', Type=f'{REL_NS}/officeDocument', Target='xl/workbook.xml')
        archive.writestr('_rels/.rels', xml(root_rels))
        archive.writestr('[Content_Types].xml', xml(types))
        archive.writestr('xl/workbook.xml', xml(workbook))
        archive.writestr('xl/_rels/workbook.xml.rels', xml(relationships))
        archive.writestr('xl/styles.xml', styles_xml())
    return content.getvalue()
