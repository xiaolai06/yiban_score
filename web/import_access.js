import {h, icon, pageHeading} from './components.js';

export const isImportView = key => ['import','score-import'].includes(key);
export const importLocked = S => S.importAccess?.enabled && !S.importAccess.unlocked;

export function importAccessView(S) {
  const title=S.view==='score-import'?'导入成绩':'名单导入';
  return pageHeading('IMPORT ACCESS',title,'此页面需要导入密码。成绩明细和数据总览继续开放。')+
    `<section class="card import-access-card" aria-labelledby="import-access-title">
      <div class="import-access-symbol">${icon('shield')}</div>
      <h2 id="import-access-title">验证导入密码</h2>
      <p class="small-muted">验证后可使用学生名单导入、成绩导入及名单剔除。<br>本浏览器验证有效期为 8 小时，可随时退出。</p>
      <form id="import-access-form" method="post" action="/api/import-access/unlock">
        <div class="form-field"><label for="import-access-password">导入密码</label>
          <input id="import-access-password" name="password" type="password" autocomplete="current-password" maxlength="256" required ${S.importAccessBusy?'disabled':''}></div>
        <div class="form-error" role="alert">${h(S.importAccessError||'')}</div>
        <button class="btn primary" type="submit" ${S.importAccessBusy?'disabled':''}>${S.importAccessBusy?'正在验证…':'验证并进入'}</button>
      </form>
      <a class="import-access-back" href="#scores">先查看成绩明细${icon('arrow')}</a>
    </section>`;
}

export function importAccessToolbar(S) {
  if(!S.importAccess?.enabled)return '';
  const busy=S.busy||S.score?.busy||S.removal?.busy||S.importAccessBusy;
  return `<div class="import-access-toolbar"><span>${icon('shield')}导入已解锁 · 本浏览器有效期 8 小时</span><button class="btn secondary small" data-action="lock-import-access" ${busy?'disabled':''}>退出导入验证</button></div>`;
}
