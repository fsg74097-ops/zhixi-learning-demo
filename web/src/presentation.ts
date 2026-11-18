import type { Metric } from './models.ts';
import { entries, object } from './models.ts';
export function formatRate(value: number | null | undefined) { return value == null ? '未观察' : `${Number((value*100).toFixed(1))}%`; }
export function formatScore(numerator: number | null | undefined, denominator: number | null | undefined) { return numerator == null || denominator == null ? '未评估' : `${numerator} / ${denominator}`; }
export function metricValue(metric: Metric) {
  if ('rate' in metric) return formatRate(metric.rate);
  if (typeof metric.value === 'string' || typeof metric.value === 'number') return String(metric.value);
  if (metric.numerator != null) return formatScore(metric.numerator, metric.denominator);
  return '未观察';
}
export function evidenceIds(value: unknown): string[] {
  const ids = entries(value).map(ref => typeof ref === 'string' ? ref : object(ref).id ?? object(ref).evidence_id);
  return [...new Set(ids.filter((id): id is string => typeof id === 'string' && id.trim() !== ''))];
}
export function metricNote(metric: Metric) { return [metric.n == null ? '' : `n=${metric.n}`, metric.coverage == null ? '' : `覆盖 ${formatRate(metric.coverage)}`, metric.excluded_count ? `排除 ${metric.excluded_count}` : ''].filter(Boolean).join(' · '); }
export function stateLabel(state?:string) { return ({DRAFT:'编辑中',VALIDATION_PENDING:'正在复核',NEEDS_REVIEW:'待教师确认',SUBMITTED_FOR_REVIEW:'教师已确认',EXPORTED:'已导出',INVALID:'需修改',STALE:'依据已更新',PASSED:'自动复核通过',RECHECK_REQUIRED:'需要内容复核',APPROVED_PRACTICE:'已授权练习',ASSESSMENT_RESERVED:'测评保留题',GENERATED_CANDIDATE:'待审核候选',AI_CANDIDATE:'AI核对候选',HUMAN_VERIFIED:'已人工核对',VALID:'数学等价',UNRESOLVED:'待核对'} as Record<string,string>)[state??''] ?? state ?? '待核对'; }
export function message(error:unknown) { return error instanceof Error ? error.message : '本次操作未完成，请稍后重试。'; }
