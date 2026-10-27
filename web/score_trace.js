import {h,percent,duration,badge} from './components.js';
import {statusDescription} from './score_status.js';

const text=value=>value===null||value===undefined||value===''?'（空值）':String(value);
const field=(label,value)=>`<div class="trace-field"><span>${h(label)}</span><strong>${h(text(value))}</strong></div>`;
export function recordTraceView({row,batch}) {
  const snapshot=row.source_row,columns=snapshot?.columns;
  const full=Array.isArray(columns);
  const before=row.before||{};
  // Rejected candidates are not committed after-values, including legacy snapshots.
  const rejected=['unmatched','invalid','name_conflict','duplicate_conflict'].includes(row.action);
  return `<div class="score-row-trace">
    <p class="trace-location"><b>批次 #${h(batch.id)} · ${h(batch.name)}</b><br>${h(batch.filename)} · 工作表「${h(snapshot?.sheet_name||batch.sheet_name||'未保存')}」 · 第 ${h(row.row_number)} 行${snapshot?.header_row?` · 表头第 ${h(snapshot.header_row)} 行`:''}</p>
    <p class="trace-note">这是该批次导入时保存的记录，不是目前成绩。CSV 行号为包含表头的逻辑记录号，字段内换行不另算一条。</p>
    <div class="trace-fields">${field('原学号',row.raw_id)}${field('清洗后学号',row.student_id)}${field('原易班 ID',row.raw_yiban_id)}${field('清洗后易班 ID',row.yiban_id)}${field('原姓名',row.source_name)}${field('导入时名单姓名',row.matched?row.name:'未匹配')}${field('原学院 / 班级',[row.source_college,row.source_class_name].filter(Boolean).join(' / '))}${field('导入时名单归属',row.matched?[row.college,row.class_name].filter(Boolean).join(' / '):'未匹配')}${field('原状态',row.raw_status)}<div class="trace-field"><span>清洗后状态及含义</span><div>${row.status?badge(row.status):'未能识别'}</div><p>${h(statusDescription(row.status))}</p></div>${field('原得分率 / 原成绩',[row.raw_rate,row.raw_result].filter(value=>value!==null&&value!==undefined&&value!=='').join(' / '))}${field('可识别得分率',percent(row.score_rate))}${field('开始时间',row.exam_started_at)}${field('结束时间',row.exam_finished_at)}${field('答题时长',duration(row.answer_duration_seconds))}${field('导入前状态 / 得分率',row.before?`${before.status||'未保存'} / ${percent(before.score_rate)}`:'导入时未匹配名单')}${field('本行写入情况',rejected?'异常未写入正式成绩':['protected','unchanged'].includes(row.action)?'保留原记录，未覆盖':row.action==='duplicate'?'同批已合并，本行未单独写入':'按本批次合并结果处理')}</div>
    <div class="trace-note"><b>清洗说明</b><p>${h(row.notes?.join('；')||'无额外清洗说明')}</p><b>异常原因</b><p>${h(row.errors?.join('；')||'无')}</p></div>
    <h4>${full?'原始行全部列':'旧记录已保存的原始字段'}</h4>
    ${full?`<p class="trace-note">逐列展示导入读取值，保留空列和重复表头。Excel 展示单元格读取值及格式，不重建视觉排版、不计算公式；公式仅保留读取标记 / 缓存值。易班 ID 只供检索，不替代学号匹配。</p><div class="source-columns" role="table" aria-label="原始行全部列"><div class="source-column source-column-heading" role="row"><span role="columnheader">源列 / 表头</span><span role="columnheader">原单元格值</span><span role="columnheader">读取说明</span></div>${columns.map(cell=>`<div class="source-column" role="row"><span role="cell"><b>${h(cell.column)} · ${h(cell.label||'无表头')}</b></span><span role="cell" class="source-cell-value">${h(text(cell.value))}</span><span role="cell">${h([cell.formula?'公式（未执行）':'',cell.error?'错误单元格':'',cell.number_format?`格式：${cell.number_format}`:''].filter(Boolean).join('；')||'原始读取值')}${cell.text!==String(cell.value??'')?`<div>格式化读取：${h(text(cell.text))}</div>`:''}</span></div>`).join('')}</div>`:`<div class="info-box warning">此旧批次未保存原始整行 / 易班 ID 等全部列，以上只展示当时实际保留的字段。不能从清洗结果还原未保存的数据；如需全部列，请重新导入原文件并核对确认，新增一次批次记录，旧历史不回填。</div>`}
  </div>`;
}
