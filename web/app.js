import {installFollowUp} from './follow_up.js';
import {newRemovalState,installRemovalController} from './roster_removal.js';
import {newScoreState} from './score_views.js';
import {installScoreController} from './score_controller.js';
import {h, icon} from './components.js';
import {mountHTML, updateHTML} from './safe_dom.js';
import {shell, renderView, editDialog} from './views.js';
import {enhanceControls, closeControl, captureControl, restoreControl} from './custom_controls.js';
import {installDirectoryController, DIRECTORY_UPDATE_MESSAGE} from './directory_controller.js';
import {loadOverviewStats} from './overview_stats.js';
import {completeDirectoryStatistics} from './directory_stats.js';
import {readBaseSnapshot} from './read_snapshots.js';
import {PASS_SCORE} from './grade_values.js';

const emptyStats = {students:0,colleges:0,classes:0,recorded:0,completed:null,qualified:null,average_rate:null,statuses:[],colleges_detail:[]};
const emptyList = {items:[],total:0,recorded:0,qualified:null,below_60:null,at_60:null,settled:0,average_rate:null,page:1,page_size:10,pages:1};
const defaultFilters = () => ({q:'',college_id:null,class_id:null,status:'',score_filter:'all',page:1,page_size:10,sort:'student_id',order:'asc'});
// Old bookmarks now open score details; there is no separate student module.
if (location.hash==='#students') history.replaceState(null,'','#scores');
const S = {
  view:['import','score-import','scores','overview','help'].includes(location.hash.slice(1))?location.hash.slice(1):'import',
  stats:{...emptyStats},hierarchy:[],history:[],health:null,
  upload:null,sheetIndex:0,headerRow:1,headerDraft:null,mapping:{},mode:'update',preview:null,committed:null,allowNames:false,
  score:newScoreState(),rosterOperation:'import',removal:newRemovalState(),
  busy:false,importError:'',filters:defaultFilters(),students:{...emptyList},studentsLoaded:false,listLoading:false,listError:'',listContext:'',
  openColleges:new Set(),treeSearch:'',editing:null,saving:false,ready:false,
  pageMode:'auto',exportClasses:new Set(),exportAll:false,exportBusy:false,
  exportStudents:new Set(),exportAllStudents:false,exportExcludedStudents:new Set(),exportStudentFilter:null,selectedStudentTotal:0,
  directoryOutdated:false,directoryStatsRecovered:false,directoryStatsError:'',treeSort:{key:'name',order:'asc'},
  overviewSort:{key:'students',order:'desc'},overviewError:'',
};
let toastTimer, searchTimer, treeTimer, listSequence=0,baseSequence=0,bootSequence=0;
let renderedView=null,listAbort=null;
const studentCache=new Map();
const CACHE_TTL=15000;

