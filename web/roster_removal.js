import {h, num, date, icon, percent, blank} from './components.js';
import {newScoreState} from './score_views.js';

export const newRemovalState=()=>({upload:null,sheetIndex:0,headerRow:1,headerDraft:null,preview:null,committed:null,busy:false,error:'',confirm:false,confirmation:'',history:null});
export function rosterOperationTabs(S){
  return `<div class="tabs roster-operation" role="group" aria-label="名单操作">${[['import','导入名单'],['remove','剔除名单']].map(([key,title])=>`<button type="button" class="tab ${S.rosterOperation===key?'active':''}" aria-pressed="${S.rosterOperation===key}" data-action="roster-operation" data-operation="${key}" ${S.busy||S.score.busy||S.removal.busy?'disabled':''}>${title}</button>`).join('')}</div>`;
}
function historyView(R){
  const H=R.history;
  return `<section class="card history removal-history"><div class="card-header"><h2>剔除记录</h2><span class="small-muted">${H?`共 ${num(H.total)} 批`:'尚未读取'}</span></div>${!H?`<div class="card-body"><button class="btn secondary" data-action="removal-history">读取剔除记录</button></div>`:H.items.length?`<div class="table-scroll"><table><thead><tr><th>名单文件</th><th>剔除时间</th><th>删除人员</th><th>未匹配学号</th><th>清理成绩明细</th></tr></thead><tbody>${H.items.map(b=>`<tr><td>${h(b.filename)}</td><td>${h(date(b.created_at))}</td><td>${num(b.deleted_count)}</td><td>${num(b.unmatched_count)}</td><td>${num(b.score_rows)}</td></tr>`).join('')}</tbody></table></div><div class="pagination"><span>第 ${H.page} / ${H.pages} 页 · 仅保留汇总和剔除学号，不保留被删姓名与成绩</span><div class="page-controls"><button class="btn secondary small" data-action="removal-history" data-page="${H.page-1}" ${R.busy||H.page<=1?'disabled':''}>上一页</button><button class="btn secondary small" data-action="removal-history" data-page="${H.page+1}" ${R.busy||H.page>=H.pages?'disabled':''}>下一页</button></div></div>`:blank('还没有剔除记录','确认剔除后在这里查看处理结果。')}</section>`;
}
function previewView(R){
  const P=R.preview,s=P.summary;
  return `<section class="card preview-section removal-preview" id="removal-preview"><div class="card-header"><h2>剔除预览与校验</h2><a class="btn secondary small" href="/api/removals/${h(P.preview_id)}/preview.csv" download>${icon('download')}下载完整核对表</a></div><div class="preview-summary">${[['unique_ids','去重学号'],['matched','将删除人员'],['unmatched','未匹配学号'],['duplicate_rows','重复行'],['error_count','错误行']].map(([k,t])=>`<div class="preview-count ${k==='error_count'&&s[k]?'error':''}"><strong>${num(s[k])}</strong>${t}</div>`).join('')}</div>${P.errors.length?`<div class="card-body"><div class="info-box error" role="alert">发现错误，整批剔除已禁用。修正名单后重新上传；不自动跳过错误行。</div><div class="table-scroll"><table><thead><tr><th>原表行号</th><th>A 列学号</th><th>错误原因</th></tr></thead><tbody>${P.errors.map(e=>`<tr><td>${e.row_number}</td><td>${h(e.student_id)}</td><td>${h(e.message)}</td></tr>`).join('')}</tbody></table></div></div>`:''}<div class="table-scroll"><table><thead><tr><th>原表行号</th><th>A 列学号</th><th>数据库姓名</th><th>学院</th><th>班级</th><th>当前得分率</th><th>处理结果</th></tr></thead><tbody>${P.sample.map(r=>`<tr><td>${r.row_number}</td><td class="mono">${h(r.student_id)}</td><td>${h(r.name??'—')}</td><td>${h(r.college_name??'—')}</td><td>${h(r.class_name??'—')}</td><td>${r.score_rate===null?'未录入':percent(r.score_rate)}</td><td><span class="badge ${r.matched?'absent':'neutral'}">${r.matched?'将删除':'仅登记剔除学号'}</span></td></tr>`).join('')}</tbody></table></div><div class="table-caption">页面预览前 ${P.sample.length} 个去重学号；确认后处理全部 ${num(s.unique_ids)} 个学号。完整核对表包含全部有效学号及错误。跳过 ${num(s.blank_rows)} 个空行、${num(s.repeated_headers)} 行重复表头。</div><div class="card-body removal-confirm"><div class="info-box warning">${icon('alert')}<span>将永久删除 ${num(s.matched)} 名学生的基础资料、当前成绩、${num(s.change_rows)} 条修改记录和 ${num(s.score_rows)} 条历次成绩导入明细（含异常记录）。名单外人员不变；空班级及空学院会清理。<br>全部 ${num(s.unique_ids)} 个学号会登记为已剔除，后续名单导入自动跳过。未匹配学号不会按姓名删除其他人员。涉及已清理人员的成绩批次不能再整批撤销。</span></div><p class="small-muted removal-note">本操作没有在线撤销。原 Excel、之前下载的备份不会被改动；SQLite 删除不等于磁盘级安全擦除。如需恢复保障，请先保存备份。</p><a class="btn secondary small" href="/api/database/backup" download>${icon('download')}删除前下载数据库备份</a><label class="check-option"><input id="removal-confirm" type="checkbox" ${R.confirm?'checked':''} ${R.busy||!P.can_commit?'disabled':''}><b>我已核对全部学号，确认永久删除并防止再次导入</b></label><div class="form-field"><label for="removal-confirmation">输入“确认剔除”后才能提交</label><input id="removal-confirmation" type="text" autocomplete="off" maxlength="4" value="${h(R.confirmation)}" ${R.busy||!P.can_commit?'disabled':''}></div></div><div class="card-footer"><p>${icon('shield')}按 A 列学号精确匹配，一次事务完成</p><button class="btn removal-danger" data-action="removal-commit" ${R.busy||!P.can_commit||!R.confirm||R.confirmation!=='确认剔除'?'disabled':''}>${R.busy?'正在剔除…':`确认剔除 ${num(s.matched)} 名学生`}</button></div></section>`;
}
export function removalView(S,heading){
  const R=S.removal,U=R.upload,sh=U?.sheets[R.sheetIndex];
  const supported=Number(S.health?.roster_removal_api_version||0)>=1;
  return heading+`<div class="roster-import-workflow"><section class="card roster-import-card removal-card"><div class="card-header"><h2>剔除学生名单</h2>${rosterOperationTabs(S)}</div>${R.committed?`<div class="success-panel"><div class="success-symbol">${icon('check')}</div><h2>剔除已完成</h2>${R.error?`<div class="info-box error" role="alert">${h(R.error)}<button class="btn secondary small" data-action="removal-refresh">重新读取统计</button></div>`:''}<p>已从数据库删除 ${num(R.committed.deleted_count)} 名学生，不再参与名单、成绩、统计或导出。<br>已登记 ${num(R.committed.unique_ids)} 个剔除学号，后续名单导入将自动跳过。</p><div class="success-actions"><button class="btn primary" data-action="goto-scores">查看成绩明细</button><button class="btn secondary" data-action="removal-reset">继续剔除</button></div></div>`:`<div class="card-body"><input type="file" id="removal-file-input" class="sr-only" accept=".xls,.xlsx" aria-label="选择剔除名单 Excel 文件">${!supported?`<div class="info-box error" role="alert">当前后端尚不支持剔除名单。请在原启动窗口重启服务，再按 Ctrl+F5 刷新；不会使用旧导入接口替代删除。</div>`:U?`<div class="uploaded-file">${icon('file')}<div class="file-content"><div class="file-name">${h(U.filename)}</div><div class="file-meta">表头后 ${num(sh.rows)} 行 · 仅按 A 列学号匹配</div></div><button class="icon-btn" data-action="removal-reset" aria-label="移除剔除文件" ${R.busy?'disabled':''}>${icon('close')}</button></div><div class="form-row"><div class="form-field"><label for="removal-sheet">选择工作表</label><select id="removal-sheet" ${R.busy?'disabled':''}>${U.sheets.map((s,i)=>`<option value="${i}" ${i===R.sheetIndex?'selected':''}>${h(s.name)}${s.hidden?'（隐藏表）':''} · ${num(s.rows)} 行</option>`).join('')}</select></div><div class="form-field header-field"><label for="removal-header">表头所在行</label><input id="removal-header" type="number" min="1" max="60000" value="${h(R.headerDraft??R.headerRow)}" ${R.busy?'disabled':''}></div><button class="btn secondary small" data-action="removal-header" ${R.busy?'disabled':''}>重新识别</button></div><div class="info-box">${icon('info')}<span>固定读取 A 列「${h(sh.headers.find(c=>c.index===0)?.label??'无表头')}」作为学号。姓名、学院、班级仅作原表参考，不参与绑定或删除。保留学号前导零，不自动补齐。</span></div>`:`<div class="dropzone" id="removal-dropzone"><div class="upload-symbol">${icon('users')}</div><h3>选择需要剔除的人员名单</h3><p>A 列须为学号，第一行为表头；也可选择其他表头行。支持 XLS / XLSX，最大 20 MB。上传和预览不会删除任何数据。</p><button class="btn primary" data-action="removal-choose" ${R.busy?'disabled':''}>${R.busy?'正在读取…':'选择剔除名单'}</button></div>`}${R.error?`<div class="info-box error" role="alert">${h(R.error)}</div>`:''}</div><div class="card-footer"><button class="btn primary" data-action="removal-preview" ${R.busy||!U||!supported?'disabled':''}>${R.busy?'正在校验…':'生成剔除预览'}</button></div>`}</section>${R.preview?previewView(R):''}</div>${historyView(R)}`;
}

