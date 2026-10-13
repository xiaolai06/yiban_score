"""Local XLSX styles: centered cells, YaHei Chinese, Times New Roman digits."""
from __future__ import annotations

import re
from xml.etree.ElementTree import Element, SubElement, tostring

SHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
TEXT_STYLE = 0
HEADER_STYLE = 1
PERCENT_STYLE = 2
NUMBER_STYLE = 3
SUMMARY_PERCENT_STYLE = 4
CHINESE_FONT = 'Microsoft YaHei'
NUMBER_FONT = 'Times New Roman'


def styles_xml() -> bytes:
    root = Element('styleSheet', xmlns=SHEET_NS)
    formats = SubElement(root, 'numFmts', count='2')
    SubElement(formats, 'numFmt', numFmtId='164', formatCode='0.##%')
    SubElement(formats, 'numFmt', numFmtId='165', formatCode='0.00%')
    fonts = SubElement(root, 'fonts', count='3')
    for name, bold, color in [(CHINESE_FONT, False, 'FF273E5B'),
                              (CHINESE_FONT, True, 'FFFFFFFF'),
                              (NUMBER_FONT, False, 'FF273E5B')]:
        font = SubElement(fonts, 'font')
        if bold:
            SubElement(font, 'b')
        SubElement(font, 'sz', val='11')
        SubElement(font, 'color', rgb=color)
        SubElement(font, 'name', val=name)
    fills = SubElement(root, 'fills', count='3')
    for pattern in ('none', 'gray125', 'solid'):
        fill = SubElement(SubElement(fills, 'fill'), 'patternFill', patternType=pattern)
        if pattern == 'solid':
            SubElement(fill, 'fgColor', rgb='FF365DC5')
            SubElement(fill, 'bgColor', indexed='64')
    SubElement(SubElement(root, 'borders', count='1'), 'border')
    SubElement(SubElement(root, 'cellStyleXfs', count='1'), 'xf',
               numFmtId='0', fontId='0', fillId='0', borderId='0')
    xfs = SubElement(root, 'cellXfs', count='5')
    for format_id, font_id, fill_id in [('0', '0', '0'), ('0', '1', '2'),
                                        ('164', '2', '0'), ('0', '2', '0'), ('165', '2', '0')]:
        xf = SubElement(xfs, 'xf', numFmtId=format_id, fontId=font_id, fillId=fill_id,
                        borderId='0', xfId='0', applyFont='1', applyFill='1',
                        applyAlignment='1', applyNumberFormat='1')
        SubElement(xf, 'alignment', horizontal='center', vertical='center', wrapText='1')
    SubElement(SubElement(root, 'cellStyles', count='1'), 'cellStyle', name='Normal', xfId='0', builtinId='0')
    return tostring(root, encoding='utf-8', xml_declaration=True)


def inline_text(cell, value, *, header=False) -> None:
    """Keep identifiers as text, applying digit fonts even inside Chinese labels.

    Literal inline strings never become Excel formulas. No font downloads or
    embedding: the viewer must have the named fonts installed (otherwise fallback).
    """
    literal = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))
    if len(literal.encode('utf-16-le')) > 65_534:
        raise ValueError('汇总文本超过 Excel 单元格长度上限，请改导出 CSV 或出现明细；原始记录未截断。')
    cell.set('t', 'inlineStr')
    node = SubElement(cell, 'is')
    digit_pattern = r'\d+(?:[.,．，]\d+)*[%％]?'
    parts = re.split(f'({digit_pattern})', literal)
    if not any(re.fullmatch(digit_pattern, part) for part in parts):
        text = SubElement(node, 't')
        text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        text.text = literal
        return
    for part in parts:
        if not part:
            continue
        run = SubElement(node, 'r')
        properties = SubElement(run, 'rPr')
        SubElement(properties, 'rFont', val=NUMBER_FONT if re.fullmatch(digit_pattern, part) else CHINESE_FONT)
        if header:
            SubElement(properties, 'b')
        SubElement(properties, 'sz', val='11')
        SubElement(properties, 'color', rgb='FFFFFFFF' if header else 'FF273E5B')
        text = SubElement(run, 't')
        text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        text.text = part
