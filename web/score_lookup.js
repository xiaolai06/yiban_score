import {h,num,percent,date,duration,examTimeTitle,icon,badge,blank,statuses} from './components.js';
import {closeControl,enhanceControls,captureControl,restoreControl} from './custom_controls.js';
import {updateHTML} from './safe_dom.js';
import {statusDescription,scoreMeaning,statusGuide} from './score_status.js';

function peopleView(state) {
  const data=state.data;
  if(!data)return blank(state.error?'查询暂时不可用':'正在读取人员状态',state.error||'仅查询本地已保存的正式名单，不访问第三方平台。',state.error?'<button class="btn secondary" data-action="lookup-refresh">重试</button>':'');
  if(!data.items.length)return blank('未找到在册人员','可按学号、姓名、历史易班 ID、学院或班级检索；没有匹配不等于缺考或未注册。名单外历史记录请到“异常人员”查看。',`<button class="btn secondary" data-action="score-issues-open" data-query="${h(state.q)}">查历史异常</button>`);
  return `<div class="table-scroll"><table class="lookup-table"><thead><tr><th>人员 / 学号</th><th>历史易班 ID</th><th>当前名单归属</th><th>当前状态及含义</th><th>当前得分率</th><th>答题时长</th><th>当前成绩来源</th></tr></thead><tbody>${data.items.map(row=>`<tr data-student-row="lookup:${h(row.student_id)}"><td data-label="人员 / 学号"><b class="lookup-name">${h(row.name)}</b><div class="mono">${h(row.student_id)}</div><button class="btn ghost small" data-action="score-issues-open" data-query="${h(row.student_id)}">查历史异常</button></td><td data-label="历史易班 ID" class="mono">${h(row.historical_yiban_ids?.join(' / ')||'未保存')}</td><td data-label="当前名单归属">${h(row.college_name)}<div>${h(row.class_name)}</div><div class="cell-secondary">${h([row.gender,row.major].filter(Boolean).join(' / '))}</div></td><td data-label="当前状态及含义">${badge(row.status)}<p>${h(statusDescription(row.status))}</p></td><td data-label="当前得分率"><strong class="rate-value">${percent(row.score_rate)}</strong><p>${h(scoreMeaning(row))}</p></td><td data-label="答题时长" title="${h(examTimeTitle(row))}">${duration(row.answer_duration_seconds)}</td><td data-label="当前成绩来源">${row.score_batch_id?`批次 #${h(row.score_batch_id)}<div>${h(row.score_batch_name||'')}</div><div class="cell-secondary">${h(row.score_source_filename||'')}</div>`:'未关联成绩批次（可能为人工维护或尚未导入）'}<div class="cell-secondary">最近更新：${h(date(row.updated_at))}</div></td></tr>`).join('')}</tbody></table></div>`;
}
function view(state) {
  const data=state.data;
  return `<div class="dialog-header"><div><h2 id="score-lookup-title">人员状态查询</h2><p>当前在册名单与成绩，只读查询；状态是本地最后保存值，不是易班平台实时状态。</p></div><button class="icon-btn" data-action="lookup-close" aria-label="关闭人员状态查询">${icon('close')}</button></div>
    <div class="issue-toolbar"><div class="search-box">${icon('search')}<input type="search" id="lookup-search" aria-label="检索在册人员" maxlength="120" value="${h(state.q)}" placeholder="学号 / 姓名 / 历史易班 ID / 学院班级"></div><select id="lookup-status" aria-label="当前登记状态"><option value="">全部状态</option>${statuses.map(status=>`<option value="${h(status)}" ${state.status===status?'selected':''}>${h(status)}</option>`).join('')}</select><button class="btn secondary" data-action="lookup-refresh" ${state.loading?'disabled':''}>${icon('search')}查询</button><button class="btn secondary" data-action="lookup-clear">清空</button></div>
    <div class="issue-summary">${data?`共 ${num(data.total)} 位在册人员 · 数据版本 ${h(data.revision??'未提供')}`:'人员状态与成绩独立核对'}<span role="status" aria-live="polite">${state.error?`读取失败：${h(state.error)}${data?'；暂时保留上次结果':''}`:state.loading?'正在查询，已有结果暂时保留':'易班 ID 仅来自已保存报表的历史学号关联；不验证当前账号归属，不按同名合并，不自动解决异常。'}</span></div>
    <div class="issue-dialog-content" aria-busy="${state.loading}">${peopleView(state)}${data?`<div class="pagination"><span>共 ${num(data.total)} 位 · 每页20位</span><div class="page-controls"><button class="btn secondary small" data-action="lookup-prev" ${state.loading||data.page<=1?'disabled':''}>上一页</button><span class="page-number">${data.page} / ${data.pages}</span><button class="btn secondary small" data-action="lookup-next" ${state.loading||data.page>=data.pages?'disabled':''}>下一页</button></div></div>`:''}${statusGuide()}</div><div class="dialog-footer"><span class="small-muted">无有效成绩 ≠ 缺考；已完成 ≠ 合格；历史异常 ≠ 当前未解决。</span><button class="btn secondary" data-action="lookup-close">关闭</button></div>`;
}
export function installScoreLookup({api}) {
  const dialog=document.createElement('dialog');
  dialog.id='score-lookup-dialog';dialog.className='issue-dialog lookup-dialog';dialog.setAttribute('aria-labelledby','score-lookup-title');document.body.append(dialog);
  const state={q:'',status:'',loading:false,error:'',data:null};
  let seq=0,abort,timer,opener;
  function render() {
    const control=captureControl(dialog),active=document.activeElement,id=dialog.contains(active)?active.id:'',position=active?.selectionStart;
    const scroll=dialog.querySelector('.issue-dialog-content')?.scrollTop||0,top=dialog.scrollTop;
    updateHTML(dialog,view(state),enhanceControls);
    const content=dialog.querySelector('.issue-dialog-content');if(content)content.scrollTop=scroll;dialog.scrollTop=top;
    const next=id?dialog.querySelector('#'+CSS.escape(id)):null;
    if(next&&!next.disabled){next.focus({preventScroll:true});if(typeof position==='number'){try{next.setSelectionRange(position,position);}catch{ /* Search selection is optional. */ }}}
    restoreControl(control);
  }
  async function load(page=1) {
    clearTimeout(timer);abort?.abort();abort=new AbortController();const token=++seq;
    state.loading=true;state.error='';render();
    try {
      const data=await api(`/api/scores/people?${new URLSearchParams({q:state.q,status:state.status,page:String(page),page_size:'20'})}`,{signal:abort.signal});
      if(token===seq&&dialog.open)state.data=data;
    } catch(error){if(error.name!=='AbortError'&&token===seq&&dialog.open)state.error=error.message==='Not Found'?'当前服务尚不支持人员状态查询，请重启服务并刷新；原导入和成绩查看不受影响。':error.message;}
    finally{if(token===seq&&dialog.open){state.loading=false;render();}}
  }
  function close(){clearTimeout(timer);seq++;abort?.abort();closeControl();if(dialog.open)dialog.close();const target=opener?.isConnected&&!opener.closest('dialog:not([open])')?opener:document.querySelector('.heading-actions [data-action="score-people-open"]');target?.focus({preventScroll:true});}
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-action="score-people-open"]');
    if(button&&!button.disabled&&!dialog.open){opener=button;if(button.dataset.query!==undefined){state.q=button.dataset.query;state.status='';state.data=null;}render();dialog.showModal();dialog.querySelector('#lookup-search')?.focus({preventScroll:true});load();}
  });
  dialog.addEventListener('click',event=>{
    if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)close();return;}
    const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
    switch(button.dataset.action){
      case 'lookup-close':close();break;
      case 'lookup-refresh':load();break;
      case 'lookup-prev':load(Math.max(1,state.data.page-1));break;
      case 'lookup-next':load(state.data.page+1);break;
      case 'lookup-clear':state.q='';state.status='';render();dialog.querySelector('#lookup-search')?.focus({preventScroll:true});load();break;
      case 'score-issues-open':close();break;
    }
  });
  dialog.addEventListener('input',event=>{if(event.target.id==='lookup-search'){state.q=event.target.value;seq++;abort?.abort();clearTimeout(timer);state.loading=true;state.error='';render();timer=setTimeout(()=>load(),300);}});
  dialog.addEventListener('change',event=>{if(event.target.id==='lookup-status'){state.status=event.target.value;load();}});
  dialog.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.id==='lookup-search'){event.preventDefault();load();}if(event.key==='Escape'&&!dialog.querySelector('.control-popover')){event.preventDefault();event.stopPropagation();close();}});
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  return {refresh:()=>{if(dialog.open)load(state.data?.page||1);}};
}
