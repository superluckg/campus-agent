export interface CaseView { case_id: string; case_number: number; case_label: string; payload: any }
export interface UnifiedResult { result_id: string; case: CaseView; trial_index: number; trace_id: string;
  status: string; prediction: any; intent_metrics: Record<string, number>; retrieval: any; generation: any;
  duration_ms: number; error_code?: string; error_message?: string; visible_context?: any[] }
export interface CurrentRun { run_id: string; status: string; repeats: number; started_at: string;
  progress: { total_attempts: number; completed_attempts: number; current_case?: string; trial_index?: number; stage: string };
  summary_metrics: any; error_message?: string; stop_requested: boolean }
export const RETRIEVAL_METRICS = [
  ['hit', 'Hit@5'], ['precision', 'Precision@5'], ['unit_recall', '片段召回率@5'], ['mrr', 'MRR@5'],
  ['evidence_item_recall', '证据项召回率@5'], ['evidence_coverage', '证据文本覆盖率@5'],
] as const;
export const GENERATION_METRICS = [
  ['correctness', '正确性'], ['faithfulness', '忠实度'], ['relevance', '相关性'], ['completeness', '完整性'],
  ['helpfulness', '实用性'], ['refusal_quality', '拒答质量'],
] as const;
export const percent = (value: unknown) => typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '不适用／未评分';
export const score = (value: unknown) => typeof value === 'number' ? `${value.toFixed(2)} / 5` : '不适用／未评分';
export function groupResults(rows: UnifiedResult[]) {
  const map = new Map<string, { case: CaseView; rows: UnifiedResult[] }>();
  for (const row of rows) {
    if (!map.has(row.case.case_id)) map.set(row.case.case_id, { case: row.case, rows: [] });
    map.get(row.case.case_id)!.rows.push(row);
  }
  return [...map.values()].sort((a, b) => a.case.case_number - b.case.case_number)
    .map(group => ({ ...group, rows: group.rows.sort((a, b) => a.trial_index - b.trial_index) }));
}
export function average(rows: UnifiedResult[], getter: (row: UnifiedResult) => unknown): number | undefined {
  const values = rows.map(getter).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  return values.length ? values.reduce((sum, v) => sum + v, 0) / values.length : undefined;
}
export const EVAL_MATCH_FIELDS = [
  { key: 'intent_accuracy', reference: 'expected_intent', actual: 'primary_intent', title: '主意图' },
  { key: 'route_accuracy', reference: 'expected_route', actual: 'route', title: '路由' },
  { key: 'agent_exact_match', reference: 'expected_agents', actual: 'agents', title: '助手集合' },
  { key: 'sub_intents_exact_match', reference: 'expected_sub_intents', actual: 'sub_intents', title: '子意图' },
  { key: 'policy_accuracy', reference: 'expected_policy', actual: 'policy_decision', title: '处理策略' },
] as const;
export const EVAL_POLICY_LABELS = { allow: '允许', refuse: '拒绝' };
export const EVAL_AGENT_LABELS: Record<string, string> = { CampusAgent: '校园助手', CareerAgent: '就业助手', LearningAgent: '学习助手' };
export const EVAL_ROUTE_LABELS: Record<string, string> = { single_agent: '单助手', multi_agent: '多助手', clarify: '追问', direct_response: '直接回复' };
export const AGENT_FOR_INTENT: Record<string, string> = { campus_network: 'CampusAgent', campus_affairs: 'CampusAgent', career_recruitment: 'CareerAgent', learning_plan: 'LearningAgent' };
export const BEHAVIORS = [
  { value: 'answer', label: '正确回答' }, { value: 'partial_answer', label: '部分回答' },
  { value: 'clarify', label: '追问' }, { value: 'refuse', label: '拒答' },
  { value: 'explain_limitation', label: '说明知识限制' }, { value: 'small_talk', label: '闲聊' },
];
export interface CaseEvidence {
  source_kind: 'knowledge_file' | 'official_web'; filename: string; source_key: string; source_url: string;
  page_number: number | null; section_path: string; content: string; related_intents: string[];
}
export interface CasePayload {
  query: string; expected_intent: string; expected_behavior: string; reference_answer: string;
  evidence: CaseEvidence[]; conversation_context: Array<{ role: 'user' | 'assistant'; content: string }>;
  expected_sub_intents: string[];
}
export const blankCase = (): CasePayload => ({
  query: '', expected_intent: 'small_talk', expected_behavior: 'small_talk', reference_answer: '',
  evidence: [], conversation_context: [], expected_sub_intents: [],
});
export const blankEvidence = (): CaseEvidence => ({ source_kind: 'knowledge_file', filename: '', source_key: '', source_url: '', page_number: null, section_path: '', content: '', related_intents: [] });
export function editableCase(value: CasePayload): CasePayload {
  return { ...blankCase(), ...structuredClone(value), evidence: value.evidence.map(e => ({ ...blankEvidence(), ...e })) };
}
export function decisionReference(value: CasePayload) {
  const intent = value.expected_intent, parts = value.expected_sub_intents ?? [];
  const policy = value.expected_behavior === 'refuse' ? 'refuse' : 'allow';
  const route = policy === 'refuse' || ['small_talk', 'out_of_scope'].includes(intent) ? 'direct_response'
    : intent === 'unclear' ? 'clarify' : intent === 'compound' ? 'multi_agent' : 'single_agent';
  return { expected_intent: intent, expected_policy: policy as 'allow' | 'refuse', expected_route: route, expected_sub_intents: parts,
    expected_agents: route === 'multi_agent' ? [...new Set(parts.map(p => AGENT_FOR_INTENT[p]))].sort()
      : route === 'single_agent' ? [AGENT_FOR_INTENT[intent]] : [] };
}
export function changeGold(value: CasePayload, field: 'expected_intent' | 'expected_sub_intents', selection: string | string[]): CasePayload {
  const next = structuredClone(value);
  if (field === 'expected_sub_intents') next.expected_sub_intents = selection as string[];
  else {
    next.expected_intent = selection as string;
    if (selection !== 'compound') next.expected_sub_intents = [];
    next.expected_behavior = selection === 'unclear' ? 'clarify' : selection === 'small_talk' ? 'small_talk'
      : selection === 'out_of_scope' ? 'explain_limitation' : 'answer';
  }
  if (next.expected_intent !== 'compound') next.evidence.forEach(e => { e.related_intents = []; });
  return next;
}
export function validateCase(value: CasePayload): string[] {
  const errors: string[] = [];
  const parts = value.expected_intent === 'compound' ? value.expected_sub_intents : [value.expected_intent];
  if (!value.query.trim()) errors.push('请填写问题。');
  if (value.query.length > 4000) errors.push('问题最多四千字。');
  if (!value.reference_answer.trim()) errors.push('请填写参考回答。');
  if (value.reference_answer.length > 30000) errors.push('参考回答最多三万字。');
  if (value.expected_intent === 'compound' && (parts.length !== 2 || new Set(parts.map(p => AGENT_FOR_INTENT[p])).size !== 2 || parts.some(p => !AGENT_FOR_INTENT[p]))) errors.push('复合子意图请选择两个不同助手领域。');
  if (value.conversation_context.length > 8) errors.push('历史消息最多八条。');
  value.conversation_context.forEach((m, i) => { if (!m.content.trim()) errors.push(`第${i + 1}条历史消息不能为空。`); if (m.content.length > 12000) errors.push(`第${i + 1}条历史消息最多一万两千字。`); });
  if (value.evidence.length > 50) errors.push('标准证据最多五十条。');
  value.evidence.forEach((e, i) => {
    const prefix = `第${i + 1}条证据：`;
    if (e.source_kind === 'knowledge_file' ? !e.filename : !e.source_key) errors.push(prefix + '请选择资料。');
    if (e.content.normalize('NFKC').replace(/[^\p{L}\p{N}]/gu, '').length < 4) errors.push(prefix + '原文至少需要四个有效文字字符。');
    if (e.content.length > 30000) errors.push(prefix + '原文最多三万字。');
    if ((value.expected_intent === 'compound' && !e.related_intents.length) || new Set(e.related_intents).size !== e.related_intents.length || e.related_intents.some(p => !parts.includes(p))) errors.push(prefix + '请重新选择当前关联子任务。');
  });
  return errors;
}
