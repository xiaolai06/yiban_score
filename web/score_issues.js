import {h,num,percent,date,duration,examTimeTitle,icon,blank} from './components.js';
import {closeControl,enhanceControls,captureControl,restoreControl} from './custom_controls.js';
import {updateHTML} from './safe_dom.js';

const kinds={unmatched:'名单外学号',invalid:'数据错误',name_conflict:'姓名冲突',duplicate_conflict:'重复学号冲突'};
const joined=(values)=>values?.length?values.join(' / '):'未填写';
const kindBadges=(values)=>values.map(kind=>`<span class="badge absent">${h(kinds[kind]||kind)}</span>`).join('');
function exportMenu(qs,detail=false,disabled=false) {
  if(disabled)return `<button class="btn secondary" disabled>${icon('download')}导出</button>`;
  const links=detail?[['occurrences','出现明细']]:[['summary','人员汇总'],['occurrences','全部出现明细']];
  return `<details class="export-menu issue-export"><summary class="btn secondary" aria-label="${detail?'导出此人出现记录':'导出异常人员'}">${icon('download')}导出${icon('down')}</summary><div class="export-popover"><p>${detail?'导出此汇总项的全部出现记录':'导出当前筛选的全部结果，不限当前页'}</p>${links.map(([scope,label])=>['xlsx','csv'].map(type=>`<a class="export-option" href="/api/scores/issues/export.${type}?${h(new URLSearchParams({...qs,scope}).toString())}" download>${icon('file')}${h(label)} · ${type==='xlsx'?'Excel':'CSV'}</a>`).join('')).join('')}</div></details>`;
}
function pager(data,prefix,loading) {
  return `<div class="pagination"><span>共 ${num(data.total)} ${prefix==='issue-detail'?'条记录':'个汇总项'}</span><div class="page-controls"><button class="btn secondary small" data-action="${prefix}-prev" ${loading||data.page<=1?'disabled':''}>上一页</button><span class="page-number">${data.page} / ${data.pages}</span><button class="btn secondary small" data-action="${prefix}-next" ${loading||data.page>=data.pages?'disabled':''}>下一页</button></div></div>`;
}
function peopleTable(data,loading=false) {
  if(!data.items.length)return blank('暂无异常人员记录','已保存的导入出现异常后，会自动汇总在这里；可以调整搜索或分类。');
  return `<div class="table-scroll issue-table-scroll" id="issue-people-table" aria-busy="${loading}"><table class="issue-table"><thead><tr><th>学号</th><th>姓名记录</th><th>最高记录得分率</th><th>对应答题时长</th><th>出现 / 批次</th><th>异常类型与原因</th><th>最近出现</th><th>操作</th></tr></thead><tbody>${data.items.map(person=>`<tr data-person-key="${h(person.key)}">
    <td class="mono issue-id">${h(person.student_id||'学号无效 / 未填写')}${!person.student_id&&person.raw_ids.length?`<div class="cell-secondary">${h(joined(person.raw_ids))}</div>`:''}</td><td class="issue-person-name"><b>${h(joined(person.names))}</b>${person.colleges.length||person.classes.length?`<div class="cell-secondary">${h([...person.colleges,...person.classes].join(' / '))}</div>`:''}</td>
    <td><strong class="rate-value">${percent(person.best_score_rate)}</strong>${person.best_batch_id?`<div class="cell-secondary">批次 #${person.best_batch_id} · 第 ${person.best_row_number} 行</div>`:'<div class="cell-secondary">原始分数见出现明细</div>'}</td>
    <td class="duration-cell" title="${h(examTimeTitle({exam_started_at:person.best_exam_started_at,exam_finished_at:person.best_exam_finished_at}))}">${duration(person.best_answer_duration_seconds)}</td>
    <td><b>${num(person.occurrence_count)} 次</b><div class="cell-secondary">${num(person.batch_count)} 个批次</div></td>
    <td class="issue-reasons"><div class="issue-badges">${kindBadges(person.kinds)}</div><p title="${h(person.reasons.join('；'))}">${h(person.reasons.join('；')||'详见原始记录')}</p></td>
    <td>${h(date(person.last_seen))}<div class="cell-secondary">批次 #${person.latest_batch_id}</div></td><td><button class="btn secondary small" data-action="issue-person" data-key="${h(person.key)}" ${loading?'disabled':''}>出现记录</button></td>
  </tr>`).join('')}</tbody></table></div>`;
}
function detailView(state) {
  if(!state.selectedKey)return '';
  const data=state.detail;
  if(state.detailLoading&&!data)return '<section class="issue-detail" id="issue-detail"><div class="empty" role="status">正在读取出现记录…</div></section>';
  if(state.detailError&&!data)return `<section class="issue-detail" id="issue-detail"><div class="info-box error" role="alert">${h(state.detailError)}<button class="btn secondary small" data-action="issue-detail-retry">重试</button></div></section>`;
  if(!data)return '';
  return `<section class="issue-detail" id="issue-detail" aria-busy="${state.detailLoading}"><div class="card-header"><div><h3>${h(joined(data.person.names))} · ${h(data.person.student_id||'学号无效 / 未填写')}</h3><p class="card-subtitle">${state.detailLoading?'正在更新出现记录，暂时保留上次结果':'最高记录 '+percent(data.person.best_score_rate)+' · 保留每次原始分数，不写入正式成绩'}</p></div><div class="history-tools">${exportMenu({key:state.selectedKey},true,state.detailLoading)}<button class="icon-btn" data-action="issue-detail-close" aria-label="收起出现记录">${icon('close')}</button></div></div>
    ${state.detailError?`<div class="info-box error" role="alert">更新失败：${h(state.detailError)}；已保留上次结果<button class="btn secondary small" data-action="issue-detail-retry">重试</button></div>`:''}<div class="table-scroll"><table class="issue-table"><thead><tr><th>批次 / 时间</th><th>原表行</th><th>原学号 / 姓名</th><th>原状态</th><th>原始分数</th><th>可识别得分率</th><th>答题时长</th><th>异常原因</th></tr></thead><tbody>${data.items.map(row=>`<tr data-row-key="${row.batch_id}:${row.row_number}">
      <td><b>#${row.batch_id} · ${h(row.batch_name)}</b><div class="cell-secondary">${h(date(row.created_at))} · ${row.batch_state==='reverted'?'已撤销（记录保留）':'已保存'}</div><div class="cell-secondary">${h(row.filename)}</div></td>
      <td>${row.row_number}</td><td class="issue-person-name"><div class="mono">${h(row.raw_id||'未填写')}</div><b>${h(row.source_name||'未填写')}</b>${row.name&&row.name!==row.source_name?`<div class="cell-secondary">名单姓名：${h(row.name)}</div>`:''}</td>
      <td>${h(row.raw_status||'未填写')}</td><td class="issue-original">${h(row.raw_rate||'未填写')}<div class="cell-secondary">${h(row.raw_result)}</div></td><td class="rate-value">${percent(row.score_rate)}</td><td class="duration-cell" title="${h(examTimeTitle(row))}">${duration(row.answer_duration_seconds)}<div class="cell-secondary">${h(row.raw_started_at||'未提供')} → ${h(row.raw_finished_at||'未提供')}</div></td>
      <td class="issue-reasons">${kindBadges([row.action])}<p>${h(row.errors.join('；'))}</p></td>
    </tr>`).join('')}</tbody></table></div>${pager(data,'issue-detail',state.detailLoading)}</section>`;
}
function dialogView(state) {
  const data=state.data;
  let body='<div class="empty" role="status">正在读取异常汇总…</div>';
  if(state.error)body=`<div class="empty"><h3>异常汇总暂时无法读取</h3><p role="alert">${h(state.error)}</p><button class="btn secondary" data-action="issue-retry">重新读取</button></div>`;
  if(data)body=peopleTable(data,state.loading||!!state.error)+pager(data,'issue-list',state.loading);
  return `<div class="dialog-header"><div><h2 id="score-issues-title">异常人员</h2><p>历次导入按有效学号汇总；学号无效的记录单独保留。</p></div><button class="icon-btn" data-action="issue-close" aria-label="关闭异常人员">${icon('close')}</button></div>
    <div class="issue-toolbar" id="issue-toolbar"><div class="search-box">${icon('search')}<input type="search" id="issue-search" maxlength="120" value="${h(state.q)}" placeholder="搜索学号、姓名或异常原因" aria-label="搜索异常人员"></div>
      <select id="issue-kind" aria-label="异常分类"><option value="all">全部异常</option>${Object.entries(kinds).map(([key,label])=>`<option value="${key}" ${state.kind===key?'selected':''}>${h(label)}</option>`).join('')}</select>
      <button class="btn secondary" data-action="issue-refresh" ${state.loading?'disabled':''}>${icon('refresh')}刷新</button>${exportMenu({q:data?.q||'',kind:data?.kind||'all'},false,state.loading||!!state.error||!data?.total)}
    </div>
    <div class="issue-summary" id="issue-summary">${data?`共 ${num(data.total)} 个汇总项 · ${num(data.occurrences)} 条出现记录`:'正在读取已保存记录'}<span id="issue-update-status" class="${state.error?'update-error':''}" role="status" aria-live="polite">${state.error?`更新失败：${h(state.error)}${data?'；已保留上次结果':''}${data?' <button class="btn secondary small" data-action="issue-retry">重试</button>':''}`:state.loading?(data?'正在更新，暂时显示上次结果':'正在读取异常汇总…'):'较高得分率保留，重复出现继续累计；撤销批次不删除历史。'}</span></div>
    <div class="issue-dialog-content" id="issue-content">${body}${detailView(state)}</div>
    <div class="dialog-footer"><span class="small-muted">非法 / 缺失分数不参与最高值比较，原值始终保留。</span><button class="btn secondary" data-action="issue-close">关闭</button></div>`;
}