async function api(path, options={}) {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method !== 'GET') headers.set('X-Requested-With','StudentScore');
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type','application/json');
  let response;
  try {
    response = await fetch(path, {...options, headers, credentials:'same-origin', cache:'no-store'});
  } catch(error) {
    if(error.name==='AbortError')throw error;
    throw new Error('无法连接服务。请确认服务正在运行，再刷新页面。');
  }
  let payload;
  try { payload = await response.json(); }
  catch(error) {if(error.name==='AbortError')throw error;throw new Error('服务返回了无法识别的响应，请检查启动窗口。');}
  if (!response.ok) {
    const detail=payload.detail;
    const message=Array.isArray(detail)?detail.map(x=>`${x.loc?.slice(1).join('.') || '参数'}：${x.msg}`).join('；'):detail;
    throw new Error(message || `请求失败（HTTP ${response.status}）`);
  }
  return payload;
}
function toast(message, error=false) {
  const node=document.getElementById('toast');
  clearTimeout(toastTimer);
  node.textContent=message;
  node.className=`visible ${error?'error':''}`;
  toastTimer=setTimeout(()=>{node.className='';},error?6500:3800);
}
function render() {
  if (!S.ready) return;
  const labels={import:'名单导入','score-import':'导入成绩',scores:'成绩明细',overview:'数据总览',help:'使用说明'};
  document.title=`${labels[S.view]} · 学绩`;
  const main=document.getElementById('main');
  const controlSnapshot=captureControl(main);
  if (!main) {
    closeControl();
    mountHTML(document.getElementById('app'),shell(S));
    enhanceControls();renderedView=S.view;directoryController.afterRender();return;
  }
  main.classList.toggle('score-main',S.view==='scores');
  const active=document.activeElement;
  const activeId=main.contains(active)?active.id:'';
  const position=activeId&&typeof active.selectionStart==='number'?active.selectionStart:null;
  const treeScroll=main.querySelector('.tree-body')?.scrollTop;
  const tableArea=main.querySelector('.score-table-scroll');
  const tableScroll=tableArea?{top:tableArea.scrollTop,left:tableArea.scrollLeft}:null;
  const overviewScroll=main.querySelector('.overview-table-scroll')?.scrollLeft;
  if(renderedView===S.view)updateHTML(main,renderView(S),enhanceControls);
  else {mountHTML(main,renderView(S));enhanceControls(main);}
  renderedView=S.view;directoryController.afterRender();
  document.querySelectorAll('[data-nav]').forEach(a=>{
    const selected=a.dataset.nav===S.view;
    a.classList.toggle('active',selected);
    if(selected)a.setAttribute('aria-current','page');
    else a.removeAttribute('aria-current');
  });
  const tree=main.querySelector('.tree-body');
  if (tree && treeScroll!==undefined) tree.scrollTop=treeScroll;
  const nextTable=main.querySelector('.score-table-scroll');
  if(nextTable&&tableScroll){nextTable.scrollTop=tableScroll.top;nextTable.scrollLeft=tableScroll.left;}
  const overviewTable=main.querySelector('.overview-table-scroll');
  if(overviewTable && overviewScroll!==undefined)overviewTable.scrollLeft=overviewScroll;
  if (activeId) {
    const next=document.getElementById(activeId);
    if (next && !next.disabled) {
      next.focus({preventScroll:true});
      if (position!==null && typeof next.setSelectionRange==='function') {
        try {next.setSelectionRange(position,position);} catch {}
      }
    }
  }
  restoreControl(controlSnapshot);
}
async function refreshBase({includeHistory=true}={}) {
  const seq=++baseSequence;
  const snapshot=await readBaseSnapshot(api,{includeHistory,isCurrent:()=>seq===baseSequence});
  if(!snapshot)return;
  const {stats,hierarchy,history}=snapshot;
  if(S.stats.revision!==stats.revision)studentCache.clear();
  // Never render an old server's 90-point totals while recalculating or on failure.
  S.stats=stats.pass_score===PASS_SCORE?stats:{...stats,completed:null,qualified:null,
    colleges_detail:stats.colleges_detail?.map(co=>({...co,completed:null,qualified:null}))};
  S.hierarchy=hierarchy.colleges;if(history)S.history=history.items;
  const classes=new Set(S.hierarchy.flatMap(co=>co.classes.map(cl=>cl.id)));
  if(S.exportAll)S.exportClasses=classes;
  else for(const id of S.exportClasses)if(!classes.has(id))S.exportClasses.delete(id);
  if(S.view==='overview'){
    S.overviewError='';
    try {
      const completed=await loadOverviewStats(api,stats);
      if(seq===baseSequence&&S.view==='overview')S.stats=completed;
    } catch(error){if(seq===baseSequence&&S.view==='overview')S.overviewError=error.message;}
  }
}
async function initialize() {
  const seq=++bootSequence;
  try {
    const [health]=await Promise.all([api('/api/health'),refreshBase()]);
    if(seq!==bootSequence)return;
    S.health=health;
    S.directoryOutdated=Number(S.health.directory_api_version||0)<6;
    if(S.view==='score-import')await scoreController.history();
    S.ready=true;
    render();
    if (S.view==='scores') await loadStudents();
  } catch(e) {
    if(seq!==bootSequence)return;
    mountHTML(document.getElementById('app'),`<div class="boot"><span class="boot-mark">学</span><h1>服务未连接</h1><p>${h(e.message)}</p><button class="btn primary" data-action="retry">重新连接</button></div>`);
  }
}
async function changeView() {
  let key=location.hash.slice(1);
  if (key==='students') {
    history.replaceState(null,'','#scores');
    key='scores';
  }
  if (!['import','score-import','scores','overview','help'].includes(key)) return;
  S.view=key;
  if(!S.ready)return;
  render();
  if (key==='scores') await loadStudents();
  else if (key==='score-import') {
    try {await Promise.all([refreshBase(),scoreController.history()]);if(S.view===key)render();} catch(e) {toast(e.message,true);}
  }
  else if (key==='overview') {
    try {await refreshBase({includeHistory:false});if(S.view===key)render();} catch(e) {toast(e.message,true);}
  }
}
function gotoScores(college=null,classId=null) {
  if (college!==null) {
    directoryController.clearStudentSelection();
    S.filters.college_id=college;S.filters.class_id=classId;S.filters.page=1;S.filters.q='';
    S.openColleges.add(college);
  }
  if (S.view==='scores') loadStudents();
  else location.hash='scores';
}
function resetImport() {
  if (S.busy) return;
  Object.assign(S,{upload:null,preview:null,committed:null,importError:'',mapping:{},sheetIndex:0,headerRow:1,headerDraft:null,allowNames:false});
  render();
}
async function upload(file) {
  if (!file || S.busy) return;
  if (!/\.(xls|xlsx)$/i.test(file.name)) {toast('请选择 .xls 或 .xlsx 文件，不要直接修改扩展名。',true);return;}
  if (file.size>20*1024*1024) {toast('文件不能超过 20 MB。',true);return;}
  Object.assign(S,{busy:true,upload:null,preview:null,committed:null,importError:'',allowNames:false});
  render();
  try {
    const form=new FormData();form.append('file',file);
    S.upload=await api('/api/imports/analyze',{method:'POST',body:form});
    S.sheetIndex=S.upload.selected_sheet;
    const sheet=S.upload.sheets[S.sheetIndex];
    S.headerRow=sheet.header_row;S.headerDraft=null;S.mapping={...sheet.mapping};
    toast(`已读取文件，识别 ${sheet.rows.toLocaleString('zh-CN')} 行。请核对字段映射。`);
  } catch(e) {S.importError=e.message;toast(e.message,true);}
  finally {S.busy=false;render();}
}
async function inspectHeader() {
  if (!S.upload||S.busy) return;
  const value=Number(document.getElementById('header-row').value);
  if (!Number.isInteger(value)||value<1||value>60000) {toast('请输入有效的表头行号。',true);return;}
  S.busy=true;S.importError='';render();
  try {
    const sh=await api(`/api/imports/${S.upload.upload_id}/sheet?sheet_index=${S.sheetIndex}&header_row=${value}`);
    S.upload.sheets[S.sheetIndex]=sh;S.headerRow=value;S.headerDraft=null;S.mapping={...sh.mapping};S.preview=null;
    toast('已重新识别表头。');
  } catch(e) {S.importError=e.message;toast(e.message,true);}
  finally {S.busy=false;render();}
}
async function makePreview() {
  if (!S.upload||S.busy) return;
  if (S.headerDraft!==null && Number(S.headerDraft)!==S.headerRow) {toast('修改表头行后，请先点击“重新识别”。',true);return;}
  S.busy=true;S.importError='';S.preview=null;S.allowNames=false;render();
  try {
    S.preview=await api('/api/imports/preview',{method:'POST',body:JSON.stringify({
      upload_id:S.upload.upload_id,sheet_index:S.sheetIndex,header_row:S.headerRow,mapping:S.mapping,mode:S.mode,
    })});
    if (!S.preview.can_commit) toast(`发现 ${S.preview.summary.error_count} 行错误；请查看错误记录。`,true);
    else toast('全表校验完成，请核对预览后确认入库。');
  } catch(e) {S.importError=e.message;toast(e.message,true);}
  finally {
    S.busy=false;render();
    if (S.preview) document.getElementById('preview')?.scrollIntoView({behavior:'smooth',block:'start'});
  }
}
async function commitImport() {
  if (!S.preview||S.busy||!S.preview.can_commit) return;
  S.busy=true;S.importError='';render();
  try {
    const result=await api('/api/imports/commit',{method:'POST',body:JSON.stringify({preview_id:S.preview.preview_id,allow_name_changes:S.allowNames})});
    S.committed=result.batch;S.upload=null;S.preview=null;
    if(result.stats)S.stats=result.stats;
    try{await refreshBase();}
    catch(e){throw new Error(`名单已保存，但目录刷新失败：${e.message}。请刷新后核对，勿重复导入。`);}
    if (S.hierarchy.length) S.openColleges.add(S.hierarchy[0].id);
    toast('名单已写入 SQLite 数据库。');
    window.scrollTo({top:0,behavior:'smooth'});
  } catch(e) {S.importError=e.message;toast(e.message,true);}
  finally {S.busy=false;render();}
}
async function loadStudents({refresh=false}={}) {
  directoryController.fitPageSize();
  const seq=++listSequence;
  listAbort?.abort();listAbort=new AbortController();
  const signal=listAbort.signal;
  const qs=new URLSearchParams();
  for (const [key,value] of Object.entries(S.filters)) if (value!==null&&value!=='') qs.set(key,String(value));
  const cacheKey=qs.toString(),cached=studentCache.get(cacheKey);
  const college=S.hierarchy.find(co=>co.id===S.filters.college_id),cl=college?.classes.find(cl=>cl.id===S.filters.class_id);
  const context=cl?cl.name:college?.name||'全校';
  if(!refresh&&cached&&cached.revision===S.stats.revision&&Date.now()-cached.time<CACHE_TTL){
    S.students=cached.data;S.studentsLoaded=true;S.listContext=context;S.filters.page=cached.data.page;
    S.listLoading=false;S.listError='';if(S.view==='scores')render();return;
  }
  S.listLoading=true;S.listError='';S.directoryStatsRecovered=false;S.directoryStatsError='';
  if (S.view==='scores') render();
  try {
    if(refresh&&S.directoryOutdated)S.health=await api('/api/health',{signal});
    S.directoryOutdated=Number(S.health?.directory_api_version||0)<6;
    if(S.directoryOutdated&&S.filters.score_filter==='below_60')throw new Error(DIRECTORY_UPDATE_MESSAGE);
    const [list]=await Promise.all([api(`/api/students?${qs}`,{signal}),refresh?refreshBase({includeHistory:false}):null]);
    let result=list;
    if (seq!==listSequence) return;
    if(Number.isInteger(result.revision)&&result.revision!==S.stats.revision){
      await refreshBase({includeHistory:false});
      if(seq!==listSequence)return;
      if(result.revision!==S.stats.revision)result=await api(`/api/students?${qs}`,{signal});
      if(seq!==listSequence)return;
      if(result.revision!==S.stats.revision)throw new Error('刷新期间名单持续变化，请再次读取，未混用不同版本的统计。');
    }
    const missingStats=['qualified','below_60','at_60'].some(key=>result[key]===undefined)||!Number.isInteger(result.valid_scores??result.settled);
    if(missingStats||S.directoryOutdated){
      try {
        const recovered=await completeDirectoryStatistics(api,qs.toString(),()=>seq===listSequence);
        if(seq!==listSequence||!recovered)return;
        result=recovered;S.directoryStatsRecovered=true;
      } catch(error){
        if(seq!==listSequence)return;
        S.directoryStatsError=error.message;
        result={...result,qualified:null,below_60:null,at_60:null};
      }
    }
    S.students=result;S.studentsLoaded=true;S.listContext=context;S.filters.page=result.page;
    S.directoryOutdated=Number(S.health?.directory_api_version||0)<6||missingStats;
    if(S.exportAllStudents)S.selectedStudentTotal=result.total;
    if(!missingStats&&!S.directoryOutdated){
      studentCache.set(cacheKey,{data:result,revision:result.revision??S.stats.revision,time:Date.now()});
      if(studentCache.size>30)studentCache.delete(studentCache.keys().next().value);
    }
  } catch(e) {
    if (seq!==listSequence||e.name==='AbortError') return;
    S.listError=S.directoryOutdated?DIRECTORY_UPDATE_MESSAGE:e.message;toast(S.listError,true);
  } finally {
    if (seq===listSequence) {S.listLoading=false;if(S.view==='scores')render();}
  }
}
async function openStudent(studentId) {
  try {
    const student=await api(`/api/students/${encodeURIComponent(studentId)}`);
    S.editing=student;
    const dialog=document.getElementById('student-dialog');
    mountHTML(dialog,editDialog(student));
    enhanceControls(dialog);dialog.showModal();
    document.getElementById('edit-status-trigger').focus();
  } catch(e) {toast(e.message,true);}
}
async function saveStudent(form) {
  if (!S.editing||S.saving||!form.reportValidity()) return;
  const text=document.getElementById('edit-rate').value.trim();
  const score=text===''?null:Number(text);
  const status=document.getElementById('edit-status').value;
  if (score!==null&&(!Number.isFinite(score)||score<0||score>100)) {
    document.getElementById('edit-error').textContent='得分率必须为 0–100 的数值。';return;
  }
  S.saving=true;
  let persisted=false;
  const submit=document.getElementById('save-student');submit.disabled=true;submit.textContent='正在保存…';
  try {
    await api(`/api/students/${encodeURIComponent(S.editing.student_id)}`,{method:'PATCH',body:JSON.stringify({version:S.editing.version,status,score_rate:score})});
    persisted=true;
    document.getElementById('student-dialog').close();S.editing=null;
    directoryController.clearStudentSelection();
    await refreshBase();await loadStudents();toast('修改已保存，刷新页面后仍然有效。');
  } catch(e) {
    const message=persisted?`修改已保存，但列表刷新失败：${e.message}。请刷新核对，勿重复保存。`:e.message;
    const error=document.getElementById('edit-error');if(error) error.textContent=message;
    toast(message,true);
  } finally {
    S.saving=false;
    if(submit.isConnected){submit.disabled=false;mountHTML(submit,icon('check')+'保存修改');}
  }
}

