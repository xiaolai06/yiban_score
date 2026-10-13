import {scoreImportView} from './score_views.js';
import {selectedStudentCount,studentIsChecked,DIRECTORY_UPDATE_MESSAGE} from './directory_controller.js';
import {hasValidScore} from './grade_values.js';
import {statuses, h, num, percent, date, icon, badge, metrics, pageHeading, templateLink, blank} from './components.js';
const fields = {student_id:'学号',name:'姓名',gender:'性别',college:'学院 / 院系',major:'专业',class_name:'班级',status:'状态',score_rate:'得分率'};
const required = ['student_id','name','college','class_name'];
const actionNames = {new:'新增',updated:'更新基础信息',unchanged:'无变化',skipped_existing:'跳过已有学生'};

export function shell(S) {
  const entries = [['import','upload','名单导入'],['score-import','file','导入成绩'],['scores','chart','成绩明细'],['overview','layers','数据总览']];
  return `<div class="layout">
    <aside class="sidebar">
      <a class="brand" href="#import" aria-label="学绩首页"><div class="brand-mark">${icon('cap')}</div><div class="brand-text"><div class="brand-name">学绩</div><div class="brand-sub">学生成绩管理</div></div></a>
      <nav class="navigation" aria-label="主导航">${entries.map(([key,i,label])=>`<a class="nav-link ${S.view===key?'active':''}" href="#${key}" data-nav="${key}" title="${label}" ${S.view===key?'aria-current="page"':''}>${icon(i)}<span class="nav-text">${label}</span></a>`).join('')}</nav>
    </aside>
    <div class="workspace"><main class="main ${S.view==='scores'?'score-main':''}" id="main" tabindex="-1">${renderView(S)}</main></div>
  </div>`;
}
export function renderView(S) {
  const content = ({import:importView,'score-import':scoreImportView,scores:scoresView,overview:overviewView,help:helpView})[S.view] || importView;
  return content(S);
}
function historyCard(S) {
  return `<section class="card history"><div class="card-header"><div class="section-title">${icon('clock')}<h2>最近导入记录</h2></div><span class="small-muted">保留最近 50 批，当前显示 ${Math.min(S.history.length,5)} 批</span></div>${S.history.length?`<div class="table-scroll"><table><thead><tr><th>导入文件</th><th>导入时间</th><th>新增</th><th>更新 / 无变化</th><th>结果</th></tr></thead><tbody>${S.history.slice(0,5).map(b=>`<tr><td><div class="file-inline" title="${h(b.filename)}">${icon('file')}<span>${h(b.filename)}</span></div></td><td>${h(date(b.created_at))}</td><td class="text-success">+${num(b.new_count)}</td><td>${num(b.updated_count)} / ${num(b.unchanged_count+b.skipped_count)}</td><td><span class="badge success">${icon('check')}入库完成</span></td></tr>`).join('')}</tbody></table></div>`:blank('还没有导入记录','上传第一份学生名单，开始建立你的数据工作台。')}</section>`;
}
function uploadStart(S) {
  return `<div class="dropzone ${S.busy?'busy':''}" id="dropzone">
    <div class="upload-symbol">${icon('upload')}</div>
    <h3>${S.busy?'正在读取工作簿…':'将学生名单拖到这里，或点击上传'}</h3>
    <p>名单需包含学号、姓名、学院 / 院系和班级</p>
    <button class="btn primary ${S.busy?'spin':''}" data-action="choose-file" ${S.busy?'disabled':''}>${icon(S.busy?'refresh':'upload')}${S.busy?'正在识别表头':'选择学生名单'}</button>
    <div class="dropzone-foot"><span class="format-pill">XLS</span><span class="format-pill">XLSX</span><span>单个文件最大 20 MB</span></div>
  </div>`;
}
function mappingForm(S) {
  const sh = S.upload.sheets[S.sheetIndex];
  return `<div class="uploaded-file">${icon('file')}<div class="file-content"><div class="file-name">${h(S.upload.filename)}</div><div class="file-meta">${(S.upload.size/1024).toFixed(1)} KB · 已识别 ${S.upload.sheets.length} 张工作表 · 当前表头后 ${num(sh.rows)} 行</div></div><button class="icon-btn" data-action="reset-import" title="移除文件" ${S.busy?'disabled':''}>${icon('close')}</button></div><div class="form-row"><div class="form-field"><label for="sheet-select">选择工作表</label><select id="sheet-select" ${S.busy?'disabled':''}>${S.upload.sheets.map((x,i)=>`<option value="${i}" ${S.sheetIndex===i?'selected':''}>${h(x.name)}${x.hidden?'（隐藏表）':''} · ${num(x.rows)} 行</option>`).join('')}</select></div><div class="form-field header-field"><label for="header-row">表头所在行</label><input id="header-row" type="number" min="1" max="60000" value="${h(S.headerDraft??S.headerRow)}" ${S.busy?'disabled':''}></div><button class="btn secondary small" data-action="apply-header" ${S.busy?'disabled':''}>重新识别</button></div><div class="section-caption">确认字段对应关系<span class="small-muted">左侧为系统字段，选项为原表列</span></div><div class="mapping">${Object.entries(fields).map(([key,label])=>`<div class="form-field"><label for="map-${key}">${h(label)} ${required.includes(key)?'<span>*</span>':'<em>选填</em>'}</label><select id="map-${key}" data-map="${key}" ${S.busy?'disabled':''}><option value="">${required.includes(key)?'请选择对应列':'不导入此字段'}</option>${sh.headers.map(c=>`<option value="${c.index}" ${S.mapping[key]===c.index?'selected':''}>${h(c.label)}（第 ${c.index+1} 列）</option>`).join('')}</select></div>`).join('')}</div><div class="divider"></div><div class="section-caption">遇到数据库里已有的学号时</div><div class="mode-options"><label class="mode-option ${S.mode==='update'?'selected':''}"><input type="radio" name="import-mode" value="update" ${S.mode==='update'?'checked':''} ${S.busy?'disabled':''}><div><b>更新基础信息</b><p>更新姓名与班级归属等；状态和得分率保持不变。</p></div></label><label class="mode-option ${S.mode==='skip'?'selected':''}"><input type="radio" name="import-mode" value="skip" ${S.mode==='skip'?'checked':''} ${S.busy?'disabled':''}><div><b>只新增，跳过已有学生</b><p>已有学号不作改动，仅将新学生加入数据库。</p></div></label></div><div class="info-box">${icon('info')}<span>只导入当前工作表的已映射字段。状态和得分率列只在新增学生时生效；普通数值 85.5 表示 85.5%，留空表示未录入。</span></div>`;
}
function importSuccess(S) {
  const b = S.committed;
  return `<div class="success-panel"><div class="success-symbol">${icon('check')}</div><h2>学生名单已写入数据库</h2><p>学院与班级已自动关联，你现在可以查看成绩明细。<br>已有状态和得分率已保留，未删除任何名单外学生。</p><div class="success-stats"><div><strong>${num(b.new_count)}</strong><span>新增学生</span></div><div><strong>${num(b.updated_count)}</strong><span>更新基础信息</span></div><div><strong>${num(b.unchanged_count+b.skipped_count)}</strong><span>保留不变</span></div></div><p class="small-muted">当前数据库：${num(S.stats.colleges)} 个学院 · ${num(S.stats.classes)} 个班级 · ${num(S.stats.students)} 名学生</p><div class="success-actions"><button class="btn primary" data-action="goto-scores">查看成绩明细${icon('arrow')}</button><button class="btn secondary" data-action="reset-import">继续导入名单</button></div></div>`;
}
function previewView(S) {
  const P=S.preview, s=P.summary;
  return `<section class="card preview-section" id="preview"><div class="card-header"><div><div class="section-title">${icon('check')}<h2>导入预览与校验</h2></div><p class="card-subtitle">来自原表的 ${num(s.source_rows)} 行数据，识别 ${num(s.colleges)} 个学院、${num(s.classes)} 个班级</p></div><span class="badge ${s.error_count?'absent':'success'}">${s.error_count?'需要修正':'校验通过'}</span></div><div class="preview-summary">${[['new','新增学生'],['updated','更新信息'],['unchanged','无变化'],['skipped_existing','跳过已有'],['error_count','错误行']].map(([k,t])=>`<div class="preview-count ${k==='error_count'&&s[k]?'error':''}"><strong>${num(s[k])}</strong>${t}</div>`).join('')}</div>${P.errors.length?`<div class="card-body"><div class="info-box error">${icon('alert')}<span>发现 ${num(s.error_count)} 行错误，确认导入已禁用。修正原表后重新上传；不会跳过错误行偷偷导入部分名单。</span></div><div class="table-scroll"><table><thead><tr><th>Excel 行号</th><th>学号</th><th>问题说明</th></tr></thead><tbody>${P.errors.map(e=>`<tr><td>${e.row_number}</td><td class="mono">${h(e.student_id)}</td><td>${h(e.message)}</td></tr>`).join('')}</tbody></table></div><a class="btn secondary small" href="/api/imports/${h(P.preview_id)}/errors.csv" download>${icon('download')}下载全部错误记录</a></div>`:''}<div class="table-scroll"><table><thead><tr><th>原表行号</th><th>学号</th><th>姓名</th><th>学院</th><th>班级</th><th>入库动作</th><th>状态</th><th>得分率</th></tr></thead><tbody>${P.sample.map(r=>`<tr><td class="row-index">${r.row_number}</td><td class="mono">${h(r.student_id)}</td><td>${h(r.name)}</td><td>${h(r.college)}</td><td>${h(r.class_name)}</td><td><span class="badge ${r.action==='new'?'success':''}" title="${h(r.changes.map(c=>`${c.field}：${c.before} → ${c.after}`).join('；'))}">${h(actionNames[r.action])}</span></td><td>${badge(r.status)}</td><td>${r.score_rate===null?'<span class="not-recorded">未录入</span>':percent(r.score_rate)}</td></tr>`).join('')}</tbody></table></div><div class="table-caption">这里只预览前 ${P.sample.length} 条有效数据；确认后处理全部 ${num(s.valid_rows)} 条有效记录。学号不会按长度自动补齐。</div><div class="card-body"><div class="preview-notes">${P.warnings.map(w=>`<p>${h(w)}</p>`).join('')}</div>${s.name_conflict_count?`<div class="confirmation"><strong>发现 ${num(s.name_conflict_count)} 条同学号、不同姓名的记录</strong><p>请核对是否确为姓名修正，而不是错误使用了他人的学号。</p><div class="table-scroll"><table><thead><tr><th>学号</th><th>原姓名</th><th>新姓名</th></tr></thead><tbody>${P.name_conflicts.map(c=>`<tr><td class="mono">${h(c.student_id)}</td><td>${h(c.before)}</td><td>${h(c.after)}</td></tr>`).join('')}</tbody></table></div><label><input id="allow-name-changes" type="checkbox" ${S.allowNames?'checked':''}>我已核对以上姓名变更，允许更新（超过 50 条时请在原表中逐一核对）</label></div>`:''}</div><div class="card-footer"><p>${icon('shield')}确认后通过一次事务写入；不清空数据库。</p><button class="btn primary ${S.busy?'spin':''}" data-action="commit" ${S.busy||!P.can_commit||(s.name_conflict_count&&!S.allowNames)?'disabled':''}>${icon(S.busy?'refresh':'database')}${S.busy?'正在保存到 SQLite…':`确认导入 ${num(s.valid_rows)} 条记录`}</button></div></section>`;
}
export function importView(S) {
  const step=S.committed?3:S.preview?3:S.upload?2:1;
  return pageHeading('ROSTER IMPORT','名单导入','从一份学生名单开始，自动建立「学院 → 班级 → 学生」的数据层级。',`${templateLink()}<button class="btn secondary" data-action="goto-scores">${icon('chart')}查看成绩</button>`) + `<div class="roster-import-workflow">
    <section class="card roster-import-card">
      <div class="card-header"><div class="section-title">${icon('upload')}<h2>导入学生名单</h2></div></div>
      <div class="steps">${['选择文件','识别与预览','确认入库'].map((t,i)=>`${i?'<span class="step-line"></span>':''}<div class="step ${step===i+1?'active':step>i+1?'done':''}"><span class="step-circle">${step>i+1?icon('check'):i+1}</span><span>${t}</span></div>`).join('')}</div>
      ${S.committed?importSuccess(S):`<div class="card-body">
        <input type="file" id="file-input" accept=".xls,.xlsx" class="sr-only" aria-label="选择学生名单 Excel 文件">
        ${S.upload?mappingForm(S):uploadStart(S)}
        ${S.importError?`<div class="info-box error" role="alert">${icon('alert')}<span>${h(S.importError)}</span></div>`:''}
      </div><div class="card-footer"><button class="btn primary ${S.busy&&S.upload?'spin':''}" data-action="preview" ${!S.upload||S.busy||required.some(k=>S.mapping[k]===undefined)?'disabled':''}>${icon(S.busy?'refresh':'arrow')}${S.busy&&S.upload?'正在校验全部数据…':'生成导入预览'}</button></div>`}
    </section>
    ${S.preview?previewView(S):''}
  </div>${historyCard(S)}`;
}
function selectedContext(S) {
  const college = S.hierarchy.find(c=>c.id===S.filters.college_id);
  const cl = college?.classes.find(c=>c.id===S.filters.class_id);
  return {college, cl};
}
function scopeCheck(S,kind,id,ids,label) {
  const count=ids.filter(value=>S.exportClasses.has(value)).length;
  let state='false';
  if(kind==='all')state=S.exportAll&&ids.length?'true':'false';
  else if(count&&count===ids.length)state='true';
  else if(count)state='mixed';
  return `<button type="button" class="scope-check" role="checkbox" aria-checked="${state}" aria-label="勾选${h(label)}" data-action="check-scope" data-scope="${kind}" data-id="${id}" ${!ids.length||S.exportBusy?'disabled':''}>${icon(state==='mixed'?'minus':'check')}</button>`;
}
function treeView(S) {
  const needle=S.treeSearch.trim().toLowerCase();
  const sorting=S.treeSort||{key:'name',order:'asc'};
  const compare=(a,b)=>{
    const result=sorting.key==='name'?a.name.localeCompare(b.name,'zh-CN'):Number(a.count)-Number(b.count);
    return (sorting.order==='asc'?result:-result)||a.id-b.id;
  };
  const sortButton=(key,label)=>`<button class="tree-sort-button ${sorting.key===key?'active':''}" data-action="tree-sort" data-sort="${key}" aria-label="学院与班级按${label}排序" aria-pressed="${sorting.key===key}">${label}${icon(sorting.key===key?(sorting.order==='asc'?'arrowup':'down'):'sort')}</button>`;
  return `<aside class="card organization" id="score-directory"><div class="card-header"><div class="section-title">${icon('layers')}<h2>学院与班级</h2></div><span class="small-muted">${num(S.stats.colleges)} 个学院</span></div><div class="directory-sortbar"><span>排序</span>${sortButton('name','名称')}${sortButton('count','人数')}</div><div class="search-box tree-search">${icon('search')}<input id="tree-search" type="search" value="${h(S.treeSearch)}" placeholder="搜索学院或班级" aria-label="搜索学院或班级"></div><div class="tree-body" id="score-tree-body"><div class="tree-scope-row">${scopeCheck(S,'all',0,S.hierarchy.flatMap(co=>co.classes.map(cl=>cl.id)),'全校')}<button class="tree-all ${!S.filters.college_id?'selected':''}" data-action="select-all">${icon('users')}全部学生<span class="tree-count">${num(S.stats.students)}</span></button></div>${[...S.hierarchy].sort(compare).filter(co=>!needle||co.name.toLowerCase().includes(needle)||co.classes.some(c=>c.name.toLowerCase().includes(needle))).map(co=>{
    const open=S.openColleges.has(co.id)||!!needle;
    const classes=[...co.classes].sort(compare).filter(c=>!needle||co.name.toLowerCase().includes(needle)||c.name.toLowerCase().includes(needle));
    return `<section class="tree-group" data-row-key="college-${co.id}"><div class="tree-college-row">${scopeCheck(S,'college',co.id,co.classes.map(cl=>cl.id),co.name)}<button class="icon-btn tree-toggle ${open?'open':''}" data-action="toggle-college" data-id="${co.id}" aria-label="${open?'收起':'展开'}${h(co.name)}" aria-expanded="${open}">${icon('chevron')}</button><button class="tree-college-label ${S.filters.college_id===co.id&&!S.filters.class_id?'selected':''}" data-action="select-college" data-id="${co.id}" title="${h(co.name)}"><span>${h(co.name)}</span><span class="tree-count">${num(co.count)}</span></button></div>${open?`<div class="tree-classes">${classes.map(c=>`<div class="tree-scope-row">${scopeCheck(S,'class',c.id,[c.id],c.name)}<button class="tree-class ${S.filters.class_id===c.id?'selected':''}" data-action="select-class" data-college="${co.id}" data-id="${c.id}" title="${h(c.name)}">${icon('folder')}<span>${h(c.name)}</span><span class="tree-count">${num(c.count)}</span></button></div>`).join('')}</div>`:''}</section>`;
  }).join('')}${!S.hierarchy.length?'<p class="small-muted" style="padding:12px">导入名单后，这里会自动生成学院与班级。</p>':''}</div><div class="tree-selection-footer"><span>已勾选 <b>${S.exportClasses.size}</b> 个班级</span><button class="btn ghost small" data-action="clear-export-selection" ${!S.exportClasses.size||S.exportBusy?'disabled':''}>清空</button></div></aside>`;
}
function studentCheckbox(S,id=null) {
  let state='false';
  if(id!==null)state=studentIsChecked(S,id)?'true':'false';
  else if(S.exportAllStudents&&!S.exportExcludedStudents.size)state='true';
  else if(selectedStudentCount(S))state='mixed';
  const label=id===null?`全选当前筛选的全部 ${num(S.students.total)} 名学生（包含其他页）`:`勾选学号 ${id}`;
  const disabled=S.exportBusy||S.listLoading||!!S.listError||(id===null&&!S.students.total);
  return `<button type="button" class="scope-check student-check" role="checkbox" aria-checked="${state}" aria-label="${h(label)}" title="${h(label)}" data-action="${id===null?'check-all-students':'check-student'}" ${id===null?'':`data-student="${h(id)}"`} ${disabled?'disabled':''}>${icon(state==='mixed'?'minus':'check')}</button>`;
}
function scoreRow(S, r, index) {
  const unfinished=['未开始','进行中'].includes(r.status);
  const missing=!hasValidScore(r);
  const emptyLabel=unfinished?'未结算':'未录入';
  const rate=missing?`<span class="not-recorded">${emptyLabel}</span>`:`<span class="rate-value">${percent(r.score_rate)}</span><div class="rate-track"><span style="width:${Number(r.score_rate)}%"></span></div>`;
  const score=missing?'<span class="score-empty">—</span>':`<strong class="score-value">${Number(r.score_rate).toLocaleString('zh-CN',{maximumFractionDigits:2})}<small>分</small></strong>`;
  return `<tr class="${studentIsChecked(S,r.student_id)?'selected-row':''}" data-student-row="${h(r.student_id)}"><td class="row-selection-column">${studentCheckbox(S,r.student_id)}</td><td><span class="row-index">${index}</span></td><td class="mono">${h(r.student_id)}</td><td><div class="student-cell"><b title="${h(r.name)}">${h(r.name)}</b></div></td><td class="cell-class"><div title="${h(r.class_name)}">${h(r.class_name)}</div></td><td>${badge(r.status)}</td><td class="rate-cell">${rate}</td><td class="score-cell" title="${unfinished?'非最终得分':''}">${score}</td><td><button class="edit-button" data-action="edit-student" data-student="${h(r.student_id)}" ${S.listLoading||S.listError?'disabled':''}>${icon('edit')}编辑</button></td></tr>`;
}
function scoreTable(S) {
  const D=S.students;
  const sort=(key,label)=>`<button class="sort-button" data-action="sort" data-sort="${key}">${h(label)}${S.filters.sort===key?icon(S.filters.order==='asc'?'arrowup':'down'):icon('down')}</button>`;
  let body;
  if (S.listLoading&&!S.studentsLoaded) body='<tr><td colspan="9"><div class="empty" role="status"><p>正在读取成绩…</p></div></td></tr>';
  else if (S.listError&&!S.studentsLoaded) body=`<tr><td colspan="9"><div class="empty" role="alert"><h3>成绩读取失败</h3><p>${h(S.listError)}</p><button class="btn secondary small" data-action="refresh-students">重新读取</button></div></td></tr>`;
  else if (D.items.length) body=D.items.map((r,i)=>scoreRow(S,r,(D.page-1)*D.page_size+i+1)).join('');
  else body=`<tr><td colspan="9">${blank(S.stats.students?'没有符合条件的学生':'还没有学生名单',S.stats.students?'调整搜索词或筛选条件，再试一次。':'先在名单导入页上传你的学生名单。',S.stats.students?'<button class="btn secondary small" data-action="clear-filters">清除筛选</button>':'<a class="btn primary small" href="#import">导入学生名单</a>')}</td></tr>`;
  const disabled=S.listLoading||!!S.listError;
  const start=D.total?(D.page-1)*D.page_size+1:0;
  const end=Math.min(D.page*D.page_size,D.total);
  return `<div class="table-scroll score-table-scroll" id="score-table-area" aria-busy="${S.listLoading}"><table class="students-table"><caption class="sr-only">当前选择范围的学生成绩</caption><thead><tr><th scope="col" class="row-selection-column">${studentCheckbox(S)}</th><th scope="col">序号</th><th scope="col">${sort('student_id','学号')}</th><th scope="col">${sort('name','姓名')}</th><th scope="col">${sort('class_name','班级')}</th><th scope="col">状态</th><th scope="col">${sort('score_rate','得分率')}</th><th scope="col">${sort('score_rate','得分')}</th><th scope="col">操作</th></tr></thead><tbody>${body}</tbody></table></div><div class="pagination" id="score-pagination"><span class="page-range">${`第 ${num(start)}–${num(end)} 条 · 共 ${num(D.total)} 条`}${selectedStudentCount(S)?`<span class="student-selection-summary">已勾选 ${num(selectedStudentCount(S))} 人<button class="btn ghost small" data-action="clear-student-selection" ${S.exportBusy?'disabled':''}>取消勾选</button></span>`:''}</span><div class="page-controls"><select id="page-size" class="filter-select" aria-label="每页条数" ${disabled?'disabled':''}><option value="auto" ${S.pageMode==='auto'?'selected':''}>自适应 · ${S.filters.page_size} 条</option>${[10,20,50,100].map(n=>`<option value="${n}" ${S.pageMode!=='auto'&&S.filters.page_size===n?'selected':''}>${n} 条 / 页</option>`).join('')}</select><button class="icon-btn" data-action="prev-page" aria-label="上一页" ${D.page<=1||disabled?'disabled':''}>${icon('back')}</button><span class="page-number">${D.page} / ${D.pages}</span><button class="icon-btn" data-action="next-page" aria-label="下一页" ${D.page>=D.pages||disabled?'disabled':''}>${icon('chevron')}</button><form id="page-jump-form" class="page-jump"><label for="page-jump">跳至</label><input id="page-jump" type="number" min="1" max="${D.pages}" step="1" value="${D.page}" aria-label="跳转页码" required ${disabled?'disabled':''}><span>页</span><button type="submit" class="btn secondary small" ${disabled?'disabled':''}>跳转</button></form></div></div>`;
}