export function installScoreIssues({api}) {
  const dialog=document.createElement('dialog');
  dialog.id='score-issues-dialog';dialog.className='issue-dialog';dialog.setAttribute('aria-labelledby','score-issues-title');
  document.body.append(dialog);
  const state={q:'',kind:'all',data:null,loading:false,error:'',selectedKey:'',detail:null,detailLoading:false,detailError:''};
  let listSeq=0,detailSeq=0,searchTimer,listAbort,detailAbort,opener;
  function render() {
    const control=captureControl(dialog);
    const active=document.activeElement;
    const activeId=dialog.contains(active)?active.id:'';
    const activeAction=dialog.contains(active)?active.dataset.action:'';
    const activeKey=active?.dataset?.key;
    const start=active?.selectionStart,end=active?.selectionEnd;
    const scroll=dialog.querySelector('.issue-dialog-content')?.scrollTop||0,dialogScroll=dialog.scrollTop;
    updateHTML(dialog,dialogView(state),enhanceControls);
    const content=dialog.querySelector('.issue-dialog-content');if(content)content.scrollTop=scroll;
    dialog.scrollTop=dialogScroll;
    let next=activeId?document.getElementById(activeId):null;
    if(!next&&activeAction)next=dialog.querySelector(`[data-action="${CSS.escape(activeAction)}"]${activeKey?`[data-key="${CSS.escape(activeKey)}"]`:''}`);
    if(next&&!next.disabled){next.focus({preventScroll:true});if(typeof next.setSelectionRange==='function'&&typeof start==='number'){try{next.setSelectionRange(start,end);}catch{ /* Search input selection is optional. */ }}}
    restoreControl(control);
  }
  function clearDetail(){detailAbort?.abort();state.selectedKey='';state.detail=null;state.detailError='';state.detailLoading=false;detailSeq++;}
  async function loadList(page=1,refreshDetail=false) {
    clearTimeout(searchTimer);
    const seq=++listSeq;
    listAbort?.abort();listAbort=new AbortController();
    state.loading=true;state.error='';render();
    const qs=new URLSearchParams({q:state.q,kind:state.kind,page:String(page),page_size:'20'});
    try{
      const data=await api(`/api/scores/issues?${qs}`,{signal:listAbort.signal});
      if(seq!==listSeq||!dialog.open)return;
      state.data=data;
      if(refreshDetail&&state.selectedKey)await loadDetail(state.selectedKey,state.detail?.page||1);
    }catch(error){if(error.name!=='AbortError'&&seq===listSeq&&dialog.open)state.error=error.message==='Not Found'?'当前后台尚未加载异常汇总接口，请重启启动窗口后刷新页面。':error.message;}
    finally{if(seq===listSeq&&dialog.open){state.loading=false;render();}}
  }
  async function loadDetail(key,page=1) {
    const seq=++detailSeq;
    detailAbort?.abort();detailAbort=new AbortController();
    const samePerson=state.selectedKey===key;
    if(!samePerson)state.detail=null;
    state.selectedKey=key;state.detailLoading=true;state.detailError='';render();
    try{
      const data=await api(`/api/scores/issues/occurrences?${new URLSearchParams({key,page:String(page),page_size:'20'})}`,{signal:detailAbort.signal});
      if(seq!==detailSeq||!dialog.open)return;
      state.detail=data;
    }catch(error){if(error.name!=='AbortError'&&seq===detailSeq&&dialog.open)state.detailError=error.message;}
    finally{
      if(seq===detailSeq&&dialog.open){
        state.detailLoading=false;render();
        const content=dialog.querySelector('.issue-dialog-content'),detail=dialog.querySelector('#issue-detail');
        if(!samePerson&&content&&detail){const scroller=content.scrollHeight>content.clientHeight?content:dialog;scroller.scrollTop+=detail.getBoundingClientRect().top-scroller.getBoundingClientRect().top;}
      }
    }
  }
  function close() {
    closeControl();clearTimeout(searchTimer);listSeq++;detailSeq++;
    listAbort?.abort();detailAbort?.abort();state.loading=false;state.detailLoading=false;
    if(dialog.open)dialog.close();
    if(opener?.isConnected)opener.focus({preventScroll:true});
  }
  document.addEventListener('click',event=>{
    const open=event.target.closest('[data-action="score-issues-open"]');
    if(open&&!open.disabled&&!dialog.open){opener=open;state.error='';render();dialog.showModal();dialog.querySelector('#issue-search')?.focus({preventScroll:true});loadList();}
  });
  dialog.addEventListener('click',event=>{
    if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)close();return;}
    const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
    switch(button.dataset.action){
      case 'issue-close':close();break;
      case 'issue-refresh':case 'issue-retry':loadList(state.data?.page||1,true);break;
      case 'issue-list-prev':loadList(Math.max(1,state.data.page-1));break;
      case 'issue-list-next':loadList(state.data.page+1);break;
      case 'issue-person':loadDetail(button.dataset.key);break;
      case 'issue-detail-close':clearDetail();render();break;
      case 'issue-detail-retry':loadDetail(state.selectedKey,state.detail?.page||1);break;
      case 'issue-detail-prev':loadDetail(state.selectedKey,Math.max(1,state.detail.page-1));break;
      case 'issue-detail-next':loadDetail(state.selectedKey,state.detail.page+1);break;
    }
  });
  dialog.addEventListener('input',event=>{
    if(event.target.id!=='issue-search')return;
    state.q=event.target.value;clearTimeout(searchTimer);listSeq++;clearDetail();state.loading=true;state.error='';render();
    listAbort?.abort();
    searchTimer=setTimeout(()=>loadList(1),300);
  });
  dialog.addEventListener('change',event=>{if(event.target.id==='issue-kind'){state.kind=event.target.value;clearDetail();loadList(1);}});
  dialog.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!dialog.querySelector('.control-popover, .export-menu[open]')){
      event.preventDefault();event.stopPropagation();close();
    }
  });
  dialog.addEventListener('cancel',()=>{closeControl();clearTimeout(searchTimer);listSeq++;detailSeq++;});
  return {refresh:()=>{if(dialog.open)loadList(state.data?.page||1,true);}};
}
