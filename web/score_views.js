import {h,num,percent,date,duration,examTimeTitle,icon,pageHeading,blank,badge} from './components.js';
import {recordTraceView} from './score_trace.js';

export const scoreFields={student_id:'学号',name:'姓名（核对名单）',status:'状态',score_rate:'得分率',result_text:'成绩 / 百分比（备用）',college:'学院（核对名单）',class_name:'班级（核对名单）',exam_started_at:'开始时间（选填，L列）',exam_finished_at:'结束时间（选填，M列）',yiban_id:'易班 ID（选填，仅检索）'};
const actions={update:'将更新',unchanged:'保持不变',protected:'保留原成绩',unmatched:'未匹配',invalid:'数据错误',name_conflict:'姓名冲突',duplicate:'重复已合并',duplicate_conflict:'重复冲突'};
const tones={update:'success',unchanged:'neutral',protected:'pending',unmatched:'absent',invalid:'absent',name_conflict:'absent',duplicate:'neutral',duplicate_conflict:'absent'};
const pill=(action)=>`<span class="badge ${tones[action]||'neutral'}">${h(actions[action]||action)}</span>`;
export const newScoreState=()=>({upload:null,sheetIndex:0,headerRow:1,headerDraft:null,mapping:{},name:'',scale:'percent',zeroPolicy:'null_unfinished',busy:false,error:'',preview:null,skip:false,rows:[],rowTotal:0,rowPage:1,rowKind:'all',committed:null,batches:{items:[],total:0,page:1,page_size:10},audit:null});