export function scoresView(S) {
  const {college,cl}=selectedContext(S);
  const context=cl?cl.name:college?.name||'全校';
  const total=cl?.count??college?.count??S.stats.students;
  const D=S.students;
  const pending=!S.studentsLoaded;
  const value=v=>pending?'—':v;
  const countValue=v=>pending||v===null||v===undefined?'—':num(v);
  const studentCount=selectedStudentCount(S);
  const serviceMessage=S.directoryStatsRecovered?'已只读补算统计（不合格含无有效成绩人员）；当前启动窗口仍是旧后端，不合格筛选与导出需要在启动窗口按 Ctrl+C 后重新启动网站。':DIRECTORY_UPDATE_MESSAGE;
  return `<div class="students-layout scores-workspace" id="score-workspace">${treeView(S)}<section class="card students-panel" id="score-panel" aria-label="成绩明细"><div class="card-header" id="score-list-header"><div class="list-title"><h1>${h(context)}<span class="count-pill"><span>总人数 </span><b>${num(total)}</b><small> 人</small></span></h1></div><button class="icon-btn ${S.listLoading?'spin':''}" data-action="refresh-students" aria-label="刷新成绩" ${S.listLoading?'disabled':''}>${icon('refresh')}</button></div><div class="selection-stats" id="score-statistics"><div title="有效得分严格大于90分；恰好90分不计入此项"><span>合格人数 <small>＞90分</small></span><strong data-stat="qualified">${countValue(D.qualified)}<small>人</small></strong></div><div title="低于90分，或未录入、未开始/进行中等无有效成绩人员均计入；有有效成绩且恰好90分仍单独统计"><span>不合格人数 <small>含无成绩</small></span><strong data-stat="below-90">${countValue(D.below_90)}<small>人</small></strong></div><div title="只计入0–100的有效数值成绩；空值、未录入和未结算记录不计入，实际0分计入"><span>有效得分人数 <small>有数值</small></span><strong data-stat="settled">${countValue(D.valid_scores??D.settled)}<small>人</small></strong></div><div title="仅按有效数值成绩计算平均值"><span>平均得分率 <small>仅有效得分</small></span><strong data-stat="average">${value(percent(D.average_rate))}</strong></div></div>${S.directoryOutdated?`<div class="directory-service-alert" role="alert">${icon('alert')}<span>${S.directoryStatsError?h(S.directoryStatsError)+'<br>':''}${h(serviceMessage)}</span></div>`:''}<div class="list-tools" id="score-list-tools"><div class="search-box">${icon('search')}<input id="student-search" type="search" value="${h(S.filters.q)}" placeholder="搜索学号或姓名" maxlength="120" aria-label="搜索学号或姓名"></div><select class="filter-select" id="status-filter" aria-label="按状态筛选"><option value="">全部状态</option>${statuses.map(st=>`<option value="${st}" ${S.filters.status===st?'selected':''}>${st}</option>`).join('')}</select><select class="filter-select" id="score-filter" aria-label="按得分率录入情况筛选">${[['all','全部得分率'],['recorded','有数值得分率'],['missing','得分率为空'],['below_90','不合格（含无成绩）']].map(([v,t])=>`<option value="${v}" ${S.filters.score_filter===v?'selected':''}>${t}</option>`).join('')}</select><details class="export-menu"><summary class="btn primary" aria-label="导出成绩">${icon('download')}${S.exportBusy?'导出中…':'导出'}${studentCount||S.exportClasses.size?`<span class="export-count">${num(studentCount||S.exportClasses.size)}</span>`:''}${icon('down')}</summary><div class="export-popover"><p>${studentCount?`已勾选 ${num(studentCount)} 名学生<br>`:''}${S.exportClasses.size?`目录勾选 ${S.exportClasses.size} 个班级`:`当前范围：${h(context)}`}</p><button class="export-option" data-action="score-export-students" ${!studentCount||S.exportBusy||S.listLoading||S.listError?'disabled':''}>${icon('users')}导出勾选学生${studentCount?`（${num(studentCount)} 人）`:''}</button><button class="export-option" data-action="score-export" ${S.exportBusy||S.listLoading||S.listError?'disabled':''}>${icon('layers')}${S.exportClasses.size?'导出勾选学院 / 班级':'导出当前范围'}</button><button class="export-option" data-action="score-export-below" ${S.exportBusy||S.listLoading||S.listError?'disabled':''} title="包含低于90分、未录入、未开始/进行中等无有效成绩人员；优先按勾选学生，否则按目录勾选或当前筛选范围">${icon('download')}导出不合格（含无成绩）</button><small>Excel · 每个班级一个工作表${!studentCount?'<br>勾选学生后可导出指定人员':''}</small></div></details></div><div id="scores-update-status" class="data-update-status ${S.listError?'update-error':''}" role="status" aria-live="polite">${S.listLoading?`${icon('refresh')}<span>${S.studentsLoaded?`正在更新，暂时显示${h(S.listContext)}的上次结果`:'正在读取成绩…'}</span>`:S.listError?`<span>更新失败：${h(S.listError)}${S.studentsLoaded?'；已保留上次结果':''}</span>${S.studentsLoaded?'<button class="btn secondary small" data-action="refresh-students">重试</button>':''}`:''}</div>${scoreTable(S)}</section></div>`;
}

