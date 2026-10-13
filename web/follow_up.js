import {h,num,percent,icon,blank} from './components.js';
import {updateHTML} from './safe_dom.js';
import {enhanceControls,closeControl,captureControl,restoreControl} from './custom_controls.js';

const labels={pending:'全部待跟进',missing:'未录入成绩',not_started:'未开始',in_progress:'进行中',completed_low:'已完成低于90',other_low:'其他低分需核对',at_90:'恰好90（已合格）',issues:'历史异常核对'};
const qualification=row=>row.qualified===null?'非正式在册人员':row.qualified?'已合格（≥90）':'不合格（无数值或<90）';
function view(R){
  const D=R.data,disabled=R.loading||R.exporting||!!R.error||!D?.total;
  const notes=R.kind==='issues'?'历史异常仅供人工核对，不代表尚未解决，不计入正式待跟进人数。学院/班级范围仅含可绑定当前在册学号的异常；无法绑定归属的记录仅在全校范围显示。':R.kind==='at_90'?'恰好90已合格，本分类仅供边界核对，不单独计入待跟进总人数；其中未开始/进行中的人员仍需跟进考试进度。':'待跟进总人数按人员去重，分类可能交叉，不能直接相加。合格分数与考试进度分开：≥90已合格，但未开始/进行中仍需跟进。';
  let content=R.error?blank('清单读取失败',R.error):blank('正在读取清单','请稍候。');
  if(D){
    content=D.items.length?`<div class="table-scroll"><table class="issue-table follow-table"><caption class="sr-only">${h(labels[D.kind])}，范围${h(R.scopeName)}</caption><thead><tr><th>学号</th><th>姓名</th><th>学院 / 班级</th><th>当前状态</th><th>当前得分率</th><th>合格情况</th><th>分类 / 跟进原因</th>${D.kind==='issues'?'<th>历史最高记录分值</th><th>异常历史</th>':''}</tr></thead><tbody>${D.items.map(row=>`<tr data-row-key="${h(row.key)}"><td class="mono">${h(row.student_id||'学号无效 / 未填写')}</td><td class="issue-person-name"><b>${h(row.name||'未填写')}</b></td><td class="issue-person-name">${h(row.college_name)}<div class="cell-secondary">${h(row.class_name)}</div></td><td>${h(row.status)}</td><td class="rate-value">${percent(row.score_rate)}</td><td><span class="badge ${row.qualified?'success':'neutral'}">${qualification(row)}</span></td><td class="issue-reasons">${h(row.category_labels.join('；'))}</td>${D.kind==='issues'?`<td>${percent(row.historical_rate)}<div class="cell-secondary">历史记录，不作正式成绩</div></td><td class="issue-reasons">${h(row.reasons.join('；'))}<div class="cell-secondary">${num(row.occurrence_count)}次 · ${num(row.batch_count)}批</div></td>`:''}</tr>`).join('')}</tbody></table></div>`:blank('当前分类没有记录','可切换分类或调整搜索；空清单不代表缺考。');
    content+=`<div class="pagination"><span>共 ${num(D.total)} ${D.kind==='issues'?'个历史汇总项':'名学生'}</span><div class="page-controls"><button class="btn secondary small" data-action="follow-prev" ${R.loading||D.page<=1?'disabled':''}>上一页</button><span class="page-number">${D.page} / ${D.pages}</span><button class="btn secondary small" data-action="follow-next" ${R.loading||D.page>=D.pages?'disabled':''}>下一页</button></div></div>`;
  }
  return `<div class="dialog-header"><div><h2 id="follow-up-title">待跟进清单</h2><p>范围：${h(R.scopeName)} · 独立于成绩页状态/得分筛选</p></div><button class="icon-btn" data-action="follow-close" aria-label="关闭待跟进清单">${icon('close')}</button></div><div class="issue-toolbar follow-toolbar"><div class="search-box">${icon('search')}<input id="follow-search" type="search" aria-label="搜索待跟进人员" placeholder="搜索学号、姓名或班级" maxlength="120" value="${h(R.q)}"></div><button class="btn secondary small" data-action="follow-refresh" ${R.loading?'disabled':''}>${icon('refresh')}刷新</button><button class="btn secondary small" data-action="follow-export" data-format="xlsx" ${disabled?'disabled':''}>${icon('download')}Excel</button><button class="btn secondary small" data-action="follow-export" data-format="csv" ${disabled?'disabled':''}>CSV</button></div><div class="follow-tabs" role="group" aria-label="待跟进分类">${Object.entries(labels).map(([kind,label])=>`<button id="follow-kind-${kind}" class="tab ${kind===R.kind?'active':''}" aria-pressed="${kind===R.kind}" data-action="follow-kind" data-kind="${kind}">${h(label)}<span>${D?num(D.counts[kind]):'—'}</span></button>`).join('')}</div><div class="issue-summary"><span>${h(notes)}</span><span role="status" aria-live="polite">${R.loading?'正在更新，暂时保留上次结果':D?`筛选范围内在册 ${num(D.scope_students)} 人 · 待跟进 ${num(D.counts.pending)} 人 · 数据版本 ${D.revision}`:'尚未获得有效数据'}</span>${R.error?`<span role="alert" class="update-error">${h(R.error)}<button class="btn secondary small" data-action="follow-refresh">重新读取</button></span>`:''}</div><div class="issue-dialog-content" id="follow-content" aria-busy="${R.loading}">${content}</div><div class="dialog-footer"><span class="small-muted">只读分类与导出，不修改分数、进度或学生资料；0分不自动认定为实际缺考。</span><button class="btn secondary" data-action="follow-close">关闭</button></div>`;
}