window.addEventListener('hashchange',changeView);
document.addEventListener('click',async event=>{
  if (event.target.closest('.skip-link')) {event.preventDefault();document.getElementById('main')?.focus();return;}
  const button=event.target.closest('[data-action]');
  if(!button||button.disabled) return;
  const action=button.dataset.action;
  try {
    switch(action) {
      case 'retry': await initialize(); break;
      case 'choose-file': document.getElementById('file-input')?.click(); break;
      case 'reset-import': resetImport(); break;
      case 'apply-header': await inspectHeader(); break;
      case 'preview': await makePreview(); break;
      case 'commit': await commitImport(); break;
      case 'goto-scores': gotoScores(); break;
      case 'check-scope': directoryController.toggleScope(button.dataset.scope,Number(button.dataset.id));break;
      case 'clear-export-selection': directoryController.clearScope();render();break;
      case 'check-student': directoryController.toggleStudent(button.dataset.student);break;
      case 'check-all-students': directoryController.toggleAllStudents();break;
      case 'clear-student-selection': directoryController.clearStudentSelection();render();break;
      case 'score-export': await directoryController.exportScores('scope');break;
      case 'score-export-students': await directoryController.exportScores('students');break;
      case 'score-export-below': await directoryController.exportScores('below');break;
      case 'tree-sort': {
        const key=button.dataset.sort;
        if(!['name','count'].includes(key))break;
        const current=S.treeSort;
        S.treeSort={key,order:current.key===key?(current.order==='asc'?'desc':'asc'):(key==='name'?'asc':'desc')};
        render();break;
      }
      case 'toggle-college': {
        const id=Number(button.dataset.id);
        if(S.openColleges.has(id))S.openColleges.delete(id);
        else S.openColleges.add(id);
        render();break;
      }
      case 'select-all':
        directoryController.clearStudentSelection();
        S.filters.college_id=null;S.filters.class_id=null;S.filters.page=1;await loadStudents();break;
      case 'select-college':
        directoryController.clearStudentSelection();
        S.filters.college_id=Number(button.dataset.id);S.filters.class_id=null;S.filters.page=1;
        S.openColleges.add(S.filters.college_id);await loadStudents();break;
      case 'select-class':
        directoryController.clearStudentSelection();
        S.filters.college_id=Number(button.dataset.college);S.filters.class_id=Number(button.dataset.id);
        S.filters.page=1;await loadStudents();break;
      case 'overview-college': gotoScores(Number(button.dataset.id)); break;
      case 'retry-overview': button.disabled=true;await refreshBase();render();break;
      case 'overview-sort': {
        const key=button.dataset.sort;
        if(!['name','classes','students','completed','qualified'].includes(key))break;
        const current=S.overviewSort;
        let order=key==='name'?'asc':'desc';
        if(current.key===key)order=current.order==='asc'?'desc':'asc';
        S.overviewSort={key,order};
        render();break;
      }
      case 'clear-filters': {
        directoryController.clearStudentSelection();
        const {college_id,class_id,page_size}=S.filters;
        S.filters={...defaultFilters(),college_id,class_id,page_size};
        await loadStudents();break;
      }
      case 'sort':
        S.filters.order=S.filters.sort===button.dataset.sort&&S.filters.order==='asc'?'desc':'asc';
        S.filters.sort=button.dataset.sort;S.filters.page=1;await loadStudents();break;
      case 'prev-page': if(S.students.page>1){S.filters.page=S.students.page-1;await loadStudents();}break;
      case 'next-page': if(S.students.page<S.students.pages){S.filters.page=S.students.page+1;await loadStudents();}break;
      case 'refresh-students': await loadStudents({refresh:true});break;
      case 'edit-student': if(!S.listLoading&&!S.listError)await openStudent(button.dataset.student);break;
      case 'close-dialog': if(!S.saving){closeControl();document.getElementById('student-dialog').close();S.editing=null;}break;
    }
  } catch(e) {toast(e.message,true);button.disabled=false;}
});
document.addEventListener('change',async event=>{
  const input=event.target;
  if(input.id==='file-input'){await upload(input.files?.[0]);return;}
  if(input.id==='sheet-select'){
    S.sheetIndex=Number(input.value);const sh=S.upload.sheets[S.sheetIndex];
    S.headerRow=sh.header_row;S.headerDraft=null;S.mapping={...sh.mapping};S.preview=null;S.importError='';render();return;
  }
  if(input.dataset.map){
    if(input.value==='')delete S.mapping[input.dataset.map];else S.mapping[input.dataset.map]=Number(input.value);
    S.preview=null;S.importError='';render();return;
  }
  if(input.name==='import-mode'){S.mode=input.value;S.preview=null;render();return;}
  if(input.id==='allow-name-changes'){S.allowNames=input.checked;render();return;}
  if(input.id==='status-filter'){directoryController.clearStudentSelection();S.filters.status=input.value;S.filters.page=1;await loadStudents();return;}
  if(input.id==='score-filter'){directoryController.clearStudentSelection();S.filters.score_filter=input.value;S.filters.page=1;await loadStudents();return;}
  if(input.id==='page-size'){
    S.pageMode=input.value==='auto'?'auto':'manual';
    if(S.pageMode==='manual')S.filters.page_size=Number(input.value);
    S.filters.page=1;await loadStudents();return;
  }
});
document.addEventListener('input',event=>{
  const input=event.target;
  if(input.id==='student-search'){
    directoryController.clearStudentSelection();
    S.filters.q=input.value;S.filters.page=1;clearTimeout(searchTimer);
    listSequence++;listAbort?.abort();S.listLoading=true;S.listError='';render();
    searchTimer=setTimeout(loadStudents,280);
  }
  if(input.id==='tree-search'){
    S.treeSearch=input.value;clearTimeout(treeTimer);treeTimer=setTimeout(render,150);
  }
  if(input.id==='header-row'){S.headerDraft=input.value;if(S.preview){S.preview=null;render();}}
});
document.addEventListener('submit',event=>{
  if(event.target.id==='student-edit-form'){event.preventDefault();saveStudent(event.target);}
  if(event.target.id==='page-jump-form'){
    event.preventDefault();
    if(S.listLoading||S.listError||!event.target.reportValidity())return;
    const page=Number(document.getElementById('page-jump').value);
    if(!Number.isInteger(page)||page<1||page>S.students.pages)return;
    if(page!==S.students.page){S.filters.page=page;loadStudents();}
  }
});
document.addEventListener('dragover',event=>{
  if(event.dataTransfer?.types.includes('Files'))event.preventDefault();
  event.target.closest('#dropzone')?.classList.add('dragging');
});
document.addEventListener('dragleave',event=>event.target.closest('#dropzone')?.classList.remove('dragging'));
document.addEventListener('drop',event=>{
  event.preventDefault();
  const zone=event.target.closest('#dropzone');zone?.classList.remove('dragging');
  if(!zone)return;
  const files=event.dataTransfer?.files;
  if(files?.length!==1){toast('请一次选择一个 Excel 文件。',true);return;}
  upload(files[0]);
});
const dialog=document.getElementById('student-dialog');
dialog.addEventListener('click',event=>{if(event.target===dialog&&!S.saving){closeControl();dialog.close();S.editing=null;}});
dialog.addEventListener('cancel',event=>{if(S.saving)event.preventDefault();else closeControl();});
const directoryController=installDirectoryController(S,{render,loadStudents,toast});
const scoreController=installScoreController(S,{api,render,toast,refreshBase});
installFollowUp(S,{api,toast});
installRemovalController(S,{api,render,toast,refreshBase,clearSelection:()=>{
  studentCache.clear();listSequence++;listAbort?.abort();
  S.listLoading=false;S.listError='';
  directoryController.clearStudentSelection();directoryController.clearScope();
}});
initialize();
