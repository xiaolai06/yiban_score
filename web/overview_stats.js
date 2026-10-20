// New servers supply these counts directly. Older running servers can still
// provide a complete, read-only count through their paginated student API.
import {PASS_SCORE,hasNumericScore,hasValidScore,isQualifiedScore} from './grade_values.js';
const PAGE_SIZE=100;
const changedMessage='统计期间成绩发生变化，请重新读取。';

function completeCounts(stats) {
  const valid=value=>Number.isInteger(value)&&value>=0;
  const colleges=stats.colleges_detail||[];
  return stats.pass_score===PASS_SCORE&&valid(stats.qualified)&&valid(stats.completed)&&
    colleges.every(co=>valid(co.qualified)&&valid(co.completed))&&
    colleges.reduce((sum,co)=>sum+co.qualified,0)===stats.qualified&&
    colleges.reduce((sum,co)=>sum+co.completed,0)===stats.completed;
}

async function countSnapshot(api,stats) {
  const colleges=stats.colleges_detail||[];
  const counts=new Map(colleges.map(co=>[co.id,{recorded:0,qualified:0,completed:0}]));
  const expected=stats.recorded;
  if(!Number.isInteger(expected)||expected<0||stats.revision==null){
    throw new Error('当前服务无法核对完整统计，请重启启动窗口后刷新。');
  }
  const seen=new Set();
  const pages=Math.max(1,Math.ceil(expected/PAGE_SIZE));
  for(let page=1;page<=pages;page++){
    const result=await api(`/api/students?score_filter=recorded&sort=student_id&order=asc&page_size=${PAGE_SIZE}&page=${page}`);
    if(result.total!==expected||result.page!==page||result.pages!==pages){
      throw new Error(changedMessage);
    }
    for(const student of result.items){
      const bucket=counts.get(student.college_id);
      if(!bucket||seen.has(student.student_id)||!hasNumericScore(student.score_rate)||typeof student.status!=='string'){
        throw new Error('成绩数据不完整，无法确认已完成及合格人数，请重新读取。');
      }
      seen.add(student.student_id);
      bucket.recorded++;
      if(isQualifiedScore(student))bucket.qualified++;
      if(hasValidScore(student))bucket.completed++;
    }
  }
  const latest=await api('/api/stats');
  if(latest.revision!==stats.revision||seen.size!==expected||
      colleges.some(co=>counts.get(co.id).recorded!==co.recorded)){
    throw new Error(changedMessage);
  }
  const details=colleges.map(co=>({...co,qualified:counts.get(co.id).qualified,completed:counts.get(co.id).completed}));
  return {...stats,pass_score:PASS_SCORE,qualified:details.reduce((sum,co)=>sum+co.qualified,0),
    completed:details.reduce((sum,co)=>sum+co.completed,0),colleges_detail:details};
}

export async function loadOverviewStats(api,stats) {
  let snapshot=stats;
  for(let attempt=0;attempt<2;attempt++){
    if(completeCounts(snapshot))return snapshot;
    try {
      return await countSnapshot(api,snapshot);
    } catch(error) {
      if(attempt===1)throw error;
      snapshot=await api('/api/stats');
    }
  }
}
