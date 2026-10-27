import {h,badge} from './components.js';
import {hasNumericScore,isQualifiedScore} from './grade_values.js';

export const statusDescriptions={
  '待录入':'名单已建立，登记状态尚未补录；不等于缺考，也不据此推断是否参加考试。',
  '未开始':'导入或登记时记录为尚未开始答题；这是保存时的进度，不是易班平台的实时在线状态。',
  '进行中':'导入或登记时记录为正在答题，成绩可能尚未结算，需要核对后续交卷结果。',
  '已完成':'记录为考试已完成；不等于合格，仍需单独核对得分率是否达到60分。',
  '已录入':'成绩已登记，可来自报表或人工维护；不据此证明平台交卷情况，也不等于合格。',
  '缺考':'已明确登记为未参加考试；不会因为没有成绩而自动判定缺考。',
  '免考':'已明确登记为免于参加考试；不会自动补100分，也不自动认定成绩合格。',
};
export const statusDescription=status=>statusDescriptions[status]||'无法识别的原始状态，仅展示原值，不猜测其含义。';
export function scoreMeaning(row) {
  if(!hasNumericScore(row.score_rate))return '暂无有效数值成绩；空值不是0分，不能仅据此判断缺考。';
  const qualification=isQualifiedScore(row)?'分值合格（≥60）':'分值未达60分';
  return qualification+(['未开始','进行中'].includes(row.status)?'；进度尚未完成，暂存分数不计已完成人数和平均分。':'；状态与分值独立，真实0分会计入已结算统计。');
}
export function statusGuide() {
  return `<details class="score-status-guide" id="score-status-guide"><summary>状态与处理结果说明${h(' · 点击展开')}</summary><div class="status-guide-grid">${Object.entries(statusDescriptions).map(([status,text])=>`<article>${badge(status)}<p>${h(text)}</p></article>`).join('')}</div><div class="status-guide-note"><p>合格：有效数值得分率 ≥60（含60）；未开始 / 进行中的分数即使达到60，也仍需跟进考试进度，不计已完成和平均分。</p><p>异常类别：名单外学号＝导入当时未匹配正式名单；数据错误＝字段或时间未通过校验；姓名冲突＝学号匹配但姓名不同；重复学号冲突＝同一批清洗后学号相同，无法安全选取记录。历史异常不代表目前仍未解决。</p><p>批次处理：更新＝写入本次合并结果；保持不变＝未改当前值；保留原成绩＝较低分 / 未完成记录受保护；重复已合并＝同批选取安全记录。异常行均未写入正式成绩。已撤销仅表示批次操作被还原，不删除历史备案。</p></div></details>`;
}
