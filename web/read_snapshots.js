// Separate endpoints must agree on a database revision before publishing a base view.
export async function readBaseSnapshot(api,{includeHistory=true,isCurrent=()=>true}={}){
  for(let attempt=0;attempt<2;attempt++){
    const [stats,hierarchy,history]=await Promise.all([
      api('/api/stats'),api('/api/hierarchy'),includeHistory?api('/api/imports'):null,
    ]);
    if(!isCurrent())return null;
    // Old services do not have a hierarchy revision; their existing read-only
    // compatibility path remains available, without inventing a revision.
    if(Number.isInteger(hierarchy.revision)&&Number.isInteger(stats.revision)&&hierarchy.revision!==stats.revision)continue;
    return {stats,hierarchy,history};
  }
  throw new Error('读取期间名单发生变化，请重新刷新，未使用不一致的目录和统计。');
}
