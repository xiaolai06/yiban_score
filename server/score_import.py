"""Bounded score-file readers and deterministic, auditable cleaning.

No fuzzy identity matching, execution of spreadsheet formulas, or guessed scales.
Only selected score-related fields are returned; unrelated account columns are not
persisted in the audit database. All row numbers are 1-based logical record rows.
"""
from __future__ import annotations

import csv
import io
import math
import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from .excel_reader import MAX_COLS, MAX_ROWS, Cell, Sheet, WorkbookError, read_workbook
from .grade_rules import round_score
from .roster import STATUSES, header_key

FIELDS = {'student_id':'学号', 'name':'姓名（仅核对）', 'status':'考试 / 登记状态',
          'score_rate':'得分率', 'result_text':'成绩 / 百分比（备用）',
          'college':'学院（仅核对）', 'class_name':'班级（仅核对）'}
ALIASES = {
    'student_id':['学号','学生学号','学生编号','studentid','studentno','studentnumber'],
    'name':['姓名','学生姓名','name','studentname'],
    'status':['状态','考试状态','完成状态','登记状态','成绩状态','status'],
    'score_rate':['得分率','得分率(%)','得分率（%）','得分率%','scorerate','百分比'],
    'result_text':['成绩 / 百分比','成绩/百分比','成绩百分比','成绩与百分比'],
    'college':['学院','院系','所属学院','学院名称','院系名称'],
    'class_name':['班级','行政班','班级名称','所属班级'],
}
LOOKUP = {header_key(v): k for k, names in ALIASES.items() for v in names}
UNFINISHED = {'未开始', '进行中'}
FINAL = {'已完成', '已录入', '缺考', '免考'}
STATUS_MAP = {s:s for s in STATUSES} | {
    '完成':'已完成', 'completed':'已完成', 'finished':'已完成',
    'notstarted':'未开始', '未开考':'未开始',
    'inprogress':'进行中', '考试中':'进行中', '答题中':'进行中',
}
MISSING = {'', '-', '--', '—', '–', '无', 'null', 'none', 'n/a', 'na', '未录入', '未结算'}
ACTIONS = {'update':'更新成绩', 'unchanged':'无变化', 'protected':'保护已有成绩',
           'unmatched':'未匹配学号', 'invalid':'数据错误', 'name_conflict':'姓名冲突',
           'duplicate':'合并相同重复行', 'duplicate_conflict':'重复学号冲突'}


def tidy(value: str) -> str:
    return re.sub(r'[\ufeff\u200b\u200c\u200d\u2060]', '', unicodedata.normalize('NFKC', value)).strip()


def decode_csv(data: bytes) -> tuple[str, str]:
    if not data:
        raise WorkbookError('文件为空。')
    encodings = ['utf-16'] if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
    for encoding in encodings:
        try:
            text = data.decode(encoding, errors='strict')
            if '\x00' in text:
                raise WorkbookError('CSV 含有空字节。请导出为 UTF-8、GBK 或带 BOM 的 UTF-16。')
            return text, ('UTF-8' if encoding=='utf-8-sig' else 'GB18030 / GBK' if encoding=='gb18030' else 'UTF-16')
        except UnicodeDecodeError:
            continue
    raise WorkbookError('无法无损解码 CSV。请使用 UTF-8 或 GBK 编码重新导出；不会用乱码替换原数据。')


