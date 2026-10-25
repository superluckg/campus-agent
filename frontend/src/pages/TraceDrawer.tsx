import { Alert, Button, Descriptions, Drawer, Empty, Modal, Space, Spin, Table, Tabs, Tag, Tooltip, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Trace } from '../types';
import { formatBeijingTime, beijingExport } from '../utils/time';
import { chainRows, chineseFields, cumulativeDuration, seconds, spanDuration, spanRows, STATUS, timingSeconds } from './traceDetails';
import type { Span, SpanRow } from './traceDetails';
import { ROUTE_LABELS, STAGE_LABELS, STAGE_STATUS } from '../utils/evaluationLabels';
import './developer-details.css';

interface Detail { trace: Trace; stages: Span[]; events: Array<{ event_type: string; payload: any; sequence_number: number }> }
export function DataBlock({ value, raw = false }: { value: any; raw?: boolean }) {
  return <pre className="developer-json">{JSON.stringify(raw ? value : chineseFields(beijingExport(value)), null, 2)}</pre>;
}
function stateTag(value: string) {
  const mark = ['success', 'completed'].includes(value) ? '✓ ' : ['failed', 'execution_failed'].includes(value) ? '× '
    : ['partial', 'degraded'].includes(value) ? '△ ' : '';
  return <Tag color={['success', 'completed'].includes(value) ? 'green' : value.includes('fail') ? 'red'
    : ['partial', 'degraded'].includes(value) ? 'orange' : undefined}>{mark}{STATUS[value] ?? value}</Tag>;
}
export function TraceDrawer({ traceId, section = 'overview', onClose }:
  { traceId?: string; section?: string; onClose: () => void }) {
  const [detail, setDetail] = useState<Detail>();
  const [error, setError] = useState('');
  const [tab, setTab] = useState(section);
  const [loading, setLoading] = useState(false);
  const [selectedSpan, setSelectedSpan] = useState<SpanRow>();
  async function load(id: string) {
    setLoading(true); setError('');
    try { setDetail(await api<Detail>(`/developer/traces/${id}`)); }
    catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }
  useEffect(() => { setDetail(undefined); setSelectedSpan(undefined); setTab(section); if (traceId) void load(traceId); }, [traceId, section]);
  const trace = detail?.trace;
  const spans = detail?.stages ?? [];
  const event = (name: string) => detail?.events.find(e => e.event_type === name)?.payload;
  const output = (node: string) => spans.find(s => s.node === node)?.output;
  const intent = event('intent.completed') ?? spans.find(s => s.node === 'recognize_intent')?.intent_diagnostics;
  const route = event('route.completed');
  const answer = event('request.output')?.answer ?? event('response.completed')?.content ?? output('compose')?.final_response?.content
    ?? output('direct_response')?.final_response?.content ?? output('clarify')?.final_response?.content;
  const start = Date.parse(trace?.started_at ?? '');
  const timingRows = spanRows(spans);
  const retrieval = spanRows(spans, true);
  const flow = chainRows(spans, trace?.route, trace?.status);
  const missing = <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未记录此信息" />;
  function spanTable(rows: SpanRow[], timing = false) {
    return <Table<SpanRow> size="small" pagination={false} rowKey="span_id" dataSource={rows}
      scroll={{ x: 900 }} expandable={{ defaultExpandAllRows: false, indentSize: 16 }} columns={[
        { title: '步骤', width: 400, render: (_: unknown, s: SpanRow) => <span className="trace-step-label">{s.label}
          {s.query_index != null && !s.synthetic ? ` · 查询 ${s.query_index}` : ''}</span> },
        ...(!timing ? [{ title: 'Agent', dataIndex: 'agent', width: 130 }] : []),
        { title: '状态', dataIndex: 'status', width: 100, render: stateTag },
        { title: '耗时', dataIndex: 'duration_ms', width: 130, align: timing ? 'right' : undefined,
          render: timing ? timingSeconds : spanDuration },
        ...(timing ? [
          { title: <Tooltip title="从请求开始到本步骤结束的时间，包含此前经过的时间。"><span>累计耗时</span></Tooltip>,
            width: 130, align: 'right' as const, render: (_: unknown, s: SpanRow) => cumulativeDuration(s, start) },
        ] : []),
        { title: '详情', width: 70, render: (_: unknown, s: SpanRow) => !s.synthetic
          && <Button type="link" size="small" onClick={() => setSelectedSpan(s)}>查看</Button> },
      ]} />;
  }
  return <Drawer title="Trace 详情" open={!!traceId} onClose={onClose} size={Math.min(1000, window.innerWidth - 24)}>
    {error && <Alert type="error" title={error} />}
    <Spin spinning={loading}>
      {trace && <><Space wrap><Tag>{trace.request_source === 'evaluation' ? '评测请求' : '聊天请求'}</Tag>{stateTag(trace.status)}
        <Typography.Text type="secondary">{formatBeijingTime(trace.started_at, true, '  ')}</Typography.Text>
        <Button size="small" onClick={() => traceId && void load(traceId)}>刷新</Button></Space>
        <Tabs activeKey={tab} onChange={setTab} items={[
          { key: 'overview', label: '输入与回答', children: <Space orientation="vertical" className="developer-full">
            <Typography.Text type="secondary" copyable>{trace.trace_id}</Typography.Text>
            <Typography.Title level={5}>完整输入</Typography.Title><div className="developer-prose">{trace.query ?? '未记录'}</div>
            <Typography.Title level={5}>最终回答</Typography.Title><div className="developer-prose">{answer ?? '未记录'}</div>
            <Descriptions size="small" column={2} items={[
              { key: 'route', label: '路由', children: ROUTE_LABELS[trace.route ?? ''] ?? '未记录' },
              { key: 'duration', label: '请求总耗时', children: seconds(trace.duration_ms) },
              { key: 'intent', label: '意图', children: trace.primary_intent ?? '未记录' },
              { key: 'error', label: '错误码', children: trace.error_code ?? '无' },
              { key: 'request', label: '请求编号', children: trace.request_id },
              { key: 'conversation', label: '会话编号', children: trace.conversation_id },
              { key: 'user', label: '用户编号', children: trace.user_id ?? '未记录' },
              { key: 'finished', label: '结束时间', children: formatBeijingTime(trace.finished_at, true, '  ') },
              { key: 'agents', label: '执行Agent', children: trace.selected_agents?.join('、') || '无需业务Agent／未记录' },
              { key: 'tools', label: '工具调用次数', children: trace.tool_call_count ?? '未记录' },
              { key: 'retrieval', label: '检索轮数', children: trace.retrieval_rounds ?? '未记录' },
              { key: 'tokens', label: '已采集Token用量', children: `输入 ${trace.prompt_tokens ?? '未知'} · 输出 ${trace.completion_tokens ?? '未知'} · 总计 ${trace.total_tokens ?? '未知'}` },
            ]} /></Space> },
          { key: 'intent', label: '意图', children: intent ? <><Descriptions column={2} items={[
            { key: 'primary', label: '主意图', children: intent.primary_intent ?? '未记录' },
            { key: 'confidence', label: '置信度', children: intent.confidence ?? '未记录' },
            { key: 'policy', label: '处理策略', children: intent.policy_decision ?? '未记录' },
            { key: 'stage', label: '返回阶段', children: intent.decision_stage ?? '未记录' },
          ]} /><Table size="small" pagination={false} rowKey="stage" dataSource={
            Object.entries(STAGE_LABELS).map(([stage, label]) => ({ label, ...(intent.stage_trace?.find((s: any) => s.stage === stage)
              ?? { stage, status: 'unrecorded', reason: '未记录', duration_ms: null }) }))} columns={[
              { title: '阶段', dataIndex: 'label' }, { title: '状态', render: (_, r) => STAGE_STATUS[r.status as keyof typeof STAGE_STATUS] ?? r.status },
              { title: '耗时', dataIndex: 'duration_ms', render: seconds }, { title: '原因', dataIndex: 'reason' },
            ]} expandable={{ expandedRowRender: r => <DataBlock value={r} /> }} /><DataBlock value={intent} /></> : missing },
          { key: 'route', label: '路由', children: route ? <><Alert type="info" title="路由由程序根据意图、策略、缺失字段及复合子任务决定。" />
            <Descriptions column={1} items={[{ key: 'route', label: '路由结果', children: ROUTE_LABELS[route.route] },
              { key: 'reason', label: '决策依据', children: route.reason_summary },
              { key: 'parts', label: '识别子任务', children: intent?.sub_intents?.join('、') || '无' },
              { key: 'agents', label: '任务分派', children: route.assignments?.map((a: any) => `${a.agent_name}：${a.focus}`).join('；') || '无需业务Agent' }
            ]} /><DataBlock value={{ intent, route }} /></> : missing },
          { key: 'chain', label: '执行链路', children: <><Table size="small" pagination={false} scroll={{ x: 700 }} rowKey="span_id" dataSource={flow}
            columns={[{ title: '阶段', dataIndex: 'label' }, { title: '状态', dataIndex: 'status', render: stateTag },
              { title: '耗时', dataIndex: 'duration_ms', render: seconds },
              { title: '原因', render: (_, s) => s.error_message ?? s.output?.errors?.join('；') ?? '' }]}
            expandable={{ expandedRowRender: s => <DataBlock value={s.output ?? s} /> }} />
            <Typography.Title level={5}>Agent 与工具执行</Typography.Title>
            <Table size="small" pagination={false} scroll={{ x: 680 }} rowKey="span_id" dataSource={spans.filter(s => ['agent', 'tool'].includes(s.node))}
              columns={[{ title: '步骤', dataIndex: 'label' }, { title: 'Agent', dataIndex: 'agent' },
                { title: '状态', dataIndex: 'status', render: stateTag }, { title: '原因', dataIndex: 'error_message' }]}
              expandable={{ expandedRowRender: s => <DataBlock value={s} /> }} /></> },
          { key: 'context', label: '上下文与记忆', children: <><Typography.Title level={5}>加载的上下文</Typography.Title>
            {output('load_context') ? <DataBlock value={output('load_context')} /> : missing}
            <Typography.Title level={5}>实际注入分块快照</Typography.Title>
            {detail?.events.filter(e => e.event_type === 'context.injected').map(e => <DataBlock key={e.sequence_number} value={e.payload} />)}
            {!detail?.events.some(e => e.event_type === 'context.injected') && missing}
            <Typography.Title level={5}>长期记忆注入结果</Typography.Title>{output('recall_memory') ? <DataBlock value={output('recall_memory')} /> : missing}</> },
          { key: 'skills', label: 'Skills', children: output('select_skills') ? <DataBlock value={output('select_skills')} /> : missing },
          { key: 'tools', label: '检索与工具', children: <>
            <Typography.Paragraph type="secondary">记录每次实际检索批次、查询计划及缓存。展开可查看 BM25、向量化、数据库查询与候选融合。业务模型调用的耗时和输出动作在耗时页展开查看；父步骤耗时包含子步骤。</Typography.Paragraph>
            {spanTable(retrieval)}</> },
          { key: 'timing', label: '耗时', children: <><Typography.Title level={5}>总耗时：{timingSeconds(trace.duration_ms)}</Typography.Title>
            {spanTable(timingRows, true)}</> },
          { key: 'json', label: '原始 JSON', children: <DataBlock raw value={detail} /> },
        ]} /></>}
    </Spin>
    <Modal title={selectedSpan?.label} open={!!selectedSpan} footer={null} width={800}
      onCancel={() => setSelectedSpan(undefined)}>
      {selectedSpan && <DataBlock value={Object.fromEntries(Object.entries(selectedSpan).filter(([key]) => key !== 'children'))} />}
    </Modal>
  </Drawer>;
}
