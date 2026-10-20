"""Read-only Excel adapters. No office process, macros, or external links are executed.

The bundled readers cover ordinary BIFF8 (.xls) and OOXML (.xlsx) roster tables.
They deliberately reject encrypted/legacy BIFF files, damaged streams, oversized
workbooks and unsupported structures rather than guessing students' identifiers.
"""
from __future__ import annotations

import io
import math
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

MAX_ROWS = 60000
MAX_COLS = 160
MAX_CELLS = 1_500_000
MAX_EXPANDED = 100 * 1024 * 1024

class WorkbookError(ValueError):
    pass

@dataclass
class Cell:
    value: str | float | int | bool | None
    number_format: str = ''
    formula: bool = False
    error: bool = False

    def text(self) -> str:
        v = self.value
        if v is None:
            return ''
        if isinstance(v, bool):
            return 'TRUE' if v else 'FALSE'
        if isinstance(v, (int, float)):
            if not math.isfinite(v):
                return ''
            # Honor simple leading-zero identifier formats such as 0000000000.
            fmt = self.number_format.split(';')[0].strip()
            if float(v).is_integer():
                raw = str(int(v))
                return raw.zfill(len(fmt)) if re.fullmatch(r'0{2,64}', fmt) and v >= 0 else raw
            return format(v, '.15g')
        return str(v).strip()

@dataclass
class Sheet:
    name: str
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)
    merges: list[tuple[int, int, int, int]] = field(default_factory=list)
    hidden: bool = False
    rows: int = 0
    cols: int = 0
    column_counts: dict[int, int] = field(default_factory=dict)  # CSV structural validation
    date_1904: bool = False  # Excel serial-date epoch, used only by explicit time mappings

    def put(self, row: int, col: int, cell: Cell, *, replace: bool = False) -> None:
        if (cell.value is None or cell.value == '') and not cell.formula and not cell.error:
            return
        if row < 0 or col < 0 or row >= MAX_ROWS or col >= MAX_COLS:
            raise WorkbookError(f'工作表“{self.name}”超出限制：最多 {MAX_ROWS} 行、{MAX_COLS} 列。')
        position = (row, col)
        if position in self.cells and not replace:
            raise WorkbookError('工作表存在重复单元格坐标，无法可靠绑定学号。')
        if position not in self.cells and len(self.cells) >= MAX_CELLS:
            raise WorkbookError('工作表有效单元格过多。')
        self.cells[position] = cell
        self.rows = max(self.rows, row + 1)
        self.cols = max(self.cols, col + 1)

    def finish(self) -> None:
        # Only actual merged ranges are propagated; ordinary missing data stay missing.
        for r0, r1, c0, c1 in self.merges:
            if r1 >= MAX_ROWS or c1 >= MAX_COLS or r1 < r0 or c1 < c0:
                raise WorkbookError('合并单元格范围不受支持。')
            anchor = self.cells.get((r0, c0))
            if anchor is not None:
                for r in range(r0, r1 + 1):
                    for c in range(c0, c1 + 1):
                        if (r, c) not in self.cells:
                            self.put(r, c, anchor)

    def cell(self, row: int, col: int) -> Cell:
        return self.cells.get((row, col), Cell(None))

    def has_content(self, row: int) -> bool:
        return any(cell.text() or cell.formula or cell.error or
                   isinstance(cell.value, (int, float)) and not math.isfinite(cell.value)
                   for cell in (self.cell(row, col) for col in range(self.cols)))

    def texts(self, row: int) -> list[str]:
        return [self.cell(row, c).text() for c in range(self.cols)]