def read_score_file(data: bytes, filename: str) -> tuple[list[Sheet], dict]:
    if filename.lower().endswith(('.xls', '.xlsx')):
        return read_workbook(data, filename), {'encoding':'Excel 工作簿','delimiter':'工作表列'}
    if not filename.lower().endswith(('.csv', '.tsv')):
        raise WorkbookError('成绩文件支持 CSV、TSV、XLS 和 XLSX。')
    text, encoding = decode_csv(data)
    sample = '\n'.join(text.splitlines()[:30])
    first = text.splitlines()[0] if text.splitlines() else ''
    explicit = re.fullmatch(r'sep=([,;\t])', first.strip(), re.IGNORECASE)
    if explicit:
        delimiter = explicit[1]
        text = '\n' + text[len(first):].lstrip('\r\n')  # keep record row numbers
    else:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=',;\t').delimiter
        except csv.Error:
            delimiter = max(',;\t', key=lambda c:first.count(c))
            if first.count(delimiter)==0:
                raise WorkbookError('找不到有效分隔符，请导出为逗号、分号或制表符分隔的表格。') from None
    sh = Sheet('成绩数据')
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
    try:
        for r, values in enumerate(reader):
            if r >= MAX_ROWS or len(values)>MAX_COLS:
                raise WorkbookError(f'文件最多 {MAX_ROWS} 行、{MAX_COLS} 列。')
            sh.column_counts[r] = len(values)
            for c, value in enumerate(values):
                if len(value)>32000:
                    raise WorkbookError(f'第 {r+1} 行单元格过长。')
                sh.put(r, c, Cell(value))
    except csv.Error as e:
        raise WorkbookError(f'CSV 格式错误（记录附近物理行 {reader.line_num}）：{e}') from e
    if not sh.rows:
        raise WorkbookError('没有可读取的数据。')
    return [sh], {'encoding':encoding, 'delimiter': {'\t':'制表符', ',':'逗号', ';':'分号'}[delimiter]}


def score_sheet_info(sh: Sheet, index: int, header_row: int | None = None) -> dict:
    if header_row is None:
        def score(r):
            keys = {LOOKUP.get(header_key(x)) for x in sh.texts(r)}
            return 100*len(keys & {'student_id','status'}) + 50*bool(keys & {'score_rate','result_text'}) + len(keys-{None})
        header_row = max(range(min(30, max(1,sh.rows))), key=score)+1
    if not 1<=header_row<=max(1,sh.rows):
        raise WorkbookError('表头行号超出范围。')
    mapping = {}
    for i, label in enumerate(sh.texts(header_row-1)):
        key = LOOKUP.get(header_key(label))
        if key and key not in mapping:
            mapping[key]=i
    return {'index':index, 'name':sh.name, 'hidden':sh.hidden, 'rows':max(0,sh.rows-header_row),
            'header_row':header_row, 'headers':[{'index':i,'label':v or f'第 {i+1} 列'} for i,v in enumerate(sh.texts(header_row-1))],
            'mapping':mapping, 'recognized':len(mapping)}


def clean_identifier(cell: Cell) -> tuple[str, list[str]]:
    notes=[]
    if cell.formula or cell.error or isinstance(cell.value,bool):
        raise ValueError('学号不能是公式、错误或布尔值。')
    if isinstance(cell.value,(int,float)):
        if not math.isfinite(cell.value) or not float(cell.value).is_integer() or cell.value<0:
            raise ValueError('数字型学号必须是非负整数。')
        if len(str(int(cell.value)))>15:
            raise ValueError('数字型学号超过 15 位，可能已丢失精度，请改为核对后的文本。')
    raw=cell.text()
    text=tidy(raw)
    # A literal ="digits" wrapper in CSV is unwrapped without eval or formula execution.
    wrapper=re.fullmatch(r'=\s*"([0-9A-Za-z._-]+)"', text)
    if wrapper:
        text=wrapper[1]
        notes.append('去除 Excel 文本包装')
    stripped=text.lstrip("`'‘’＇｀").strip()
    if stripped!=text:
        text=stripped
        notes.append('去除学号前的反引号 / 单引号')
    if re.fullmatch(r'[0-9\s]+',text):
        text=re.sub(r'\s+','',text)
    if re.fullmatch(r'[0-9]+\.0+',text):
        text=text.split('.')[0]
        notes.append('去除学号的 .0 小数尾缀')
    if not text:
        raise ValueError('缺少学号，不能用姓名或易班 ID 代替匹配。')
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+',text):
        raise ValueError('学号为科学计数法，拒绝猜测或恢复；请提供完整文本学号。')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',text):
        raise ValueError('学号含不支持的字符；仅清除明确格式前缀，不删除任意标点。')
    if text!=raw and not notes:
        notes.append('统一全角、空白或隐藏字符')
    return text, notes


