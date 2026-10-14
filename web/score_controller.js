import {newScoreState} from './score_views.js';
import {installScoreIssues} from './score_issues.js';

export function installScoreController(S,{api,render,toast,refreshBase}) {
  let rowSeq=0, auditSeq=0;
  const issues=installScoreIssues({api});
  const g=()=>S.score;
  const invalidate=()=>{g().preview=null;g().skip=false;g().error='';rowSeq++;};
  async function work(fn) {
    if(g().busy)return;
    g().busy=true;g().error='';render();
    try{await fn();}catch(e){g().error=e.message;toast(e.message,true);}
    finally{g().busy=false;render();}
  }
  async function history(page=1){g().batches=await api(`/api/scores/batches?page=${page}&page_size=${g().batches.page_size||10}`);}
  async function rows(kind='all',page=1){
    const id=g().preview?.preview_id;if(!id)return;
    const seq=++rowSeq;
    const result=await api(`/api/scores/previews/${id}/rows?kind=${encodeURIComponent(kind)}&page=${page}`);
    if(seq!==rowSeq||g().preview?.preview_id!==id)return;
    Object.assign(g(),{rows:result.items,rowTotal:result.total,rowPage:page,rowKind:kind});
  }
  async function audit(id,kind='all',page=1){
    const seq=++auditSeq;const result=await api(`/api/scores/batches/${id}/rows?kind=${encodeURIComponent(kind)}&page=${page}`);
    if(seq===auditSeq)g().audit={...result,kind};
  }
  async function upload(file){
    if(!file)return;
    if(!/\.(csv|tsv|xls|xlsx)$/i.test(file.name)){toast('请选择 CSV、TSV、XLS 或 XLSX 成绩文件。',true);return;}
    if(file.size>20*1024*1024){toast('成绩文件不能超过 20 MB。',true);return;}
    await work(async()=>{
      invalidate();g().committed=null;g().upload=null;g().audit=null;
      const form=new FormData();form.append('file',file);
      const u=await api('/api/scores/analyze',{method:'POST',body:form});
      const sh=u.sheets[u.selected_sheet];
      Object.assign(g(),{upload:u,sheetIndex:u.selected_sheet,headerRow:sh.header_row,headerDraft:null,mapping:{...sh.mapping},name:file.name.replace(/\.[^.]+$/,'').slice(0,120)});
      toast(`已识别 ${u.format.encoding}，请核对列映射与清洗规则。`);
    });
  }
  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-action^="score-"]');if(!button||button.disabled)return;
    const action=button.dataset.action;const G=g();
    if(G.busy&&!['score-audit-close'].includes(action))return;
    try{
      switch(action){
        case 'score-choose':document.getElementById('score-file')?.click();return;
        case 'score-reset':{const H=G.batches;S.score=newScoreState();S.score.batches=H;rowSeq++;auditSeq++;render();return;}
        case 'score-header':await work(async()=>{
          const header=Number(document.getElementById('score-header')?.value??G.headerDraft??G.headerRow);
          if(!Number.isInteger(header)||header<1)throw new Error('表头行必须是正整数。');
          const sh=await api(`/api/scores/uploads/${G.upload.upload_id}/sheet?sheet_index=${G.sheetIndex}&header_row=${header}`);
          G.upload.sheets[G.sheetIndex]=sh;G.headerRow=header;G.headerDraft=null;G.mapping={...sh.mapping};invalidate();
        });return;
        case 'score-preview':await work(async()=>{
          if(G.headerDraft!==null&&Number(G.headerDraft)!==G.headerRow)throw new Error('表头行已修改，请先点击“重新识别”。');
          invalidate();
          G.preview=await api('/api/scores/preview',{method:'POST',body:JSON.stringify({upload_id:G.upload.upload_id,sheet_index:G.sheetIndex,header_row:G.headerRow,mapping:G.mapping,batch_name:G.name,scale:G.scale,zero_policy:G.zeroPolicy,update_mode:'keep_highest',protect_completed:true})});
          await rows();
          const summary=G.preview.summary;
          toast(`校验完成：新增 ${summary.new_scores||0} 条，更新 ${summary.update-(summary.new_scores||0)} 条，异常 ${summary.issue_rows} 条。`);
        });document.getElementById('score-preview')?.scrollIntoView({behavior:'smooth',block:'start'});return;
        case 'score-commit':await work(async()=>{
          if(!G.preview) return;
          const body={preview_id:G.preview.preview_id,skip_issues:G.skip};
          if(!G.preview.can_commit&&G.preview.can_save_issues)body.save_issues_only=true;
          const result=await api('/api/scores/commit',{method:'POST',body:JSON.stringify(body)});
          G.committed=result.batch;G.preview=null;G.upload=null;G.audit=null;rowSeq++;
          try{await Promise.all([refreshBase(),history()]);}
          catch(e){throw new Error(`成绩记录已保存，但列表刷新失败：${e.message}。请刷新后核对，勿重复导入。`);}
          const summary=result.batch.summary;
          issues.refresh();
          toast(result.batch.options?.save_issues_only?`已保存 ${summary.issue_rows} 条异常记录，正式成绩未修改。`:`本次记录已保存：新增 ${summary.new_scores||0} 条，更新 ${result.batch.updated_count-(summary.new_scores||0)} 条。`);
          window.scrollTo({top:0,behavior:'smooth'});
        });return;
        case 'score-tab':await work(()=>rows(button.dataset.kind,1));return;
        case 'score-rows-prev':await work(()=>rows(G.rowKind,Math.max(1,G.rowPage-1)));return;
        case 'score-rows-next':await work(()=>rows(G.rowKind,G.rowPage+1));return;
        case 'score-history-refresh':await work(()=>history(G.batches.page));return;
        case 'score-history-prev':await work(()=>history(Math.max(1,G.batches.page-1)));return;
        case 'score-history-next':await work(()=>history(G.batches.page+1));return;
        case 'score-audit':await work(()=>audit(Number(button.dataset.id)));document.getElementById('score-audit')?.scrollIntoView({behavior:'smooth'});return;
        case 'score-audit-close':G.audit=null;auditSeq++;render();return;
        case 'score-audit-tab':await work(()=>audit(G.audit.batch.id,button.dataset.kind,1));return;
        case 'score-audit-prev':await work(()=>audit(G.audit.batch.id,G.audit.kind,Math.max(1,G.audit.page-1)));return;
        case 'score-audit-next':await work(()=>audit(G.audit.batch.id,G.audit.kind,G.audit.page+1));return;
        case 'score-revert':{
          const id=Number(button.dataset.id);
          if(!window.confirm(`确认撤销成绩导入批次 #${id}？\n仅恢复本批修改的状态与得分率；如学生已有后续修改，将整批拒绝撤销。`))return;
          await work(async()=>{
            const result=await api(`/api/scores/batches/${id}/revert`,{method:'POST',body:JSON.stringify({confirm:true})});
            invalidate();G.committed=null;G.audit=null;
            await Promise.all([refreshBase(),history(G.batches.page)]);issues.refresh();toast(`已撤销批次，恢复 ${result.reverted_count} 位学生。`);
          });return;
        }
      }
    }catch(e){toast(e.message,true);}
  });
  document.addEventListener('change',async event=>{
    const t=event.target,G=g();
    if(t.id==='score-file'){await upload(t.files?.[0]);return;}
    if(G.busy)return;
    if(t.id==='score-sheet'){
      G.sheetIndex=Number(t.value);const sh=G.upload.sheets[G.sheetIndex];G.headerRow=sh.header_row;G.headerDraft=null;G.mapping={...sh.mapping};invalidate();render();return;
    }
    if(t.dataset.scoreMap){if(t.value==='')delete G.mapping[t.dataset.scoreMap];else G.mapping[t.dataset.scoreMap]=Number(t.value);invalidate();render();return;}
    const map={'score-scale':'scale','score-zero':'zeroPolicy'};
    if(map[t.id]){G[map[t.id]]=t.value;invalidate();render();return;}
    if(t.id==='score-skip'){G.skip=t.checked;render();return;}
  });
  document.addEventListener('input',event=>{
    const t=event.target,G=g();
    if(t.id==='score-name'){G.name=t.value;if(G.preview){invalidate();render();}}
    if(t.id==='score-header'){G.headerDraft=t.value;if(G.preview){invalidate();render();}}
  });
  document.addEventListener('dragover',e=>{const zone=e.target.closest('#score-dropzone');if(zone){e.preventDefault();zone.classList.add('dragging');}});
  document.addEventListener('dragleave',e=>e.target.closest('#score-dropzone')?.classList.remove('dragging'));
  document.addEventListener('drop',async e=>{
    const zone=e.target.closest('#score-dropzone');if(!zone)return;e.preventDefault();zone.classList.remove('dragging');
    if(e.dataTransfer?.files.length!==1){toast('一次请选择一个成绩文件。',true);return;}await upload(e.dataTransfer.files[0]);
  });
  return {history};
}
