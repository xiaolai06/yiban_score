// Missing values and unfinished placeholders are never settled grades.
// Zero remains a valid numerical grade after settlement.
export const PASS_SCORE=60;
export function hasNumericScore(value) {
  return typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=100;
}
export function isQualifiedScore(student) {
  return hasNumericScore(student.score_rate)&&student.score_rate>=PASS_SCORE;
}
export function hasValidScore(student) {
  return hasNumericScore(student.score_rate)&&!['未开始','进行中'].includes(student.status);
}
