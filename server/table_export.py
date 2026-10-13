"""Small dependency-free XLSX table exporter with literal, non-formula text cells."""
from __future__ import annotations

import io
import math
from xml.etree.ElementTree import Element, SubElement
from zipfile import ZIP_DEFLATED, ZipFile

from .score_export import PACKAGE_NS, REL_NS, SHEET_NS, TYPE_NS, xml
from .xlsx_styles import HEADER_STYLE, NUMBER_STYLE, TEXT_STYLE, inline_text, styles_xml


def column_name(index: int) -> str:
    name = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def build_table_workbook(headers: list[str], records: list[list], title: str) -> bytes:
    if len(records) > 1_048_575:
        raise ValueError('记录超过 Excel 行数上限，请缩小筛选或导出 CSV。')
    sheet = Element('worksheet', xmlns=SHEET_NS)
    view = SubElement(SubElement(sheet, 'sheetViews'), 'sheetView', workbookViewId='0')
    SubElement(view, 'pane', ySplit='1', topLeftCell='A2', activePane='bottomLeft', state='frozen')
    SubElement(sheet, 'sheetFormatPr', defaultRowHeight='25')
    columns = SubElement(sheet, 'cols')
    for index in range(1, len(headers) + 1):
        SubElement(columns, 'col', min=str(index), max=str(index), width='24', customWidth='1', style=str(TEXT_STYLE))
    data = SubElement(sheet, 'sheetData')
    for number, values in enumerate([headers, *records], 1):
        row = SubElement(data, 'row', r=str(number))
        for index, value in enumerate(values, 1):
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            style = HEADER_STYLE if number == 1 else NUMBER_STYLE if numeric else TEXT_STYLE
            cell = SubElement(row, 'c', r=f'{column_name(index)}{number}', s=str(style))
            if value is None:
                continue  # Preserve an empty value while centering the whole table.
            if numeric:
                SubElement(cell, 'v').text = str(value)
            else:
                inline_text(cell, value, header=number == 1)
    SubElement(sheet, 'autoFilter', ref=f'A1:{column_name(len(headers))}{len(records) + 1}')
    workbook = Element('workbook', xmlns=SHEET_NS, attrib={'xmlns:r': REL_NS})
    SubElement(SubElement(workbook, 'sheets'), 'sheet', name=title[:31], sheetId='1', attrib={'r:id': 'rId1'})
    relationships = Element('Relationships', xmlns=PACKAGE_NS)
    SubElement(relationships, 'Relationship', Id='rId1', Type=f'{REL_NS}/worksheet', Target='worksheets/sheet1.xml')
    SubElement(relationships, 'Relationship', Id='styles', Type=f'{REL_NS}/styles', Target='styles.xml')
    root = Element('Relationships', xmlns=PACKAGE_NS)
    SubElement(root, 'Relationship', Id='workbook', Type=f'{REL_NS}/officeDocument', Target='xl/workbook.xml')
    types = Element('Types', xmlns=TYPE_NS)
    SubElement(types, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    SubElement(types, 'Default', Extension='xml', ContentType='application/xml')
    SubElement(types, 'Override', PartName='/xl/workbook.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml')
    SubElement(types, 'Override', PartName='/xl/worksheets/sheet1.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
    SubElement(types, 'Override', PartName='/xl/styles.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml')
    content = io.BytesIO()
    with ZipFile(content, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('_rels/.rels', xml(root))
        archive.writestr('[Content_Types].xml', xml(types))
        archive.writestr('xl/workbook.xml', xml(workbook))
        archive.writestr('xl/_rels/workbook.xml.rels', xml(relationships))
        archive.writestr('xl/worksheets/sheet1.xml', xml(sheet))
        archive.writestr('xl/styles.xml', styles_xml())
    return content.getvalue()
