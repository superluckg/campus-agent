export interface Span {
  span_id: string; parent_span_id?: string; node: string; label: string; status: string;
  started_at: string; finished_at?: string; duration_ms?: number; background?: boolean;
  agent?: string; tool?: string; output?: any; inputs?: any; error_message?: string; [key: string]: any;
}
export const STATUS: Record<string, string> = {
  success: '成功', failed: '失败', partial: '部分完成', degraded: '降级完成', running: '执行中',
  cancelled: '已中止', skipped: '跳过', not_reached: '未到达', unrecorded: '未记录',
  completed: '已完成', queued: '排队中', stopped: '已停止', interrupted: '服务重启中断',
  not_applicable: '不适用', upstream_blocked: '上游阻断', missing: '缺少预期检索',
  execution_failed: '执行失败', judge_failed: '评分失败',
};
export const seconds = (value: unknown) => typeof value === 'number' && Number.isFinite(value)
  ? `${(value / 1000).toFixed(2)} 秒` : '未记录';
export const spanDuration = (value: unknown) => typeof value === 'number' && Number.isFinite(value)
  ? value < 1000 ? `${value.toFixed(2)} 毫秒` : seconds(value) : '未记录';
export function timingSeconds(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '未记录';
  return value > 0 && value < 1 ? '<0.001 秒' : `${(value / 1000).toFixed(3)} 秒`;
}
export function cumulativeDuration(span: Span, requestStart: number): string {
  if (span.status === 'running') return '执行中';
  const end = Date.parse(span.finished_at ?? '');
  return timingSeconds(Number.isFinite(requestStart) && Number.isFinite(end) ? end - requestStart : undefined);
}
export interface SpanRow extends Span { children?: SpanRow[]; synthetic?: boolean }