function statCards(items) {
  return `<div class="score-metrics">${items.map(([value,label,note,cls])=>`<div class="metric ${cls||''}"><div class="metric-label">${h(label)}</div><div class="metric-value">${num(value)}</div><div class="metric-note">${h(note)}</div></div>`).join('')}</div>`;
}
function uploadBox(G) {
  return `<div class="dropzone ${G.busy?'busy':''}" id="score-dropzone">
    <div class="upload-symbol">${icon('upload')}</div>
    <h3>${G.busy?'正在读取成绩文件…':'将学生成绩拖到这里，或点击上传'}</h3>
    <p>成绩需包含学号、状态和得分率；开始 / 结束时间选填，旧报表可直接导入</p>
    <button class="btn primary ${G.busy?'spin':''}" data-action="score-choose" ${G.busy?'disabled':''}>${icon(G.busy?'refresh':'upload')}${G.busy?'正在识别表头':'选择成绩文件'}</button>
    <div class="dropzone-foot"><span class="format-pill">CSV / TSV</span><span class="format-pill">XLS / XLSX</span><span>单个文件最大 20 MB</span></div>
  </div>`;
}
function mapping(G) {
  const sh=G.upload.sheets[G.sheetIndex], disabled=G.busy?'disabled':'';
  const timeSupported=G.upload.timeSupported!==false,yibanSupported=G.upload.yibanSupported!==false;
  const timeNote=timeSupported?'开始 / 结束时间均为选填。旧报表无需补列，没有完整时间时正常导入成绩，答题时长显示“—”。':'兼容旧版服务：成绩可以正常导入，本次不处理时间，答题时长显示“—”；如需保存文件里的时间，请升级服务后重新导入。';
  return `<div class="uploaded-file">${icon('file')}<div class="file-content"><div class="file-name">${h(G.upload.filename)}</div><div class="file-meta">${h(G.upload.format.encoding)} · ${h(G.upload.format.delimiter)} · 表头后 ${num(sh.rows)} 行</div></div><button class="icon-btn" data-action="score-reset" title="更换文件" ${disabled}>${icon('close')}</button></div>
    <div class="form-field batch-name-field"><label for="score-name">本次导入名称 <span>*</span></label><input id="score-name" maxlength="120" value="${h(G.name)}" placeholder="输入便于查找的导入名称" ${disabled}></div>
    <div class="form-row"><div class="form-field"><label for="score-sheet">选择工作表</label><select id="score-sheet" ${disabled}>${G.upload.sheets.map((s,i)=>`<option value="${i}" ${i===G.sheetIndex?'selected':''}>${h(s.name)} · ${num(s.rows)} 行</option>`).join('')}</select></div><div class="form-field header-field"><label for="score-header">表头所在行</label><input type="number" id="score-header" value="${h(G.headerDraft??G.headerRow)}" min="1" max="60000" ${disabled}></div><button class="btn secondary" data-action="score-header" ${disabled}>重新识别</button></div>
    <div class="section-caption">确认字段对应关系<span class="small-muted">按学号匹配，姓名与学院班级仅用于核对</span></div>
    <div class="mapping score-mapping">${Object.entries(scoreFields).map(([key,label])=>`<div class="form-field"><label for="score-map-${key}">${h(label)} ${['student_id','status'].includes(key)?'<span>*</span>':''}</label><select id="score-map-${key}" data-score-map="${key}" ${G.busy||(!timeSupported&&['exam_started_at','exam_finished_at'].includes(key))||(!yibanSupported&&key==='yiban_id')?'disabled':''}><option value="">不使用此列</option>${sh.headers.map(c=>`<option value="${c.index}" ${G.mapping[key]===c.index?'selected':''}>${h(c.label)} · 第 ${c.index+1} 列</option>`).join('')}</select></div>`).join('')}</div>
    <div class="info-box score-merge-rule">${icon('check')}<span>增量导入：未录入的成绩补入，已有成绩保留较高得分率；相同分数保留原记录，可补齐缺失答题时间。答题时长＝结束时间－开始时间（M－L），不使用系统评定时间。<br><span id="score-time-note">${h(timeNote)}</span><br><span id="score-source-note">${yibanSupported?'易班 ID 只用于检索，不替代学号匹配。确认保存后，导入全部列将作为本地历史快照保留，请勿上传无关敏感列。':'兼容旧版服务：本次不保存易班 ID / 原始整行快照，原成绩导入仍可正常使用。'}</span></span></div>
    <details class="cleaning-settings"><summary>${icon('shield')}数值识别设置</summary><div class="settings-grid">
      <div class="form-field"><label for="score-scale">无百分号的数字</label><select id="score-scale" ${disabled}><option value="percent" ${G.scale==='percent'?'selected':''}>按 0–100：92 表示 92%（默认）</option><option value="fraction" ${G.scale==='fraction'?'selected':''}>按 0–1：0.92 表示 92%</option></select></div>
      <div class="form-field"><label for="score-zero">未开始 / 进行中的 0 分</label><select id="score-zero" ${disabled}><option value="null_unfinished" ${G.zeroPolicy==='null_unfinished'?'selected':''}>视为占位，正式得分率留空（推荐）</option><option value="keep" ${G.zeroPolicy==='keep'?'selected':''}>保留原表 0 分，统计仍排除未完成</option></select></div>
    </div></details>`;
}
function comparison(row) {
  const before=row.before?.score_rate;
  const retained=['protected','unchanged'].includes(row.action);
  let after;
  if(retained) after=before;
  else if(['update','duplicate'].includes(row.action)) after=row.after?.score_rate;
  return `<span class="source-text">${percent(before)}</span><strong class="rate-value">→ ${percent(after)}</strong>`;
}
function previewTable(rows,batch=null) {
  return `<div class="table-scroll"><table class="cleaning-table"><thead><tr><th>行</th><th>原学号 → 清洗后</th><th>学生</th><th>本次状态</th><th>本次得分率</th><th>原有 → 合并后</th><th>本次答题时长</th><th>处理结果</th><th>核对说明</th></tr></thead><tbody>${rows.length?rows.map(r=>`<tr>
    <td class="row-index">${r.row_number}</td><td><div class="source-text mono">${h(r.raw_id)}</div><div class="mono strong">${h(r.student_id||'无法提取')}</div></td>
    <td><strong>${h(r.name||'—')}</strong><div class="cell-secondary">${h(r.class_name||'未在名单中匹配')}</div>${r.source_name&&r.source_name!==r.name?`<div class="cell-secondary">原姓名：${h(r.source_name)}</div>`:''}</td>
    <td>${r.status?badge(r.status):h(r.raw_status)}</td><td><span class="source-text">${h(r.raw_rate||r.raw_result||'空值')}</span><div class="rate-value">${percent(r.score_rate)}</div></td>
    <td>${comparison(r)}</td><td class="duration-cell" title="${h(examTimeTitle(r))}"><strong>${duration(r.answer_duration_seconds)}</strong><div class="cell-secondary">合并后：${duration(['invalid','unmatched','name_conflict','duplicate_conflict'].includes(r.action)?null:r.after?.answer_duration_seconds)}</div></td><td>${pill(r.action)}</td><td class="cleaning-notes">${r.errors.length?`<div class="text-danger">${h(r.errors.join(' '))}</div>`:''}${r.notes.length?h(r.notes.join('；')):'无需额外清洗'}${batch?`<button class="btn secondary small audit-source-toggle" data-action="score-audit-source" data-row="${h(r.row_number)}" aria-controls="score-audit-source">展开原始行 · 第 ${h(r.row_number)} 行</button>`:''}</td>
  </tr>`).join(''):`<tr><td colspan="9">${blank('此分类暂无记录','当前筛选结果为空。')}</td></tr>`}</tbody></table></div>`;
}
function rowPager(total,page,actionPrefix) {
  const pages=Math.max(1,Math.ceil(total/20));
  return `<div class="pagination"><span>共 ${num(total)} 条 · 每页 20 条</span><div class="page-controls"><button class="btn secondary small" data-action="${actionPrefix}-prev" ${page<=1?'disabled':''}>上一页</button><span class="page-number">${page} / ${pages}</span><button class="btn secondary small" data-action="${actionPrefix}-next" ${page>=pages?'disabled':''}>下一页</button></div></div>`;
}
function preview(G) {
  const p=G.preview,s=p.summary, added=s.new_scores||0, updated=s.update-added;
  const onlyIssues=!p.can_commit&&p.can_save_issues;
  const confirmation=onlyIssues?`保存 ${num(s.issue_rows)} 条异常记录`:`确认导入 · 新增 ${num(added)} / 更新 ${num(updated)}`;
  return `<section class="card preview-section score-preview" id="score-preview">
    <div class="card-header"><div><div class="section-title">${icon('check')}<h2>导入预览与校验</h2></div><p class="card-subtitle">共 ${num(s.source_rows)} 行 · 匹配 ${num(s.matched_students)} 位学生 · ${num(s.duplicate)} 行重复已合并</p></div><span class="badge ${s.issue_rows?'pending':'success'}">${s.issue_rows?'需要核对':'校验通过'}</span></div>
    ${statCards([[added,'新增 / 补录','原得分率未录入',''],[updated,'提高 / 更新','保留较高成绩与完整状态',''],[s.unchanged+s.protected,'保留不变','相同、较低或空值不覆盖',''],[s.issue_rows,'异常记录','需修正或明确跳过',s.issue_rows?'metric-warning':'']])}
    ${p.prior_batches.length?`<div class="card-body compact"><div class="info-box warning">${icon('info')}此文件之前已导入。仍会保存本次记录；相同或较低得分率不会重复覆盖。</div></div>`:''}
    <div class="preview-toolbar"><div class="tabs" role="group" aria-label="预览分类">${[['all','全部',s.source_rows],['update','新增 / 更新',s.update],['protected','保留原成绩',s.protected],['unchanged','相同记录',s.unchanged],['issues','异常',s.issue_rows]].map(([key,title,count])=>`<button class="tab ${G.rowKind===key?'active':''}" data-action="score-tab" data-kind="${key}" ${G.busy?'disabled':''}>${title}<span>${num(count)}</span></button>`).join('')}</div><a class="btn secondary small" href="/api/scores/previews/${h(p.preview_id)}/export.csv" download>${icon('download')}导出清洗明细</a></div>
    ${previewTable(G.rows)}${rowPager(G.rowTotal,G.rowPage,'score-rows')}
    <div class="card-body commit-area">${s.issue_rows?`<div class="info-box warning">${icon('alert')}<div><b>${num(s.issue_rows)} 行不会直接写入</b><p>包含名单外学号、非法数据或身份冲突。请修正文件，或明确选择跳过；名单外学生需先导入名单。</p><a href="/api/scores/previews/${h(p.preview_id)}/export.csv?kind=issues" download>下载全部异常记录</a></div></div><label class="check-option skip-confirm"><input type="checkbox" id="score-skip" ${G.skip?'checked':''} ${G.busy?'disabled':''}><span><b>${onlyIssues?`我已核对，仅保存 ${num(s.issue_rows)} 条异常记录，不修改正式成绩`:`我已核对，跳过 ${num(s.issue_rows)} 行异常`}</b><small>异常原值和原因仍保存在本次导入记录中。</small></span></label>`:''}
      <div class="commit-actions"><p class="hint">${icon('shield')}未出现在本表中的学生保持原状。</p><button class="btn primary" data-action="score-commit" ${G.busy||(!p.can_commit&&!p.can_save_issues)||(s.issue_rows&&!G.skip)?'disabled':''}>${icon(G.busy?'refresh':'check')}${G.busy?'正在保存…':confirmation}</button></div>
    </div>
  </section>`;
}
function success(G) {
  const b=G.committed, added=b.summary.new_scores||0;
  const stats=b.options?.save_issues_only?[[b.summary.issue_rows,'保存异常记录']]:[[added,'新增 / 补录'],[b.updated_count-added,'提高 / 更新'],[b.summary.unchanged+b.summary.protected,'保留不变']];
  return `<section class="card success-panel"><div class="success-symbol">${icon('check')}</div><h2>${b.options?.save_issues_only?'异常记录已保存，正式成绩未修改':'学生成绩已合并保存'}</h2>${G.error?`<div class="info-box warning" role="alert">${h(G.error)}</div>`:''}<p>${h(b.name)} · 批次 #${b.id}</p>
    <div class="success-stats">${stats.map(([value,label])=>`<div><strong>${num(value)}</strong><span>${label}</span></div>`).join('')}</div>
    ${b.summary.issue_rows?`<p class="small-muted">${num(b.summary.issue_rows)} 行异常未写入，原始记录已保留。</p>`:''}
    <div class="success-actions">${b.summary.issue_rows?`<button class="btn secondary" data-action="score-issues-open">${icon('alert')}查看异常人员</button>`:''}<a class="btn primary" href="#scores">查看成绩明细${icon('arrow')}</a><a class="btn secondary" href="/api/scores/batches/${b.id}/export.csv" download>${icon('download')}导出本次记录</a><button class="btn secondary" data-action="score-reset">继续导入成绩</button></div>
  </section>`;
}
function history(G) {
  const H=G.batches, pages=Math.max(1,Math.ceil(H.total/H.page_size));
  return `<section class="card history" id="score-history"><div class="card-header"><div class="section-title">${icon('clock')}<h2>成绩导入记录</h2></div><div class="history-tools"><span class="small-muted">全部保存 · 共 ${num(H.total)} 个批次</span><button class="btn secondary small" data-action="score-history-refresh" ${G.busy?'disabled':''}>${icon('refresh')}刷新</button></div></div>
    ${H.items.length?`<div class="table-scroll"><table><thead><tr><th>批次 / 文件</th><th>导入时间</th><th>新增 / 更新</th><th>保留不变</th><th>异常</th><th>状态</th><th>操作</th></tr></thead><tbody>${H.items.map(b=>`<tr>
      <td><b>${h(b.name)}</b><div class="cell-secondary file-name-small">#${b.id} · ${h(b.filename)}</div></td><td>${h(date(b.created_at))}</td><td class="strong">${num(b.summary.new_scores||0)} / ${num(b.updated_count-(b.summary.new_scores||0))}</td><td>${num(b.summary.unchanged+b.summary.protected)}</td><td>${num(b.summary.issue_rows)}</td>
      <td><span class="badge ${b.state==='reverted'?'neutral':'success'}">${b.state==='reverted'?'已撤销':'已入库'}</span></td><td><div class="inline-actions"><button class="btn ghost small" data-action="score-audit" data-id="${b.id}" ${G.busy?'disabled':''}>明细</button><a class="btn ghost small" href="/api/scores/batches/${b.id}/export.csv" download>导出</a>${b.state==='committed'?`<button class="btn ghost small danger" data-action="score-revert" data-id="${b.id}" ${G.busy?'disabled':''}>撤销</button>`:''}</div></td>
    </tr>`).join('')}</tbody></table></div><div class="pagination"><span>全部 ${num(H.total)} 个批次 · 每页 ${H.page_size} 条</span><div class="page-controls"><button class="btn secondary small" data-action="score-history-prev" ${H.page<=1||G.busy?'disabled':''}>上一页</button><span class="page-number">${H.page} / ${pages}</span><button class="btn secondary small" data-action="score-history-next" ${H.page>=pages||G.busy?'disabled':''}>下一页</button></div></div>`:blank('还没有成绩导入记录','完成导入后，每个批次及其明细都会保存于此。')}
    ${G.audit?audit(G):''}
  </section>`;
}
function audit(G) {
  const A=G.audit,source=A.items.find(row=>row.row_number===A.sourceRow);
  const sourcePanel=source?`<section class="issue-source-panel" id="score-audit-source" tabindex="-1"><div class="source-panel-header"><h4>原表第 ${h(source.row_number)} 行</h4><button class="btn secondary small" data-action="score-audit-source-close">收起原始行</button></div>${recordTraceView({row:source,batch:A.batch})}</section>`:'';
  return `<section class="score-audit" id="score-audit"><div class="card-header"><div><h2>批次 #${A.batch.id} · 导入明细</h2><p class="card-subtitle">${h(A.batch.name)} · ${A.batch.state==='reverted'?'已撤销，历史记录仍完整保留':'导入时的快照，不代表学生目前的值'}</p></div><button class="icon-btn" data-action="score-audit-close" title="关闭明细">${icon('close')}</button></div><div class="preview-toolbar"><div class="tabs">${[['all','全部记录'],['issues','异常记录']].map(([key,title])=>`<button class="tab ${A.kind===key?'active':''}" data-action="score-audit-tab" data-kind="${key}" ${G.busy?'disabled':''}>${title}</button>`).join('')}</div><a class="btn secondary small" href="/api/scores/batches/${A.batch.id}/export.csv?kind=${h(A.kind)}" download>${icon('download')}导出</a></div>${previewTable(A.items,A.batch)}${sourcePanel}${rowPager(A.total,A.page,'score-audit')}</section>`;
}
export function scoreImportView(S) {
  const G=S.score, stage=G.committed?3:G.preview?3:G.upload?2:1;
  return pageHeading('SCORE IMPORT','导入成绩','按学号增量合并学生成绩，未录入的补入，已有的保留较高得分率。',`<button class="btn secondary" data-action="score-issues-open" ${G.busy?'disabled':''}>${icon('alert')}异常人员</button><button class="btn secondary" data-action="score-people-open" ${G.busy?'disabled':''}>${icon('search')}人员状态查询</button><a class="btn secondary" href="/static/templates/score_template.csv" download>${icon('download')}下载成绩模板</a><a class="btn secondary" href="#scores">${icon('chart')}查看成绩</a>`) + `<div class="score-import-workflow">
    ${G.committed?success(G):`<section class="card score-import-card">
      <div class="card-header"><div class="section-title">${icon('upload')}<h2>导入学生成绩</h2></div></div>
      <div class="steps">${['选择文件','识别与预览','确认入库'].map((title,i)=>`${i?'<span class="step-line"></span>':''}<div class="step ${stage===i+1?'active':stage>i+1?'done':''}"><span class="step-circle">${stage>i+1?icon('check'):i+1}</span><span>${title}</span></div>`).join('')}</div>
      <div class="card-body"><input class="sr-only" id="score-file" type="file" accept=".csv,.tsv,.xls,.xlsx" aria-label="选择成绩文件">${G.upload?mapping(G):uploadBox(G)}
        ${G.upload&&!S.stats.students?'<div class="info-box warning">当前没有学生名单，可仅保存异常记录；写入正式成绩前请先导入名单。<a href="#import">去导入名单 →</a></div>':''}
        ${G.error?`<div class="info-box error" role="alert">${icon('alert')}<span>${h(G.error)}</span></div>`:''}
      </div><div class="card-footer"><button class="btn primary" data-action="score-preview" ${G.busy||!G.upload?'disabled':''}>${icon(G.busy?'refresh':'arrow')}${G.busy?'正在校验全部数据…':'生成导入预览'}</button></div>
    </section>`}
    ${G.committed&&G.error?`<div class="info-box error" role="alert">${icon('alert')}<span>${h(G.error)}</span></div>`:''}
    ${G.preview?preview(G):''}
  </div>${history(G)}`;
}