export function overviewView(S) {
  const sort=S.overviewSort||{key:'students',order:'desc'};
  const details=[...(S.stats.colleges_detail||[])].sort((a,b)=>{
    const comparison=sort.key==='name'?a.name.localeCompare(b.name,'zh-CN'):
      Number(a[sort.key]||0)-Number(b[sort.key]||0);
    return (sort.order==='asc'?comparison:-comparison)||a.id-b.id;
  });
  const heading=(key,label)=>{
    const active=sort.key===key;
    let nextOrder=key==='name'?'升序':'降序';
    let direction='none', sortIcon='sort';
    if(active){
      nextOrder=sort.order==='asc'?'降序':'升序';
      direction=sort.order==='asc'?'ascending':'descending';
      sortIcon=sort.order==='asc'?'arrowup':'down';
    }
    return `<th scope="col" aria-sort="${direction}"><button id="overview-sort-${key}" class="sort-button ${active?'is-active':''}" data-action="overview-sort" data-sort="${key}" aria-label="${h(label)}，点击按${nextOrder}排序">${h(label)}${icon(sortIcon)}</button></th>`;
  };
  const qualifiedMessage=S.overviewError?'读取失败':'统计中…';
  const count=value=>value==null?qualifiedMessage:num(value);
  return `<div class="overview-workspace" aria-label="数据总览">
    ${metrics(S.stats,qualifiedMessage)}
    ${S.overviewError?`<div class="overview-count-error" role="alert"><span>合格人数读取失败：${h(S.overviewError)}</span><button class="btn secondary small" data-action="retry-overview">重新读取</button></div>`:''}
    <div class="overview-grid">
      <section class="card overview-colleges" aria-labelledby="overview-colleges-title">
        <div class="card-header"><div><h2 id="overview-colleges-title">学院数据</h2><p class="card-subtitle">点击表头排序，点击学院查看成绩</p></div><span class="count-pill">${num(details.length)} 个学院</span></div>
        ${details.length?`<div class="table-scroll overview-table-scroll" tabindex="0" role="region" aria-label="学院数据表，可横向滚动"><table class="overview-table"><caption class="sr-only">各学院人数统计，合格人数按得分率大于等于90%计算</caption><thead><tr>${heading('name','学院名称')}${heading('classes','班级数')}${heading('students','学生人数')}${heading('recorded','已录入人数')}${heading('qualified','合格人数')}</tr></thead><tbody>${details.map(co=>`<tr>
          <td><button class="college-name" data-action="overview-college" data-id="${co.id}" title="查看${h(co.name)}成绩"><span>${h(co.name)}</span></button></td>
          <td>${num(co.classes)}</td><td>${num(co.students)}</td>
          <td>${num(co.recorded)}</td><td><strong class="qualified-count">${count(co.qualified)}</strong></td>
        </tr>`).join('')}</tbody></table></div>`:blank('还没有学院数据','导入学生名单后自动生成统计。','<a class="btn primary" href="#import">导入学生名单</a>')}
      </section>
      <section class="card overview-status" aria-labelledby="overview-status-title">
        <div class="card-header"><div><h2 id="overview-status-title">考试与登记状态</h2><p class="card-subtitle">共 ${num(S.stats.students)} 名学生</p></div></div>
        <div class="card-body">${S.stats.statuses.map(x=>`<div class="distribution-row"><div class="distribution-label">${badge(x.status)}<strong>${num(x.count)} <span class="small-muted">人</span></strong></div><div class="distribution-track"><span style="width:${S.stats.students?x.count/S.stats.students*100:0}%"></span></div></div>`).join('')}</div>
      </section>
    </div>
  </div>`;
}
export function helpView(S) {
  const storagePath = S.health?.local_only === false ? '/var/lib/student-score/students.sqlite3' : 'data/students.sqlite3';
  return pageHeading('GETTING STARTED','使用说明','先把学生基础数据管理好，再逐步扩展考试、课程与成绩统计。',templateLink())+`<div class="help-grid"><section class="card help-section"><h2>${icon('upload')}01 · 导入一份名单</h2><p>在“名单导入”选择 <code>.xls</code> 或 <code>.xlsx</code> 文件。系统自动寻找表头，识别学号、姓名、院系、班级，以及选填的性别和专业。多工作表文件每次选择一张。</p><p>核对字段映射后生成预览。无错误时确认入库，学院与班级会自动建立。同一文件出现重复学号时必须先修正。</p><p>数字型学号无法恢复文件里已经丢失的前导零；学号列建议保存为文本。此版本支持常规 BIFF8 XLS 和普通 XLSX 表格，不支持加密文件、宏执行或公式求值。</p>${templateLink('下载含虚构样例的模板')}</section><section class="card help-section"><h2>${icon('layers')}02 · 学院 → 班级 → 学生</h2><p>在“成绩明细”左侧展开学院，查看下属班级。点击班级，右侧自动列出对应学生；也可以选择整个学院或全部学生。</p><p>学院名称唯一；班级以“学院 + 班级名称”区分；学生只用学号唯一识别。同名学生不会被合并，不同学院的同名班级也不会混在一起。</p><p>当前名单导入是增量新增 / 更新，不是全量替换。未出现在新表中的旧学生不会自动删除。</p></section><section class="card help-section"><h2>${icon('edit')}03 · 状态和得分率</h2><p>点击学生行末的“编辑”，修改成绩登记状态或得分率。状态包括 <code>待录入</code>、<code>未开始</code>、<code>进行中</code>、<code>已完成</code>、<code>已录入</code>、<code>缺考</code>、<code>免考</code>，不代表学籍状态。</p><p>得分率保存为 0–100 的百分数数值，例如 <code>85.5</code> 表示 85.5%。留空表示“未录入”，输入 <code>0</code> 表示真实的 0%，二者不同。两项独立保存，不相互推断。</p><p>Excel 百分比格式的 85% 会识别为 85；普通数值 <code>0.85</code> 会识别为 0.85%，不会擅自放大 100 倍。</p></section><section class="card help-section"><h2>${icon('shield')}04 · 重复导入与校验</h2><p>选择“更新基础信息”时，已有学号只更新姓名、性别、专业、学院和班级，始终保留状态和得分率。选择“只新增”时，已有学生完全不变。</p><p>同一学号对应不同姓名时，需要人工核对并确认。任何错误都会阻止整批写入；预览之后数据库被其他窗口修改，也需要重新生成预览。</p><p>导入预览有效期为 60 分钟。成功入库后临时 Excel 文件会被清理；修改记录保留在数据库。</p></section><section class="card help-section"><h2>${icon('database')}05 · 数据保存与备份</h2><p>业务数据保存在 <code>${storagePath}</code>，不是浏览器缓存。刷新页面、关闭浏览器或正常重启服务，不会清空数据。</p><p>使用下方按钮下载一致性备份。恢复时先停止服务，再将备份放到 <code>${storagePath}</code>；云服务器恢复步骤见 <code>deploy/README.md</code>。请不要在服务运行中直接替换数据库。</p><a class="btn secondary" href="/api/database/backup" download>${icon('download')}下载 SQLite 数据库备份</a></section><section class="card help-section"><h2>${icon('lock')}06 · 访问范围</h2><p>${S.health?.local_only === false ? '云服务器通过独立域名和 HTTPS 访问；后端仅监听服务器本机地址，Nginx 白名单仅允许指定公网 IP。公网 IP 变化时需更新白名单。' : '当前为本机模式，仅监听 127.0.0.1。如需在云服务器使用，请按 deploy/README.md 配置独立域名、HTTPS 和 Nginx 白名单。'}</p><p>本项目没有账号登录或多人角色权限。每位学生维护一组当前成绩；批次记录不是多考试模型，独立考试、课程加权成绩和排名尚未实现。</p><p class="help-note">完整名单、成绩和备份属于真实学生资料，请只在获授权的设备中保存，不要上传到公开代码仓库。</p></section><section class="card help-section help-wide"><h2>07 · 成绩文件清洗与导入</h2><p>进入“导入成绩”选择 CSV、TSV、XLS 或 XLSX。CSV 自动识别 UTF-8、GBK / GB18030 和带 BOM 的 UTF-16。只映射学号、状态、得分率等所需字段，不使用易班 ID 匹配。</p><p>清除学号前的反引号、单引号、全角及明确的 .0 尾缀，保留前导零。得分率默认按 0–100 读取；“92 / 92.0%”提取右侧百分比。两列同时存在但不一致、姓名冲突或重复学号冲突，均列入异常，不自动猜测。</p><p>未开始 / 进行中的占位 0 分默认置空，原值保留在批次明细。未匹配学号不会新建学生；必须确认后才可跳过异常行。默认不允许未完成记录覆盖已完成的成绩。</p><p>成功导入后可在“成绩明细”按学院、班级和状态查看。历史记录可下载完整清洗明细；撤销必须整批通过版本校验，有后续修改时会拒绝覆盖。跨考试或较早报表导入前请核对目标，此版不自动判断哪次考试较新。</p></section><section class="card help-section help-wide"><h2>${icon('book')}开发与扩展</h2><p>当前使用原生 JavaScript 模块与 CSS 构建界面，Python FastAPI 提供接口，SQLite 负责持久化。没有前端构建步骤，不需要安装 Node.js。全部页面、接口和导入代码均在源码包中。</p><div class="doc-path">web/　界面与交互　　server/app.py　接口与事务\nserver/score_import.py　成绩清洗与精确匹配
server/score_api.py　成绩导入接口、审计与撤销
server/roster.py　名单字段映射与校验\nserver/excel_reader.py　只读 Excel 解析　　server/schema.sql　数据库结构\ntests/　自动化测试　　docs/　结构说明　　deploy/　云服务器部署</div></section></div>`;
}
export function editDialog(student, saving=false, error='') {
  return `<form id="student-edit-form"><div class="dialog-header"><div><p class="eyebrow">STUDENT RECORD</p><h2 id="dialog-title">编辑状态与得分率</h2><p>基础信息由名单导入维护，学号不可在此更改。</p></div><button type="button" class="icon-btn" data-action="close-dialog" aria-label="关闭">${icon('close')}</button></div><div class="dialog-body"><div class="student-profile"><span class="avatar">${h(student.name.slice(0,1))}</span><div><strong>${h(student.name)}</strong><p>${h(student.college_name)} · ${h(student.class_name)}</p></div></div><div class="form-field"><label for="edit-student-id">学号 · 唯一标识</label><input id="edit-student-id" class="mono" value="${h(student.student_id)}" readonly></div><div class="form-field"><label for="edit-status">成绩登记状态</label><select id="edit-status" ${saving?'disabled':''}>${statuses.map(s=>`<option value="${s}" ${student.status===s?'selected':''}>${s}</option>`).join('')}</select><span class="hint">状态是登记标记，不代表学籍状态；请按实际情况选择。</span></div><div class="form-field"><label for="edit-rate">得分率</label><div class="rate-input"><input id="edit-rate" type="number" min="0" max="100" step="0.01" inputmode="decimal" value="${student.score_rate===null?'':student.score_rate}" placeholder="未录入" ${saving?'disabled':''}><span>%</span></div><span class="hint">输入 0–100，支持两位小数；留空表示未录入，0 表示真实 0%。</span></div><div class="info-box">${icon('shield')}状态和得分率独立保存，后续重复导入名单不会覆盖这两项。</div><div class="form-error" id="edit-error" role="alert">${h(error)}</div></div><div class="dialog-footer"><button type="button" class="btn secondary" data-action="close-dialog" ${saving?'disabled':''}>取消</button><button type="submit" class="btn primary" id="save-student" ${saving?'disabled':''}>${icon('check')}${saving?'正在保存…':'保存修改'}</button></div></form>`;
}