/** 从父跨度补齐调用上下文，按实际父子关系展示调用与检索批次。 */
export function spanRows(spans: Span[], retrievalOnly = false): SpanRow[] {
  const all = new Map(spans.map(s => [s.span_id, s]));
  const rows = spans.filter(s => !s.background && (!retrievalOnly
    || s.node === 'tool' || s.node.startsWith('retrieval.'))).map(s => {
    const row: SpanRow = { ...s };
    const seen = new Set([s.span_id]);
    let parent = all.get(s.parent_span_id ?? '');
    while (parent && !seen.has(parent.span_id)) {
      seen.add(parent.span_id);
      for (const key of ['agent', 'tool', 'round_number', 'query_index', 'source', 'protocol']) {
        if (row[key] == null && parent[key] != null) row[key] = parent[key];
      }
      parent = all.get(parent.parent_span_id ?? '');
    }
    return row;
  }).sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at));
  const included = new Map(rows.map(s => [s.span_id, s]));
  const roots: SpanRow[] = [];
  const calls = new Map<string, number>();
  for (const row of rows) {
    if (row.node === 'tool') {
      const key = `${row.agent ?? ''}:${row.tool ?? row.label}`;
      const number = (calls.get(key) ?? 0) + 1;
      calls.set(key, number);
      row.label = `${row.label} 第 ${number} 次调用`;
    }
    const seen = new Set([row.span_id]);
    let parent = all.get(row.parent_span_id ?? '');
    while (parent && !included.has(parent.span_id) && !seen.has(parent.span_id)) {
      seen.add(parent.span_id);
      parent = all.get(parent.parent_span_id ?? '');
    }
    const target = parent && !seen.has(parent.span_id) ? included.get(parent.span_id) : undefined;
    if (target) (target.children ??= []).push(row);
    else roots.push(row);
  }
  for (const tool of rows.filter(s => s.node === 'tool')) {
    for (const child of tool.children ?? []) {
      if (child.node === 'retrieval.retrieve') {
        child.label = child.status === 'skipped' ? '复用成功查询缓存'
          : `第 ${child.round_number} 批实际检索`;
      }
    }
  }
  return roots;
}
const NODES = [
  ['load_context', '加载上下文'], ['recognize_intent', '意图识别'], ['route_request', '路由决策'],
  ['clarify', '追问回复'], ['direct_response', '直接回复'], ['recall_memory', '长期记忆注入'],
  ['select_skills', 'Skills选择'], ['execute_agents', 'Agent执行阶段'], ['compose', '汇总回答'],
];
export function chainRows(spans: Span[], route?: string | null, traceStatus?: string) {
  const observed = spans.filter(s => !s.background);
  const failedAt = NODES.findIndex(([node]) => observed.some(s => s.node === node && ['failed', 'cancelled'].includes(s.status)));
  const branch = route === 'clarify' ? ['clarify'] : route === 'direct_response' ? ['direct_response']
    : route ? ['recall_memory', 'select_skills', 'execute_agents', 'compose'] : [];
  return NODES.map(([node, label], index) => {
    const actual = observed.find(s => s.node === node);
    if (actual) return actual;
    const status = !observed.length ? 'unrecorded' : index > 2 && route && !branch.includes(node) ? 'skipped'
      : failedAt >= 0 && index > failedAt ? 'not_reached' : traceStatus === 'running' ? 'not_reached' : 'unrecorded';
    return { span_id: node, node, label, status, started_at: '', error_message:
      status === 'skipped' ? '此路由不执行该分支。' : status === 'not_reached' ? '本次执行未到达此步骤。' : '未采集此步骤信息。' } as Span;
  });
}
const FIELD_LABELS: Record<string, string> = {
  protocol: '运行协议', total_rounds: '累计实际检索批次', call_number: '调用序号',
  actions: '模型输出动作', arguments: '工具参数', action: '动作',
  evidence_id: '证据标识', render_citations: '正文引用格式化', citation_warning: '引用核验提示',
  invalid_evidence_ids: '无法定位的证据标识', budget_exhausted: '检索预算已用完',
  query: '查询', primary_intent: '主意图', confidence: '置信度', entities: '实体', missing_fields: '缺失字段',
  candidate_scores: '候选分数', sub_intents: '复合子意图', policy_decision: '处理策略', decision_stage: '返回阶段',
  source_scores: '信号分数', signal_errors: '信号错误', signal_durations_ms: '信号耗时（毫秒）',
  stage_trace: '阶段记录', model_usage: '模型用量', model_calls: '模型调用次数', decision_details: '决策详情',
  route: '路由', reason_summary: '决策依据', assignments: '任务分派', agent_name: 'Agent', focus: '任务范围',
  clarification_question: '追问内容', conversation_summary: '会话摘要', recent_messages: '近期消息',
  long_term_memories: '长期记忆', recalled_memories: '已注入记忆', available_files: '可用附件',
  selected: '已选择', selected_skills: '已选择Skills', content: '内容', reason: '原因', status: '状态',
  documents: '实际返回片段', diagnostics: '检索诊断', metadata: '元数据', page_content: '原文',
  domain: '领域', category: '文档类型', source_url: '来源网址',
  filename: '文件名', page_number: '页码', section_path: '章节', title: '标题', source_key: '官网来源',
  page_numbers: '涉及的物理页码', page_spans: '页内原文映射', page_start_index: '该页内起始字符',
  start_index: '原文起始字符', end_index: '原文结束字符',
  evidence_sufficient: '证据充分', unresolved_points: '未解决要点', tool_records: '工具记录', citations: '引用',
  scores: '分数', threshold: '阈值', accepted: '接受结果', margin: '分差', predicted_intent: '预测意图',
  label: '阶段', output: '输出', inputs: '输入', error_message: '错误原因', error_type: '错误类型',
  started_at: '开始时间', finished_at: '结束时间', duration_ms: '耗时（毫秒）',
  span_id: '步骤编号', parent_span_id: '上级步骤编号', node: '步骤类型', synthetic: '展示分组',
  query_index: '查询序号', query_strategy: '查询策略', source: '检索来源', filters: '筛选条件',
  top_k: '返回数量上限', candidate_count: '候选数量', result_count: '返回数量',
  route_count: '召回路线数量', route_counts: '各路线返回数量', vector_dimensions: '向量维度',
  evidence: '标准证据', evidence_ref: '证据引用', source_kind: '来源类型', source_ref: '来源引用',
  obtained: '已取得', coverage: '文本覆盖率',
  reasons: '评分依据', correct: '正确', thresholds: '阈值', rounds: '实际召回轮数',
  stop_reason: '结束原因编码',
  query_executions: '路线查询执行记录', cache_hit: '命中缓存',
  source_counts: '各来源返回数量', errors: '错误列表',
  finished_reason: '结束原因',
  round_number: '当前轮数', query_plan: '查询计划', selected_sources: '选择来源',
  similarity: '相似度', top_candidates: '排名候选', reject_reason: '拒绝原因',
  rule: '规则层', embedding: '向量层', jev: 'Jev层', llm: 'DeepSeek层',
  prompt_tokens: '输入Token', completion_tokens: '输出Token', total_tokens: '总Token',
  is_compound: '复合任务', clarification_reason: '追问原因', score: '分数', name: '名称', display_name: '显示名称',
};
export function chineseFields(value: any): any {
  if (Array.isArray(value)) return value.map(chineseFields);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .map(([key, item]) => [FIELD_LABELS[key] ? `${FIELD_LABELS[key]}（${key}）` : key, chineseFields(item)]));
  return value;
}