def clean_status(raw: str) -> str:
    text=tidy(raw)
    value=STATUS_MAP.get(text) or STATUS_MAP.get(re.sub(r'[\s_-]','',text).lower())
    if not value:
        raise ValueError('缺少或无法识别状态：'+(text or '空值')+'。请先核对原文件。')
    return value


def clean_rate(cell: Cell, scale: str='percent') -> float | None:
    if cell.error or cell.formula or isinstance(cell.value,bool):
        raise ValueError('得分率不能是公式、错误或布尔值。')
    if isinstance(cell.value,(int,float)) and not math.isfinite(cell.value):
        raise ValueError('得分率必须是有限数值。')
    text=tidy(cell.text())
    if text.lower() in MISSING:
        return None
    percent=text.endswith('%')
    core=text[:-1].strip() if percent else text
    # Reject ambiguous decimal commas, units, infinity, and arbitrary text.
    if not re.fullmatch(r'[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)',core):
        raise ValueError('得分率必须是 0–100 的数字或百分比，不能包含含糊单位或非数值。')
    try:
        value=Decimal(core)
        excel_percent=isinstance(cell.value,(int,float)) and '%' in cell.number_format
        if not percent and (excel_percent or scale=='fraction'):
            if not 0<=value<=1:
                raise ValueError('选择 0–1 小数比例时，无百分号数值必须在 0–1 范围。')
            value*=100
        if not value.is_finite() or not 0<=value<=100:
            raise ValueError('得分率超出 0–100%。')
        return round_score(value)
    except InvalidOperation:
        raise ValueError('得分率精度异常，请核对原值。') from None


def result_rate(cell: Cell) -> float | None:
    if cell.formula or cell.error:
        raise ValueError('成绩 / 百分比列不能是公式或错误值。')
    text=tidy(cell.text())
    if text.lower() in MISSING:
        return None
    match=re.fullmatch(r'(?:[^/%]{1,100}/\s*)?([+]?(?:\d+(?:\.\d*)?|\.\d+)\s*%)', text)
    if not match:
        raise ValueError('成绩 / 百分比必须含明确百分号，例如 92 / 92.0%；不会用斜杠两侧做除法。')
    return clean_rate(Cell(match[1]))


def status_priority(status: str) -> int:
    return {'待录入': 0, '未开始': 1, '进行中': 2}.get(status, 3)


def highest_score_action(old: dict, incoming: dict) -> tuple[str, str]:
    """Missing is not zero; ties keep provenance unless status advances."""
    before, after = old['score_rate'], incoming['score_rate']
    if before is not None and after is None:
        return 'protected', '本次得分率为空，保留原有得分率'
    if before is not None and after is not None:
        if after < before:
            return 'protected', f'本次 {after:g}% 低于原有 {before:g}%，保留较高成绩'
        if after > before:
            return 'update', f'得分率由 {before:g}% 提高到 {after:g}%'
    elif after is not None:
        return 'update', f'原得分率未录入，新增成绩 {after:g}%'
    if status_priority(incoming['status']) > status_priority(old['status']):
        return 'update', '得分率不变，补充更完整的登记状态'
    return 'unchanged', '得分率相同或均为空，保留原记录及来源'


