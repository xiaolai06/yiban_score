// Compatibility for a still-running old service: count complete, read-only
// recorded-score pages, not just the visible page. Never substitute 0 for missing fields.
import {hasNumericScore,hasValidScore} from './grade_values.js';
export async function completeDirectoryStatistics(api,query,isCurrent=()=>true) {
  for(let attempt=0;attempt<2;attempt++){
    if(!isCurrent())return null;
    const before=await api('/api/health');
    if(!Number.isInteger(before.revision))throw new Error('旧服务未提供数据版本，无法核对完整统计。');
    const snapshot=await api(`/api/students?${query}`);
    const expected=snapshot.recorded;
    if(!Number.isInteger(expected)||expected<0)throw new Error('旧服务未提供有效录入人数，无法核对统计。');
    const counts={qualified:0,at_90:0};
    const seen=new Set();let settled=0,scoreSum=0,changed=false,invalid=false;
    const scan=new URLSearchParams(query);
    scan.set('score_filter','recorded');scan.set('sort','student_id');scan.set('order','asc');scan.set('page_size','100');
    for(let page=1;page<=Math.ceil(expected/100);page++){
      if(!isCurrent())return null;
      scan.set('page',String(page));
      const result=await api(`/api/students?${scan}`);
      if(result.total!==expected||result.page!==page){changed=true;break;}
      for(const student of result.items){
        const rate=student.score_rate;
        if(seen.has(student.student_id)){changed=true;break;}
        seen.add(student.student_id);
        if(!hasNumericScore(rate)){invalid=true;continue;}
        if(!hasValidScore(student))continue;
        settled++;
        scoreSum+=rate;
        if(rate>90)counts.qualified++;
        else if(rate===90)counts.at_90++;
      }
      if(changed)break;
    }
    if(!isCurrent())return null;
    const after=await api('/api/health');
    const reported=snapshot.valid_scores??snapshot.settled;
    if(!changed&&before.revision===after.revision&&seen.size===expected&&(invalid||!Number.isInteger(reported)||settled===reported)){
      return {...snapshot,...counts,settled,valid_scores:settled,average_rate:settled?Math.round(scoreSum/settled*100)/100:null,below_90:snapshot.total-counts.qualified-counts.at_90};
    }
  }
  throw new Error('补算期间成绩发生变化，无法确认统计，请刷新后重新读取。');
}
