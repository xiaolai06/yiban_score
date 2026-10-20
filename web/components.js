export const statuses=['待录入','未开始','进行中','已完成','已录入','缺考','免考'];
export const h = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
export const num = (v) => Number(v || 0).toLocaleString('zh-CN');
export const percent = (v) => v === null || v === undefined ? '—' : `${Number(v).toLocaleString('zh-CN', {maximumFractionDigits: 2})}%`;
export const date = (v) => v ? new Date(v).toLocaleString('zh-CN', {hour12: false}) : '暂无记录';
export function duration(seconds) {
  if(typeof seconds!=='number'||!Number.isFinite(seconds)||seconds<0)return '—';
  const total=Math.floor(seconds),hours=Math.floor(total/3600),minutes=Math.floor(total%3600/60),second=total%60;
  return [hours,minutes,second].map(value=>String(value).padStart(2,'0')).join(':');
}
export const examTimeTitle = (row) => `开始：${row.exam_started_at||'未提供'}；结束：${row.exam_finished_at||'未提供'}；答题时长＝结束时间－开始时间（时:分:秒）`;
const paths = {
  cap: '<path d="m2 9 10-5 10 5-10 5L2 9Z"/><path d="M6 11v7c4 3 8 3 12 0v-7M22 9v7"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/><circle cx="9" cy="7" r="4"/>',
  chart: '<path d="M3 3v18h18M7 16v-4m5 4V7m5 9v-7"/>',
  book: '<path d="M4 3h6a3 3 0 0 1 3 3v15a4 4 0 0 0-4-2H3V3h1Zm9 3a3 3 0 0 1 3-3h5v16h-4a4 4 0 0 0-4 2"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  back: '<path d="M20 12H4m6-6-6 6 6 6"/>',
  school: '<path d="M3 21h18M5 21V8l7-5 7 5v13M9 21v-5h6v5M9 10h.01M15 10h.01"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8l10-5Zm-10 9 10 5 10-5M2 16l10 5 10-5"/>',
  rate: '<path d="m5 19 14-14"/><circle cx="7" cy="7" r="3"/><circle cx="17" cy="17" r="3"/>',
  file: '<path d="M14 2H5a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9l-7-7Z"/><path d="M14 2v7h7M7 13h10M7 17h10"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  minus: '<path d="M5 12h14"/>',
  shield: '<path d="m12 2 9 4v6c0 6-9 10-9 10S3 18 3 12V6l9-4Z"/><path d="m8 12 3 3 5-6"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 11v6m0-10h.01"/>',
  search: '<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 5 5"/>',
  edit: '<path d="m16 3 5 5M4 15 16 3a3 3 0 0 1 5 5L9 20l-6 1 1-6Z"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  refresh: '<path d="M20 7V2l-3 3a9 9 0 0 0-14 7m1 5v5l3-3a9 9 0 0 0 14-7M20 2h-5M4 22h5"/>',
  lock: '<rect x="4" y="10" width="16" height="12" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 5v3"/>',
  alert: '<path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  folder: '<path d="M3 5h6l2 2h10v13H3V5Z"/>',
  arrowup: '<path d="m6 14 6-6 6 6"/>',
  sort: '<path d="m8 9 4-4 4 4m-8 6 4 4 4-4"/>',
};
export const icon = (name, cls = '') => `<svg class="icon ${h(cls)}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.file}</svg>`;
export const badge = (status) => `<span class="badge ${({'待录入':'pending','已录入':'success','已完成':'success','未开始':'neutral','进行中':'progress','缺考':'absent','免考':'exempt'})[status] || ''}"><span class="badge-dot"></span>${h(status)}</span>`;
export function metrics(stats, completedMessage='统计中…') {
  const cards = [
    ['users','学生总数',num(stats.students),'以学号唯一识别','teal'],
    ['school','学院数量',num(stats.colleges),'从名单自动归集','blue'],
    ['layers','班级数量',num(stats.classes),'按所属学院分层','purple'],
    ['check','已完成人数',stats.completed==null?completedMessage:num(stats.completed),'有正式成绩，含 0 分','teal'],
  ];
  return `<div class="metrics">${cards.map(([i,t,v,n,c])=>`<div class="metric"><div class="metric-label">${h(t)}<span class="metric-icon ${c}">${icon(i)}</span></div><div class="metric-value ${t==='已完成人数'&&stats.completed==null?'is-pending':''}">${h(v)}</div><div class="metric-note">${h(n)}</div></div>`).join('')}</div>`;
}
export function pageHeading(_eyebrow, title, subtitle, actions='') {
  return `<div class="page-heading"><div class="heading-copy"><h1>${h(title)}</h1><p class="subtitle">${h(subtitle)}</p></div><div class="heading-actions">${actions}</div></div>`;
}
export const templateLink = (text='下载导入模板') => `<a class="btn secondary" href="/static/templates/student_template.xlsx" download>${icon('download')}${h(text)}</a>`;
export const blank = (title, text, action='') => `<div class="empty">${icon('folder')}<h3>${h(title)}</h3><p>${h(text)}</p>${action}</div>`;