export function installRemovalController(S,{api,render,toast,refreshBase,clearSelection}){
  const R=S.removal;
  const invalidate=()=>{R.preview=null;R.confirm=false;R.confirmation='';R.error='';};
  async function history(page=1){
    if(Number(S.health?.roster_removal_api_version||0)<1)return;
    R.history=await api(`/api/removals?page=${page}`);
  }
  async function run(work){
    if(R.busy)return;
    R.busy=true;R.error='';render();
    try{await work();}catch(e){R.error=e.message;toast(e.message,true);}finally{R.busy=false;render();}
  }
  async function upload(file){
    if(!file||R.busy||Number(S.health?.roster_removal_api_version||0)<1)return;
    if(!/\.(xls|xlsx)$/i.test(file.name)||file.size>20*1024*1024){toast('请选择不超过 20 MB 的 XLS / XLSX 名单。',true);return;}
    await run(async()=>{
      invalidate();R.upload=null;R.committed=null;
      const form=new FormData();form.append('file',file);
      R.upload=await api('/api/imports/analyze',{method:'POST',body:form});
      R.sheetIndex=R.upload.selected_sheet;R.headerRow=R.upload.sheets[R.sheetIndex].header_row;R.headerDraft=null;
    });
  }
  document.addEventListener('click',async event=>{
    const b=event.target.closest('[data-action]');if(!b||b.disabled)return;
    const action=b.dataset.action;
    if(action==='roster-operation'){
      if(S.busy||S.score.busy||R.busy)return;
      S.rosterOperation=b.dataset.operation;R.confirm=false;R.confirmation='';render();
      if(S.rosterOperation==='remove')await run(()=>history());
      return;
    }
    if(!action?.startsWith('removal-'))return;
    if(action==='removal-choose'){document.getElementById('removal-file-input')?.click();return;}
    if(action==='removal-reset'){if(!R.busy){invalidate();R.upload=null;R.committed=null;render();}return;}
    await run(async()=>{
      if(action==='removal-history'){await history(Number(b.dataset.page)||1);return;}
      if(action==='removal-refresh'){await Promise.all([refreshBase(),history()]);return;}
      if(action==='removal-header'&&R.upload){
        const value=Number(R.headerDraft??R.headerRow);
        if(!Number.isInteger(value)||value<1||value>60000)throw new Error('请输入有效表头行号。');
        const sh=await api(`/api/imports/${R.upload.upload_id}/sheet?sheet_index=${R.sheetIndex}&header_row=${value}`);
        R.upload.sheets[R.sheetIndex]=sh;R.headerRow=value;R.headerDraft=null;invalidate();
      }
      if(action==='removal-preview'&&R.upload){
        if(R.headerDraft!==null&&Number(R.headerDraft)!==R.headerRow)throw new Error('修改表头行后，请先点击“重新识别”。');
        invalidate();
        R.preview=await api('/api/removals/preview',{method:'POST',body:JSON.stringify({upload_id:R.upload.upload_id,sheet_index:R.sheetIndex,header_row:R.headerRow})});
      }
      if(action==='removal-commit'&&R.preview?.can_commit&&R.confirm&&R.confirmation==='确认剔除'){
        const result=await api('/api/removals/commit',{method:'POST',body:JSON.stringify({preview_id:R.preview.preview_id,confirm:R.confirm,confirmation:R.confirmation,expected_count:R.preview.summary.matched})});
        R.committed=result.batch;R.upload=null;invalidate();
        S.preview=null;S.score=newScoreState();clearSelection();
        // Never retain a deleted person's cached list or editor in this browser.
        S.studentsLoaded=false;S.students.items=[];S.editing=null;
        document.getElementById('student-dialog')?.close();
        S.filters.college_id=null;S.filters.class_id=null;S.filters.page=1;
        try{await Promise.all([refreshBase(),history()]);}
        catch(e){throw new Error(`剔除已完成，但统计读取失败：${e.message}。请重新读取，勿重复删除。`);}
        toast(`已剔除 ${num(result.batch.deleted_count)} 名学生。`);
      }
    });
  });
  document.addEventListener('change',event=>{
    const i=event.target;
    if(i.id==='removal-file-input'){upload(i.files?.[0]);return;}
    if(R.busy)return;
    if(i.id==='removal-sheet'&&R.upload){R.sheetIndex=Number(i.value);R.headerRow=R.upload.sheets[R.sheetIndex].header_row;R.headerDraft=null;invalidate();render();}
    if(i.id==='removal-confirm'){R.confirm=i.checked;render();}
  });
  document.addEventListener('input',event=>{
    const i=event.target;if(R.busy)return;
    if(i.id==='removal-header'){R.headerDraft=i.value;invalidate();render();}
    if(i.id==='removal-confirmation'){R.confirmation=i.value;render();}
  });
  document.addEventListener('dragover',e=>e.target.closest('#removal-dropzone')?.classList.add('dragging'));
  document.addEventListener('dragleave',e=>e.target.closest('#removal-dropzone')?.classList.remove('dragging'));
  document.addEventListener('drop',e=>{
    const zone=e.target.closest('#removal-dropzone');if(!zone)return;
    e.preventDefault();zone.classList.remove('dragging');
    if(e.dataTransfer?.files.length!==1){toast('请一次选择一个剔除文件。',true);return;}
    upload(e.dataTransfer.files[0]);
  });
  return {history};
}