def build_preview(sh: Sheet, header_row: int, mapping: dict, existing: dict, *,
                  scale: str='percent', zero_policy: str='null_unfinished',
                  update_mode: str='keep_highest', protect_completed: bool=True) -> dict:
    if not 1<=header_row<=sh.rows:
        raise WorkbookError('表头行号无效。')
    if any(k not in mapping for k in ('student_id','status')) or not {'score_rate','result_text'} & mapping.keys():
        raise WorkbookError('必须映射学号、状态，以及得分率或成绩 / 百分比列。')
    if any(k not in FIELDS for k in mapping) or len(set(mapping.values()))!=len(mapping):
        raise WorkbookError('字段映射存在不支持的字段或重复列。')
    if any(type(v) is not int or not 0<=v<sh.cols for v in mapping.values()):
        raise WorkbookError('字段映射列号无效。')
    rows=[]
    blank=0
    repeated=0
    for ri in range(header_row,sh.rows):
        if not sh.has_content(ri):
            blank+=1
            continue
        if all(LOOKUP.get(header_key(sh.cell(ri,mapping[k]).text()))==k for k in ('student_id','status')):
            repeated+=1
            continue
        cells={k:sh.cell(ri,c) for k,c in mapping.items()}
        raw={k:c.text() for k,c in cells.items()}
        r={'row_number':ri+1,'raw_id':raw.get('student_id',''),'source_name':raw.get('name',''),
           'source_college':raw.get('college',''),'source_class_name':raw.get('class_name',''),
           'raw_status':raw.get('status',''),'raw_rate':raw.get('score_rate',''),
           'raw_result':raw.get('result_text',''), 'student_id':'','name':'',
           'college':'','class_name':'','status':None,'score_rate':None,
           'action':'update','notes':[],'errors':[],'matched':False,'before':None,'after':None}
        if sh.column_counts and sh.column_counts.get(ri)!=sh.column_counts.get(header_row-1):
            r['errors'].append('CSV 列数与表头不一致，可能发生错位；请核对分隔符和引号。')
        try:
            r['student_id'],notes=clean_identifier(cells['student_id'])
            r['notes']+=notes
        except ValueError as e:
            r['errors'].append(str(e))
        try:
            r['status']=clean_status(raw.get('status',''))
            if r['status']!=raw.get('status',''):
                r['notes'].append('统一状态写法')
        except ValueError as e:
            r['errors'].append(str(e))
        try:
            rate=clean_rate(cells['score_rate'],scale) if 'score_rate' in cells else None
            fallback=result_rate(cells['result_text']) if 'result_text' in cells else None
            if rate is not None and fallback is not None and abs(rate-fallback)>.011:
                raise ValueError('得分率列与成绩 / 百分比列不一致，请核对；不会擅自选用其中一个。')
            if rate is None and fallback is not None:
                rate=fallback
                r['notes'].append('从成绩 / 百分比提取百分号前数值')
            if rate==0 and r['status'] in UNFINISHED and zero_policy=='null_unfinished':
                rate=None
                r['notes'].append('未完成的占位 0 分置空；原值保留在批次记录')
            if rate is None and r['status'] in {'已完成','已录入'}:
                raise ValueError('已完成 / 已录入但缺少有效得分率，请先核对。')
            r['score_rate']=rate
        except ValueError as e:
            r['errors'].append(str(e))
        old=existing.get(r['student_id'])
        if old:
            r.update(matched=True,name=old['name'],college=old['college_name'],class_name=old['class_name'])
            r['before']={k:old.get(k) for k in ('status','score_rate','version','score_batch_id','score_imported_at')}
            if not tidy(raw.get('name','')):
                r['notes'].append('原表姓名为空；按学号使用学生库姓名')
            elif tidy(raw['name'])!=tidy(old['name']):
                r['action']='name_conflict'
                r['errors'].append('学号匹配但姓名不同；不自动覆盖，需核对原文件。')
            for key, oldkey in [('college','college_name'),('class_name','class_name')]:
                if tidy(raw.get(key,'')) and tidy(raw[key])!=tidy(old[oldkey]):
                    r['notes'].append(FIELDS[key]+'与名单不同；保留名单归属')
            r['after']={'status':r['status'],'score_rate':r['score_rate']}
            if r['action']!='name_conflict':
                if r['errors']:
                    r['action']='invalid'
                elif old['status']==r['status'] and old['score_rate']==r['score_rate']:
                    r['action']='unchanged'
                elif protect_completed and old['status'] in FINAL and r['status'] in UNFINISHED | {'待录入'}:
                    r['action']='protected'
                    r['notes'].append('保护已有完成 / 缺考 / 免考记录，未回退状态')
                elif update_mode=='keep_highest':
                    r['action'], note = highest_score_action(old, r)
                    r['notes'].append(note)
                elif update_mode=='fill_missing' and (old['score_rate'] is not None or old['status'] in FINAL):
                    r['action']='protected'
                    r['notes'].append('只补缺失模式：保留已有成绩')
            if r['action'] in {'protected', 'unchanged'}:
                r['after'] = {'status': old['status'], 'score_rate': old['score_rate']}
        else:
            r['name']=tidy(raw.get('name',''))
            r['action']='invalid' if r['errors'] else 'unmatched'
            if not r['errors']:
                r['errors'].append('学号不在学生名单数据库中；请先导入名单，再补入成绩。')
        rows.append(r)
    # Evaluate duplicates after cleaning. A malformed sibling prevents arbitrary
    # selection of another record with the same identifier.
    groups=defaultdict(list)
    for r in rows:
        if r['student_id']:
            groups[r['student_id']].append(r)
    for group in groups.values():
        if len(group)<2:
            continue
        same=all((r['status'],r['score_rate'],tidy(r['source_name']),r['action'])==
                 (group[0]['status'],group[0]['score_rate'],tidy(group[0]['source_name']),group[0]['action']) for r in group)
        safe=all(r['action'] in {'update','unchanged','protected'} for r in group)
        winner = None
        if safe and same:
            winner = group[0]
        elif safe and update_mode == 'keep_highest':
            candidates = group
            if protect_completed:
                completed = [r for r in group if r['status'] in FINAL]
                if completed:
                    candidates = completed
            def priority(r):
                rate = r['score_rate']
                return (rate is not None, rate if rate is not None else 0, status_priority(r['status']))
            winner = max(candidates, key=priority)
            tied = [r for r in candidates if priority(r) == priority(winner)]
            if len({r['status'] for r in tied}) > 1:
                winner = None  # e.g. absent versus exempt; never guess the status.
        if winner is not None:
            winner['notes'].append(f'同学号重复记录合并，保留第 {winner["row_number"]} 行的较高有效得分率')
            for r in group:
                if r is winner:
                    continue
                r['action'] = 'duplicate'
                r['after'] = winner['after']
                r['notes'].append(f'与第 {winner["row_number"]} 行同学号，已合并；本行原值仍保留在导入记录')
        else:
            for r in group:
                r['action']='duplicate_conflict'
                r['errors'].append('清洗后同学号出现冲突记录；请人工选择正确的一条。')
    counts=Counter(r['action'] for r in rows)
    issues=sum(counts[k] for k in ('unmatched','invalid','name_conflict','duplicate_conflict'))
    summary: dict = {k:counts[k] for k in ACTIONS}
    updates = [r for r in rows if r['action'] == 'update']
    summary.update(new_scores=sum(r['before']['score_rate'] is None for r in updates),
                   improved_scores=sum(r['before']['score_rate'] is not None and r['score_rate'] is not None
                                       and r['score_rate'] > r['before']['score_rate'] for r in updates),
                   status_updates=sum(r['before']['score_rate'] is not None and r['score_rate'] == r['before']['score_rate']
                                      for r in updates),
                   source_rows=len(rows),matched_rows=sum(r['matched'] for r in rows),
                   matched_students=len({r['student_id'] for r in rows if r['matched']}),
                   issue_rows=issues,cleaned_rows=sum(bool(r['notes']) for r in rows),
                   placeholder_zero_rows=sum(any('占位 0 分' in n for n in r['notes']) for r in rows),
                   blank_name_rows=sum(not tidy(r['source_name']) for r in rows),
                   blank_rows=blank,repeated_headers=repeated,
                   statuses=dict(Counter(r['status'] for r in rows if r['status'])))
    return {'rows':rows,'summary':summary,'action_labels':ACTIONS,
            'warnings':['只按学号匹配，成绩导入不新增学生、不改变学院和班级。',
                        '当前维护每名学生的一组状态与得分率；批次是导入审计，不是独立的多科考试账册。',
                        '默认增量合并：未录入的成绩补入，已有成绩保留较高得分率；原始行和处理结果长期保留。']}
