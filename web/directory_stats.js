// Compatibility for a still-running old service: count complete, read-only
// recorded-score pages, not just the visible page. Never substitute 0 for missing fields.
export async function completeDirectoryStatistics(api,query,isCurrent=()=>true) {
  for(let attempt=0;attempt<2;attempt++){
    if(!isCurrent())return null;
    const before=await api('/api/health');
    if(!Number.isInteger(before.revision))throw new Error('旧服务未提供数据版本，无法核对完整统计。');
    const snapshot=await api(`/api/students?${query}`);
    const expected=snapshot.recorded;
    if(!Number.isInteger(expected)||expected<0)throw new Error('旧服务未提供有效录入人数，无法核对统计。');
    const counts={qualified:0,at_90:0};
    const seen=new Set();let settled=0,changed=false;
    const scan=new URLSearchParams(query);
    scan.set('score_filter','recorded');scan.set('sort','student_id');scan.set('order','asc');scan.set('page_size','100');
    for(let page=1;page<=Math.ceil(expected/100);page++){
      if(!isCurrent())return null;
      scan.set('page',String(page));
      const result=await api(`/api/students?${scan}`);
      if(result.total!==expected||result.page!==page){changed=true;break;}
      for(const student of result.items){
        const rate=student.score_rate;
        if(seen.has(student.student_id)||typeof rate!=='number'||!Number.isFinite(rate)||rate<0||rate>100){changed=true;break;}
        seen.add(student.student_id);
        if(['未开始','进行中'].includes(student.status))continue;
        settled++;
        if(rate>90)counts.qualified++;
        else if(rate===90)counts.at_90++;
      }
      if(changed)break;
    }
    if(!isCurrent())return null;
    const after=await api('/api/health');
    if(!changed&&before.revision===after.revision&&seen.size===expected&&settled===snapshot.settled){
      return {...snapshot,...counts,below_90:snapshot.total-counts.qualified-counts.at_90};
    }
  }
  throw new Error('补算期间成绩发生变化，无法确认统计，请刷新后重新读取。');
}
