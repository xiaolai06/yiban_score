import {closeControl} from './custom_controls.js';

export const DIRECTORY_UPDATE_MESSAGE='当前启动窗口仍在运行旧后端。新版统一按得分率≥60（含60）合格，不合格仅为<60或无有效数值；未开始/进行中另列待跟进；请在启动窗口按 Ctrl+C，再重新启动网站，然后刷新本页。';
export const selectionFilters=S=>Object.fromEntries(['q','college_id','class_id','status','score_filter'].map(key=>[key,S.filters[key]]));
export const selectedStudentCount=S=>S.exportAllStudents?Math.max(0,S.selectedStudentTotal-S.exportExcludedStudents.size):S.exportStudents.size;
export const studentIsChecked=(S,id)=>S.exportAllStudents?!S.exportExcludedStudents.has(id):S.exportStudents.has(id);

export function installDirectoryController(S,{render,loadStudents,toast}) {
  let frame=0,observer=null,resizeTimer,heightKey='';
  const measuredHeights=new Map();
  const allIds=()=>S.hierarchy.flatMap(co=>co.classes.map(cl=>cl.id));
  function clearScope() {S.exportAll=false;S.exportClasses.clear();}
  function toggleScope(kind,id) {
    if(kind==='all'){
      if(S.exportAll)clearScope();
      else {S.exportAll=true;S.exportClasses=new Set(allIds());}
    } else {
      S.exportAll=false;
      const ids=kind==='college'?(S.hierarchy.find(co=>co.id===id)?.classes.map(cl=>cl.id)||[]):[id];
      const remove=ids.length&&ids.every(value=>S.exportClasses.has(value));
      for(const value of ids){if(remove)S.exportClasses.delete(value);else S.exportClasses.add(value);}
    }
    render();
  }
  function clearStudentSelection() {
    S.exportStudents.clear();S.exportExcludedStudents.clear();S.exportAllStudents=false;
    S.exportStudentFilter=null;S.selectedStudentTotal=0;
  }
  function toggleAllStudents() {
    if(S.listLoading||S.listError||!S.students.total)return;
    if(S.exportAllStudents&&!S.exportExcludedStudents.size)clearStudentSelection();
    else {
      clearStudentSelection();S.exportAllStudents=true;
      S.exportStudentFilter=selectionFilters(S);S.selectedStudentTotal=S.students.total;
    }
    render();
  }
  function toggleStudent(id) {
    if(S.exportAllStudents){
      if(S.exportExcludedStudents.has(id))S.exportExcludedStudents.delete(id);
      else S.exportExcludedStudents.add(id);
    } else if(S.exportStudents.has(id))S.exportStudents.delete(id);
    else S.exportStudents.add(id);
    if(!selectedStudentCount(S))clearStudentSelection();
    render();
  }
  function afterRender() {
    if(!observer){
      const main=document.getElementById('main');
      if(main){observer=new ResizeObserver(scheduleFit);observer.observe(main);}
    }
    scheduleFit();
  }
  function scheduleFit() {
    cancelAnimationFrame(frame);
    frame=requestAnimationFrame(()=>{
      if(S.view!=='scores'||S.pageMode!=='auto'||S.listLoading)return;
      if(fitPageSize())loadStudents();
    });
  }
  function fitPageSize() {
      if(S.view!=='scores'||S.pageMode!=='auto')return false;
      const area=document.querySelector('.score-table-scroll'),table=area?.querySelector('table');
      if(!area||!table)return;
      const header=table.querySelector('thead').getBoundingClientRect().height;
      const rowHeight=parseFloat(getComputedStyle(area).getPropertyValue('--score-row-height'))||53;
      const horizontal=area.scrollWidth>area.clientWidth?16:0;
      const key=JSON.stringify([selectionFilters(S),S.filters.sort,S.filters.order,S.students.revision,Math.round(area.clientWidth)]);
      if(key!==heightKey){heightKey=key;measuredHeights.clear();}
      const start=(S.students.page-1)*S.students.page_size;
      table.querySelectorAll('tbody tr[data-student-row]').forEach((row,index)=>{
        measuredHeights.set(start+index,Math.max(rowHeight,row.getBoundingClientRect().height));
      });
      // Names/classes may wrap. Keep measured boundary rows when a smaller page
      // omits them, so auto capacity neither cuts a row nor oscillates on resize.
      const available=area.getBoundingClientRect().height-header-horizontal-2;
      let capacity=0,used=0;
      while(capacity<100){
        const height=measuredHeights.get(start+capacity)||rowHeight;
        if(used+height>available)break;
        used+=height;capacity++;
      }
      capacity=Math.max(1,capacity);
      if(capacity!==S.filters.page_size){
        const anchor=(S.filters.page-1)*S.filters.page_size;
        S.filters.page_size=capacity;S.filters.page=Math.floor(anchor/capacity)+1;
        return true;
      }
      return false;
  }
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(scheduleFit,120);});
  function applyScope(payload) {
    if(S.exportAll)payload.all_students=true;
    else if(S.exportClasses.size)payload.class_ids=Array.from(S.exportClasses);
    else if(S.filters.class_id)payload.class_ids=[S.filters.class_id];
    else if(S.filters.college_id)payload.college_ids=[S.filters.college_id];
    else payload.all_students=true;
  }
  function applyStudents(payload) {
    if(S.exportAllStudents){payload.student_filter={...S.exportStudentFilter};payload.excluded_student_ids=Array.from(S.exportExcludedStudents);}
    else payload.student_ids=Array.from(S.exportStudents);
  }
  async function exportScores(mode='scope') {
    if(S.exportBusy||S.listLoading||S.listError)return;
    const payload={all_students:false,college_ids:[],class_ids:[],below_60:mode==='below'};
    if(mode==='students'){
      if(!selectedStudentCount(S)){toast('请勾选学生，或使用表头全选当前筛选的全部学生。',true);return;}
      applyStudents(payload);
    } else if(mode==='below'){
      if(selectedStudentCount(S))applyStudents(payload);
      else if(S.exportAll||S.exportClasses.size)applyScope(payload);
      else payload.student_filter=selectionFilters(S);
    } else applyScope(payload);
    if(Number(S.health?.read_snapshot_api_version||0)>=1&&Number.isInteger(S.students.revision))payload.expected_revision=S.students.revision;
    // Choosing an export completes this disclosure interaction. Closing it also
    // prevents the next click from hiding the menu instead of opening it again.
    const menu=document.querySelector('#score-list-tools .export-menu');
    if(menu){menu.removeAttribute('open');menu.querySelector('summary')?.focus({preventScroll:true});}
    closeControl();S.exportBusy=true;render();
    try {
      // Reject old servers that would silently omit students with no grade.
      if(payload.below_60||payload.student_filter?.score_filter==='below_60'){
        const healthResponse=await fetch('/api/health',{credentials:'same-origin',cache:'no-store'});
        if(!healthResponse.ok)throw new Error('无法核对不合格导出规则，请稍后重试。');
        const health=await healthResponse.json();S.health=health;
        if(Number(health.directory_api_version||0)<6){S.directoryOutdated=true;throw new Error(DIRECTORY_UPDATE_MESSAGE);}
      }
      const response=await fetch('/api/students/export.xlsx',{method:'POST',credentials:'same-origin',cache:'no-store',
        headers:{'Content-Type':'application/json','X-Requested-With':'StudentScore'},body:JSON.stringify(payload)});
      if(!response.ok){
        const error=await response.json();
        if([404,405].includes(response.status)&&['Not Found','Method Not Allowed'].includes(error.detail)){
          S.directoryOutdated=true;throw new Error(DIRECTORY_UPDATE_MESSAGE);
        }
        if(response.status===422&&S.directoryOutdated)throw new Error(DIRECTORY_UPDATE_MESSAGE);
        throw new Error(typeof error.detail==='string'?error.detail:'导出失败，请刷新列表后重新选择。');
      }
      const workbookVersion=Number(response.headers.get('X-Export-Workbook-Version')||0);
      if(!Number.isFinite(workbookVersion)||workbookVersion<5){
        throw new Error('当前后端尚未加载统一≥60合格规则及新版导出说明表。请在启动窗口按 Ctrl+C 后重新启动网站，再按 Ctrl+F5 刷新。');
      }
      const blob=await response.blob(),url=URL.createObjectURL(blob);
      const link=document.createElement('a');link.href=url;
      link.download=mode==='below'?'不合格名单-含无成绩.xlsx':mode==='students'?'勾选学生成绩.xlsx':'学院班级成绩.xlsx';
      document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
      toast(`已导出 ${response.headers.get('X-Export-Students')||''} 人，按班级分表，末尾附导出说明。${workbookVersion<6?'本次兼容旧版格式，不含答题时间。':''}`);
    } catch(error){toast(error.message||'无法连接服务，导出失败。',true);}
    finally {S.exportBusy=false;render();}
  }
  return {toggleScope,clearScope,toggleStudent,toggleAllStudents,clearStudentSelection,afterRender,exportScores,fitPageSize};
}
