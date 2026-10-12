"""Dependency-free XLSX export: one worksheet per actual class, never per page."""
from __future__ import annotations

import io
import re
from collections import defaultdict
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

SHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
TYPE_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
HEADERS = ['学号', '姓名', '学院', '班级', '状态', '得分率', '得分']


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


def worksheet(rows):
    sheet = Element('worksheet', xmlns=SHEET_NS)
    views = SubElement(sheet, 'sheetViews')
    view = SubElement(views, 'sheetView', workbookViewId='0')
    SubElement(view, 'pane', ySplit='1', topLeftCell='A2', activePane='bottomLeft', state='frozen')
    cols = SubElement(sheet, 'cols')
    for i, width in enumerate([22, 18, 32, 30, 14, 14, 14], 1):
        SubElement(cols, 'col', min=str(i), max=str(i), width=str(width), customWidth='1')
    data = SubElement(sheet, 'sheetData')
    values: list[list[str | float | int | None]] = [list(HEADERS)]
    for student in rows:
        rate = student['score_rate']
        values.append([student['student_id'], student['name'], student['college_name'],
                       student['class_name'], student['status'],
                       None if rate is None else rate / 100, rate])
    for number, record in enumerate(values, 1):
        row = SubElement(data, 'row', r=str(number), ht='25', customHeight='1')
        for i, value in enumerate(record):
            if value is None:
                continue
            address = f'{chr(65+i)}{number}'
            style = '1' if number == 1 else ('2' if i == 5 else '0')
            cell = SubElement(row, 'c', r=address, s=style)
            if isinstance(value, (int, float)):
                SubElement(cell, 'v').text = str(value)
            else:
                # inlineStr cannot execute formulas, even when a name starts with '='.
                cell.set('t', 'inlineStr')
                text = SubElement(SubElement(cell, 'is'), 't')
                text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                text.text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))
    SubElement(sheet, 'autoFilter', ref=f'A1:G{len(values)}')
    return xml(sheet)


def build_score_workbook(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['class_id']].append(row)
    workbook = Element('workbook', xmlns=SHEET_NS, attrib={'xmlns:r': REL_NS})
    sheets = SubElement(workbook, 'sheets')
    relationships = Element('Relationships', xmlns=PACKAGE_NS)
    types = Element('Types', xmlns=TYPE_NS)
    SubElement(types, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    SubElement(types, 'Default', Extension='xml', ContentType='application/xml')
    SubElement(types, 'Override', PartName='/xl/workbook.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml')
    SubElement(types, 'Override', PartName='/xl/styles.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml')
    used = set()
    content = io.BytesIO()
    with ZipFile(content, 'w', ZIP_DEFLATED) as archive:
        for i, students in enumerate(groups.values(), 1):
            first = students[0]
            title = sheet_title(f"{first['class_name']}-{first['college_name']}", used)
            SubElement(sheets, 'sheet', name=title, sheetId=str(i), attrib={'r:id': f'rId{i}'})
            SubElement(relationships, 'Relationship', Id=f'rId{i}', Type=f'{REL_NS}/worksheet', Target=f'worksheets/sheet{i}.xml')
            SubElement(types, 'Override', PartName=f'/xl/worksheets/sheet{i}.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
            archive.writestr(f'xl/worksheets/sheet{i}.xml', worksheet(students))
        if not groups:
            raise ValueError('Cannot export an empty workbook')
        SubElement(relationships, 'Relationship', Id='styles', Type=f'{REL_NS}/styles', Target='styles.xml')
        root_rels = Element('Relationships', xmlns=PACKAGE_NS)
        SubElement(root_rels, 'Relationship', Id='workbook', Type=f'{REL_NS}/officeDocument', Target='xl/workbook.xml')
        archive.writestr('_rels/.rels', xml(root_rels))
        archive.writestr('[Content_Types].xml', xml(types))
        archive.writestr('xl/workbook.xml', xml(workbook))
        archive.writestr('xl/_rels/workbook.xml.rels', xml(relationships))
        archive.writestr('xl/styles.xml', f'''<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="{SHEET_NS}"><numFmts count="1"><numFmt numFmtId="164" formatCode="0.##%"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/><color rgb="FF273E5B"/></font><font><b/><sz val="11"/><name val="Microsoft YaHei"/><color rgb="FFFFFFFF"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF365DC5"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''')
    return content.getvalue()