export function installFollowUp(S,{api,toast}){
  const dialog=document.createElement('dialog');dialog.id='follow-up-dialog';dialog.className='issue-dialog follow-dialog';dialog.setAttribute('aria-labelledby','follow-up-title');
  document.body.append(dialog);
  const R={kind:'pending',q:'',scope:{},scopeName:'全校',data:null,error:'',loading:false,exporting:false};
  let sequence=0,abort,timer,opener;
  function render(){
    const controls=captureControl(dialog),active=document.activeElement,id=active?.id,position=active?.selectionStart;
    const scroll=dialog.querySelector('#follow-content')?.scrollTop||0;
    updateHTML(dialog,view(R),enhanceControls);
    const content=dialog.querySelector('#follow-content');if(content)content.scrollTop=scroll;
    const next=id?document.getElementById(id):null;
    if(next&&!next.disabled&&dialog.contains(next)){next.focus({preventScroll:true});if(typeof position==='number'&&typeof next.setSelectionRange==='function'){try{next.setSelectionRange(position,position);}catch{}}}
    restoreControl(controls);
  }
  function query(page){return new URLSearchParams({...R.scope,q:R.q,kind:R.kind,page:String(page),page_size:'20'});}
  async function load(page=1){
    clearTimeout(timer);const seq=++sequence;abort?.abort();abort=new AbortController();
    R.loading=true;R.error='';render();
    try{
      const health=await api('/api/health',{signal:abort.signal});
      if(Number(health.follow_up_api_version||0)<1)throw new Error('当前后端尚未加载待跟进接口。请在启动窗口按 Ctrl+C 重启网站，再按 Ctrl+F5 刷新。');
      const data=await api(`/api/follow-up?${query(page)}`,{signal:abort.signal});
      if(seq===sequence&&dialog.open)R.data=data;
    }catch(e){if(e.name!=='AbortError'&&seq===sequence&&dialog.open)R.error=e.message;}
    finally{if(seq===sequence&&dialog.open){R.loading=false;render();}}
  }
  function close(){closeControl();clearTimeout(timer);sequence++;abort?.abort();R.loading=false;dialog.close();if(opener?.isConnected)opener.focus({preventScroll:true});}
  async function download(format){
    if(R.loading||R.exporting||R.error||!R.data?.total)return;
    const kind=R.kind,qs=query(1);qs.delete('page');qs.delete('page_size');qs.set('expected_revision',String(R.data.revision));
    R.exporting=true;render();
    try{
      const response=await fetch(`/api/follow-up/export.${format}?${qs}`,{cache:'no-store',credentials:'same-origin'});
      if(!response.ok){const error=await response.json();throw new Error(typeof error.detail==='string'?error.detail:'清单导出失败。');}
      if(Number(response.headers.get('X-Follow-Up-Api-Version')||0)<1)throw new Error('清单导出接口版本不匹配，请重启服务后刷新。');
      const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
      link.href=url;link.download=`待跟进-${labels[kind]}.${format}`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
      toast(`已导出 ${response.headers.get('X-Export-Records')} 条${labels[kind]}记录，包含当前筛选全部页。`);
    }catch(e){R.error=e.message;toast(e.message,true);}finally{R.exporting=false;if(dialog.open)render();}
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-action="follow-open"]');if(!button||button.disabled||dialog.open)return;
    opener=button;R.scope={};if(S.filters.college_id)R.scope.college_id=String(S.filters.college_id);if(S.filters.class_id)R.scope.class_id=String(S.filters.class_id);
    const co=S.hierarchy.find(value=>value.id===S.filters.college_id),cl=co?.classes.find(value=>value.id===S.filters.class_id);
    R.scopeName=cl?`${co.name} / ${cl.name}`:co?.name||'全校';
    R.q=S.filters.q;R.kind='pending';R.data=null;R.error='';render();dialog.showModal();dialog.querySelector('#follow-search')?.focus();load();
  });
  dialog.addEventListener('click',event=>{
    const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
    switch(button.dataset.action){
      case 'follow-close':close();break;
      case 'follow-refresh':load(R.data?.page||1);break;
      case 'follow-kind':R.kind=button.dataset.kind;load();break;
      case 'follow-prev':load(Math.max(1,R.data.page-1));break;
      case 'follow-next':load(R.data.page+1);break;
      case 'follow-export':download(button.dataset.format);break;
    }
  });
  dialog.addEventListener('input',event=>{
    if(event.target.id!=='follow-search')return;
    R.q=event.target.value;clearTimeout(timer);sequence++;abort?.abort();R.loading=true;R.error='';render();timer=setTimeout(()=>load(),280);
  });
  dialog.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!dialog.querySelector('.control-popover')){event.preventDefault();event.stopPropagation();close();}
  });
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  return {close};
}
