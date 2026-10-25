/** 当前评测和链路展示使用的中文标签。 */
export const ROUTE_LABELS: Record<string, string> = {
  single_agent: "单 Agent", multi_agent: "多 Agent", clarify: "追问", direct_response: "直接回复",
};

export const STAGE_LABELS = { rule: '规则', embedding: '向量', jev: 'Jev', llm: 'DeepSeek' } as const;
export type StageId = keyof typeof STAGE_LABELS;
export type StageStatus = 'returned' | 'continued' | 'skipped' | 'not_reached' | 'failed' | 'unrecorded';
export const STAGE_STATUS: Record<StageStatus, string> = {
  returned: '返回', continued: '继续下一层', skipped: '跳过', not_reached: '未到达', failed: '失败', unrecorded: '未记录',
};

export interface MetricDefinition {
  key: string; title: string; kind: "rate" | "score" | "duration"; lowerIsBetter?: boolean;
  description: string; formula: string;
}
const matchMetric = (key: string, title: string, description: string): MetricDefinition => ({
  key, title, kind: "rate", description, formula: "本项匹配次数 ÷ 标注了本项标准答案的尝试数",
});
const latencyMetric = (key: string, title: string, formula: string): MetricDefinition => ({
  key, title, kind: "duration", lowerIsBetter: true,
  description: "只统计已采集的单次意图阶段耗时，包括失败尝试；不包含后续路由和业务回答。", formula,
});
export const METRIC_GROUPS: Array<{ title: string; metrics: MetricDefinition[] }> = [
  { title: "意图分类质量", metrics: [
    matchMetric("intent_accuracy", "意图分类准确率 Accuracy", "主意图与人工标准意图相同才算分类正确。"),
    { key: "macro_f1", title: "Macro-F1", kind: "score", description: "对有标准样本的类别等权平均，观察各类别的分类表现。",
      formula: "各有标准样本类别的 F1 之和 ÷ 类别数；F1 = 2TP ÷ (2TP + FP + FN)" },
  ] },
  { title: "决策与任务分派", metrics: [
    matchMetric("route_accuracy", "路由正确率", "比较单 Agent、多 Agent、追问或直接回复的路由是否正确。"),
    matchMetric("agent_exact_match", "Agent 集合匹配", "Agent 集合必须完全相同；顺序不影响结果。"),
    matchMetric("sub_intents_exact_match", "子意图匹配", "仅在多 Agent 复合任务中记录组成意图，其他路由为空集合。"),
  ] },
  { title: "受理边界与拒绝策略", metrics: [
    matchMetric("policy_accuracy", "拒绝策略正确率", "比较允许或拒绝，两种拒绝原因统一按拒绝评分。"),
    { key: "false_rejection_rate", title: "误拒率", kind: "rate", lowerIsBetter: true,
      description: "支持范围内且不要求拒绝的请求，被识别成范围外或拒绝策略的比例；错误追问不计入误拒。",
      formula: "预测为 out_of_scope 或拒绝策略的次数 ÷ 支持范围内且不要求拒绝的尝试数" },
    { key: "false_acceptance_rate", title: "范围外误受理率", kind: "rate", lowerIsBetter: true,
      description: "标准属于范围外的请求，却被派给业务 Agent 的比例。",
      formula: "被派给单/多 Agent 的次数 ÷ 标准为 out_of_scope 的尝试数" },
    { key: "unsafe_acceptance_rate", title: "拒绝用例误受理率", kind: "rate", lowerIsBetter: true,
      description: "标准要求拒绝的请求，被允许或被派给业务 Agent 的比例。",
      formula: "策略为 allow 或被派给单/多 Agent 的次数 ÷ 标准要求拒绝的尝试数" },
  ] },
  { title: "耗时与执行稳定性", metrics: [
    latencyMetric("intent_p50_ms", "意图阶段 P50", "耗时升序排列，在位置 1 + (N − 1) × 50% 线性插值，即中位耗时"),
    latencyMetric("intent_p95_ms", "意图阶段 P95", "耗时升序排列，在位置 1 + (N − 1) × 95% 线性插值；不是第 95 名"),
    latencyMetric("intent_min_ms", "最短耗时", "已采集的意图阶段耗时的最小值"),
    latencyMetric("intent_max_ms", "最长耗时", "已采集的意图阶段耗时的最大值"),
    latencyMetric("intent_mean_ms", "平均耗时", "已采集的意图阶段耗时总和 ÷ 采集次数"),
    { key: "failure_rate", title: "调用失败率", kind: "rate", lowerIsBetter: true,
      description: "识别或路由执行产生错误码的比例；正常执行但判断错误不属于调用失败。",
      formula: "存在 error_code 的次数 ÷ 总尝试数" },
  ] },
];