class CompoundFile:
    """Bounded CFB stream reader, including FAT, DIFAT and mini-FAT chains."""
    FREE, END = 0xFFFFFFFF, 0xFFFFFFFE

    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != bytes.fromhex('D0CF11E0A1B11AE1'):
            raise WorkbookError('这不是有效的 Excel .xls 文件。')
        self.data = data
        self.sector_size = 1 << struct.unpack_from('<H', data, 30)[0]
        self.mini_size = 1 << struct.unpack_from('<H', data, 32)[0]
        if self.sector_size not in (512, 4096) or self.mini_size != 64:
            raise WorkbookError('不支持此复合文件的扇区结构。')
        self.nsectors = len(data) // self.sector_size - 1
        self.cutoff = struct.unpack_from('<I', data, 56)[0]
        fat_count, directory = struct.unpack_from('<II', data, 44)
        mini_start, mini_count, difat_start, difat_count = struct.unpack_from('<IIII', data, 60)
        if fat_count > self.nsectors or difat_count > self.nsectors:
            raise WorkbookError('文件分配表损坏。')
        fat_ids = [v for v in struct.unpack_from('<109I', data, 76) if v < self.END]
        sid, visited = difat_start, set()
        for _ in range(difat_count):
            if sid in visited:
                raise WorkbookError('DIFAT 存在循环。')
            visited.add(sid)
            entries = struct.unpack('<' + 'I' * (self.sector_size // 4), self.sector(sid))
            fat_ids.extend(v for v in entries[:-1] if v < self.END)
            sid = entries[-1]
        if len(fat_ids) < fat_count:
            raise WorkbookError('文件分配表不完整。')
        self.fat = []
        for sid in fat_ids[:fat_count]:
            self.fat.extend(struct.unpack('<' + 'I' * (self.sector_size // 4), self.sector(sid)))
        directory_data = self.chain(directory, self.fat)
        self.entries = []
        for pos in range(0, len(directory_data) - 127, 128):
            item = directory_data[pos:pos + 128]
            length = struct.unpack_from('<H', item, 64)[0]
            if not 2 <= length <= 64 or item[66] == 0:
                continue
            name = item[:length - 2].decode('utf-16le', errors='strict')
            self.entries.append((name, item[66], struct.unpack_from('<I', item, 116)[0], struct.unpack_from('<Q', item, 120)[0]))
        self.mini_fat = []
        self.mini_stream = b''
        if mini_count:
            raw = self.chain(mini_start, self.fat, mini_count * self.sector_size)
            self.mini_fat = list(struct.unpack('<' + 'I' * (len(raw) // 4), raw))
            root = next((e for e in self.entries if e[1] == 5), None)
            if root:
                self.mini_stream = self.chain(root[2], self.fat, root[3])

    def sector(self, sid: int) -> bytes:
        if not 0 <= sid < self.nsectors:
            raise WorkbookError('文件引用了无效扇区。')
        start = (sid + 1) * self.sector_size
        return self.data[start:start + self.sector_size]

    def chain(self, sid: int, fat: list[int], size: int | None = None, mini: bool = False) -> bytes:
        if size is not None and (size < 0 or size > MAX_EXPANDED):
            raise WorkbookError('文件流超过读取限制。')
        parts, seen = [], set()
        block_size = self.mini_size if mini else self.sector_size
        while sid not in (self.END, self.FREE):
            if sid in seen or sid >= len(fat):
                raise WorkbookError('文件扇区链损坏。')
            seen.add(sid)
            if mini:
                block = self.mini_stream[sid * block_size:(sid + 1) * block_size]
                if len(block) != block_size:
                    raise WorkbookError('文件小扇区链不完整。')
            else:
                block = self.sector(sid)
            parts.append(block)
            if len(parts) * block_size > MAX_EXPANDED:
                raise WorkbookError('文件流超过读取限制。')
            sid = fat[sid]
        raw = b''.join(parts)
        if size is not None and len(raw) < size:
            raise WorkbookError('文件流被截断。')
        return raw if size is None else raw[:size]

    def workbook(self) -> bytes:
        for name, typ, sid, size in self.entries:
            if typ == 2 and name.lower() in ('workbook', 'book'):
                return self.chain(sid, self.mini_fat if size < self.cutoff else self.fat, size, mini=size < self.cutoff)
        raise WorkbookError('找不到 Workbook 数据流；请用 Excel/WPS 另存为标准 .xlsx。')

class StringChunks:
    """BIFF8 Unicode string reader with CONTINUE encoding switches."""
    def __init__(self, chunks: list[bytes]):
        self.chunks, self.i, self.pos = chunks, 0, 0

    def remaining(self) -> int:
        return len(self.chunks[self.i]) - self.pos if self.i < len(self.chunks) else 0

    def advance(self) -> None:
        self.i += 1
        self.pos = 0
        if self.i >= len(self.chunks):
            raise WorkbookError('Excel 字符串数据被截断。')

    def read(self, n: int) -> bytes:
        if n < 0 or n > MAX_EXPANDED:
            raise WorkbookError('Excel 字符串长度无效。')
        out = []
        while n:
            if self.remaining() == 0:
                self.advance()
            take = min(n, self.remaining())
            out.append(self.chunks[self.i][self.pos:self.pos + take])
            self.pos += take
            n -= take
        return b''.join(out)

    def u16(self) -> int:
        return struct.unpack('<H', self.read(2))[0]

    def unicode(self, short: bool = False) -> str:
        chars = self.read(1)[0] if short else self.u16()
        flags = self.read(1)[0]
        runs = self.u16() if flags & 8 else 0
        ext = struct.unpack('<I', self.read(4))[0] if flags & 4 else 0
        wide = bool(flags & 1)
        out = []
        while chars:
            if self.remaining() == 0:
                self.advance()
                wide = bool(self.read(1)[0] & 1)
            width = 2 if wide else 1
            take = min(chars, self.remaining() // width)
            if take == 0:
                raise WorkbookError('Excel Unicode 字符边界异常。')
            raw = self.read(take * width)
            out.append(raw if wide else b''.join(bytes((v, 0)) for v in raw))
            chars -= take
        self.read(runs * 4 + ext)
        return b''.join(out).decode('utf-16le', errors='strict')

def records(data: bytes, start: int = 0):
    pos = start
    while pos + 4 <= len(data):
        rid, length = struct.unpack_from('<HH', data, pos)
        end = pos + 4 + length
        if end > len(data):
            raise WorkbookError('Excel BIFF 记录被截断。')
        yield rid, data[pos + 4:end], pos
        pos = end

def rk_number(raw: bytes) -> float | int:
    bits = struct.unpack('<I', raw)[0]
    if bits & 2:
        value = struct.unpack('<i', raw)[0] >> 2
    else:
        value = struct.unpack('<d', b'\0\0\0\0' + struct.pack('<I', bits & 0xFFFFFFFC))[0]
    return value / 100 if bits & 1 else value

def read_xls(data: bytes) -> list[Sheet]:
    stream = CompoundFile(data).workbook()
    globals_ = []
    for record in records(stream):
        globals_.append(record)
        if record[0] == 0x000A:
            break
    if not globals_ or globals_[0][0] != 0x0809 or struct.unpack_from('<H', globals_[0][1])[0] != 0x0600:
        raise WorkbookError('仅支持 Excel 97–2003 BIFF8 .xls；请另存为 .xlsx。')
    sst, bounds, formats, xfs = [], [], {9: '0%', 10: '0.00%', 14: 'yyyy-mm-dd', 22: 'yyyy-mm-dd hh:mm'}, []
    date_1904 = False
    for i, (rid, payload, _) in enumerate(globals_):
        if rid == 0x002F:
            raise WorkbookError('不支持加密工作簿，请解除文件密码后导入。')
        if rid == 0x0022 and len(payload) >= 2:
            date_1904 = bool(struct.unpack_from('<H', payload)[0])
        if rid == 0x0085 and len(payload) >= 8 and payload[5] == 0:
            bounds.append((struct.unpack_from('<I', payload)[0], StringChunks([payload[6:]]).unicode(short=True), bool(payload[4])))
        elif rid == 0x041E:
            formats[struct.unpack_from('<H', payload)[0]] = StringChunks([payload[2:]]).unicode()
        elif rid == 0x00E0:
            xfs.append(struct.unpack_from('<H', payload, 2)[0])
        elif rid == 0x00FC:
            unique = struct.unpack_from('<I', payload, 4)[0]
            if unique > MAX_CELLS:
                raise WorkbookError('共享字符串数量超过限制。')
            chunks = [payload[8:]]
            j = i + 1
            while j < len(globals_) and globals_[j][0] == 0x003C:
                chunks.append(globals_[j][1])
                j += 1
            reader = StringChunks(chunks)
            sst = [reader.unicode() for _ in range(unique)]
    if not bounds or len(bounds) > 50:
        raise WorkbookError('工作表数量无效（支持 1–50 张）。')
    sheets = []
    for offset, name, hidden in bounds:
        sh = Sheet(name=name, hidden=hidden, date_1904=date_1904)
        pending_string = None
        for rid, p, _ in records(stream, offset):
            if rid == 0x000A:
                break
            if rid in (0x00FD, 0x0203, 0x027E, 0x0204, 0x0205, 0x0006):
                if len(p) < 6:
                    raise WorkbookError('单元格记录损坏。')
                r, c, xf = struct.unpack_from('<HHH', p)
                fmt = formats.get(xfs[xf], '') if xf < len(xfs) else ''
                val, err = None, False
                if rid == 0x00FD:
                    idx = struct.unpack_from('<I', p, 6)[0]
                    if idx >= len(sst):
                        raise WorkbookError('共享字符串索引损坏。')
                    val = sst[idx]
                elif rid == 0x0203:
                    val = struct.unpack_from('<d', p, 6)[0]
                elif rid == 0x027E:
                    val = rk_number(p[6:10])
                elif rid == 0x0204:
                    val = StringChunks([p[6:]]).unicode()
                elif rid == 0x0205:
                    err = bool(p[7])
                    val = '#EXCEL_ERROR' if err else bool(p[6])
                elif rid == 0x0006:
                    if p[12:14] == b'\xff\xff':
                        if p[6] == 0:
                            pending_string = (r, c, fmt)
                        elif p[6] == 1:
                            val = bool(p[8])
                        elif p[6] == 2:
                            val, err = '#EXCEL_ERROR', True
                    else:
                        val = struct.unpack_from('<d', p, 6)[0]
                sh.put(r, c, Cell(val, fmt, rid == 0x0006, err))
            elif rid == 0x00BD:
                r, c0 = struct.unpack_from('<HH', p)
                count = (len(p) - 6) // 6
                for j in range(count):
                    xf = struct.unpack_from('<H', p, 4 + j * 6)[0]
                    fmt = formats.get(xfs[xf], '') if xf < len(xfs) else ''
                    sh.put(r, c0 + j, Cell(rk_number(p[6 + j * 6:10 + j * 6]), fmt))
            elif rid == 0x0207 and pending_string:
                r, c, fmt = pending_string
                sh.put(r, c, Cell(StringChunks([p]).unicode(), fmt, True), replace=True)
                pending_string = None
            elif rid == 0x00E5:
                for j in range(struct.unpack_from('<H', p)[0]):
                    sh.merges.append(struct.unpack_from('<HHHH', p, 2 + 8 * j))
        sh.finish()
        sheets.append(sh)
    return sheets

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'

class NoDTDTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise WorkbookError('不支持带 DTD 或实体的 XML 文件。')


def xml_parse(data: bytes):
    # XML declarations may be UTF-16/UTF-32, not just ASCII/UTF-8. Removing
    # encoding NULs makes the guard effective before any entity expansion.
    declarations = data.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
        raise WorkbookError('不支持带 DTD 或实体的 XML 文件。')
    parser = ET.XMLParser(target=NoDTDTreeBuilder())
    return ET.fromstring(data, parser=parser)

def coordinate(value: str) -> tuple[int, int]:
    m = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]*)', value)
    if not m:
        raise WorkbookError('Excel 单元格坐标异常。')
    col = 0
    for char in m[1]:
        col = col * 26 + ord(char) - 64
    return int(m[2]) - 1, col - 1

def read_xlsx(data: bytes) -> list[Sheet]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if len(infos) > 1500 or sum(f.file_size for f in infos) > MAX_EXPANDED:
            raise WorkbookError('工作簿解压后过大，最多支持 100 MB。')
        if any(f.flag_bits & 1 for f in infos):
            raise WorkbookError('不支持加密的工作簿。')
        if any(f.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for f in infos):
            raise WorkbookError('不支持此工作簿压缩方式，请在Excel/WPS中另存为普通XLSX。')
        names = set(z.namelist())
        if len(names) != len(infos):
            raise WorkbookError('工作簿包含重名文件，无法可靠读取。')
        if 'xl/workbook.xml' not in names:
            raise WorkbookError('这不是标准的 .xlsx 工作簿。')
        wb = xml_parse(z.read('xl/workbook.xml'))
        if not wb.tag.startswith(NS):
            raise WorkbookError('不支持此 OOXML 格式，请在 Excel/WPS 中另存为普通 .xlsx。')
        relations = xml_parse(z.read('xl/_rels/workbook.xml.rels'))
        targets = {}
        for item in relations:
            if item.attrib.get('TargetMode') == 'External':
                continue
            target = item.attrib.get('Target', '')
            # Resolve only workbook-local package paths, never filesystem paths.
            if target.startswith('/'):
                target = target[1:]
            else:
                target = str(PurePosixPath('xl') / target)
            if '..' in PurePosixPath(target).parts:
                raise WorkbookError('不支持工作簿中的父目录关系。')
            targets[item.attrib['Id']] = target
        strings = []
        if 'xl/sharedStrings.xml' in names:
            root = xml_parse(z.read('xl/sharedStrings.xml'))
            for si in root.findall(NS + 'si'):
                strings.append(''.join(x.text or '' for x in si.iter(NS + 't')))
                if len(strings) > MAX_CELLS:
                    raise WorkbookError('共享字符串过多。')
        formats, xfs = {9: '0%', 10: '0.00%', 14: 'yyyy-mm-dd', 22: 'yyyy-mm-dd hh:mm'}, []
        if 'xl/styles.xml' in names:
            st = xml_parse(z.read('xl/styles.xml'))
            for f in st.findall(f'{NS}numFmts/{NS}numFmt'):
                formats[int(f.attrib['numFmtId'])] = f.attrib.get('formatCode', '')
            for xf in st.findall(f'{NS}cellXfs/{NS}xf'):
                xfs.append(int(xf.attrib.get('numFmtId', 0)))
        sheet_nodes = wb.findall(f'{NS}sheets/{NS}sheet')
        if not 1 <= len(sheet_nodes) <= 50:
            raise WorkbookError('支持 1–50 张工作表。')
        sheets = []
        for item in sheet_nodes:
            target = targets.get(item.attrib.get(RID))
            if not target or target not in names or not target.endswith('.xml'):
                raise WorkbookError('工作表关系不完整。')
            properties = wb.find(NS + 'workbookPr')
            date_1904 = properties is not None and properties.attrib.get('date1904', '').lower() in {'1', 'true'}
            sh = Sheet(name=item.attrib['name'], hidden=item.attrib.get('state', 'visible') != 'visible', date_1904=date_1904)
            root = xml_parse(z.read(target))
            coordinates = set()
            for c in root.findall(f'{NS}sheetData/{NS}row/{NS}c'):
                r, col = coordinate(c.attrib['r'])
                if (r, col) in coordinates:
                    raise WorkbookError('工作表存在重复单元格坐标，无法可靠绑定学号。')
                coordinates.add((r, col))
                typ = c.attrib.get('t', 'n')
                node = c.find(NS + 'v')
                text = node.text if node is not None else None
                val = text
                if typ == 's' and text is not None:
                    idx = int(text)
                    if idx < 0 or idx >= len(strings):
                        raise WorkbookError('共享字符串索引无效。')
                    val = strings[idx]
                elif typ == 'inlineStr':
                    val = ''.join(t.text or '' for t in c.findall(f'{NS}is//{NS}t'))
                elif typ == 'b':
                    val = text == '1'
                elif typ == 'n' and text not in (None, ''):
                    val = float(text)
                style = int(c.attrib.get('s', 0))
                fmt = formats.get(xfs[style], '') if style < len(xfs) else ''
                sh.put(r, col, Cell(val, fmt, c.find(NS + 'f') is not None, typ == 'e'))
            for m in root.findall(f'{NS}mergeCells/{NS}mergeCell'):
                ends = m.attrib['ref'].split(':')
                a, b = coordinate(ends[0]), coordinate(ends[-1])
                sh.merges.append((a[0], b[0], a[1], b[1]))
            sh.finish()
            sheets.append(sh)
        return sheets

def read_workbook(data: bytes, filename: str) -> list[Sheet]:
    suffix = PurePosixPath(filename.replace('\\', '/')).suffix.lower()
    if suffix not in ('.xls', '.xlsx'):
        raise WorkbookError('请上传 .xls 或 .xlsx 文件；不要直接修改扩展名。')
    if not data or len(data) > 20 * 1024 * 1024:
        raise WorkbookError('上传文件须为 1 字节至 20 MB。')
    try:
        if suffix == '.xls':
            return read_xls(data)
        if not data.startswith(b'PK'):
            raise WorkbookError('文件内容不是 .xlsx；请用 Excel/WPS 重新另存。')
        return read_xlsx(data)
    except WorkbookError:
        raise
    except (ValueError, KeyError, IndexError, struct.error, UnicodeError, ET.ParseError, zipfile.BadZipFile, OverflowError) as exc:
        raise WorkbookError('工作簿结构异常或不受支持。请在 Excel/WPS 中打开，另存为普通 .xlsx 后重试。') from exc
