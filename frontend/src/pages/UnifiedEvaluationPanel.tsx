import { Alert, Button, Card, Descriptions, Drawer, Empty, Input, InputNumber, Modal, Space, Table, Tabs, Tag, Typography, message } from 'antd';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { formatBeijingTime } from '../utils/time';
import { TraceDrawer, DataBlock } from './TraceDrawer';
import { UnifiedCaseEditor } from './UnifiedCaseEditor';
import { METRIC_GROUPS, ROUTE_LABELS, STAGE_LABELS } from '../utils/evaluationLabels';
import { seconds, STATUS } from './traceDetails';
import { EVAL_MATCH_FIELDS as MATCH_FIELDS, EVAL_POLICY_LABELS, EVAL_ROUTE_LABELS, EVAL_AGENT_LABELS, average, blankCase, editableCase, decisionReference, validateCase, groupResults, percent, score, RETRIEVAL_METRICS, GENERATION_METRICS } from './unifiedEvaluation';
import type { CasePayload, CaseView, CurrentRun, UnifiedResult } from './unifiedEvaluation';

const ROOT = '/developer/evaluation';
function MetricGrid({ entries }: { entries: Array<[string, string]> }) {
  return <div className="eval-metrics">{entries.map(([title, value]) => <div className="eval-metric" key={title}>
    <Typography.Text type="secondary">{title}</Typography.Text><strong>{value}</strong></div>)}</div>;
}

export function UnifiedEvaluationPanel() {
  const [cases, setCases] = useState<CaseView[]>([]);
  const [run, setRun] = useState<CurrentRun | null>(null);
  const [results, setResults] = useState<UnifiedResult[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [repeats, setRepeats] = useState(1);
  const [area, setArea] = useState('cases');
  const [catalog, setCatalog] = useState<Array<{ value: string; label: string }>>([]);
  const [editor, setEditor] = useState<{ id?: string; value: CasePayload }>();
  const [editorErrors, setEditorErrors] = useState<string[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [importErrors, setImportErrors] = useState<Array<{ line: number; message: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<UnifiedResult>();
  const [traceId, setTraceId] = useState<string>();
  const [messageApi, context] = message.useMessage();
  const active = !!run && ['queued', 'running'].includes(run.status);
  async function refreshCases() {
    const values = await api<CaseView[]>(`${ROOT}/cases`); setCases(values);
    setSelected(ids => ids.filter(id => values.some(c => c.case_id === id)));
  }
  async function refreshRun(includeResults = false) {
    const value = await api<{ run: CurrentRun | null; results: UnifiedResult[] }>(`${ROOT}/current?include_results=${includeResults}`);
    if (includeResults) setResults(value.results);
    else if (value.run?.run_id !== run?.run_id) setResults([]);
    setRun(value.run);
  }
  function fail(e: unknown) { messageApi.error((e as Error).message); }
  useEffect(() => {
    void Promise.all([refreshCases(), refreshRun(), api<any>(`${ROOT}/catalog`).then(v => setCatalog(v.intents))]).catch(fail);
  }, []);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void refreshRun(area === 'results').catch(fail), 1800);
    return () => window.clearInterval(timer);
  }, [active, area]);
  useEffect(() => { if (area === 'results') void refreshRun(true).catch(fail); }, [area]);
  function openEditor(item?: CaseView) {
    const value = item ? editableCase(item.payload) : blankCase();
    setEditor({ id: item?.case_id, value });
    setEditorErrors([]);
  }
  async function saveCase() {
    if (!editor) return;
    setBusy(true);
    try {
      const value = structuredClone(editor.value);
      const errors = validateCase(value);
      if (errors.length) { setEditorErrors(errors); return; }
      await api(`${ROOT}/cases${editor.id ? `/${editor.id}` : ''}`,
        { method: editor.id ? 'PUT' : 'POST', body: JSON.stringify(value) });
      setEditor(undefined); await refreshCases(); messageApi.success('用例已保存，编号自动生成。');
    } catch (e) { setEditorErrors([(e as Error).message]); } finally { setBusy(false); }
  }
  async function importCases() {
    setBusy(true); setImportErrors([]);
    try {
      const preview = await api<{ valid_count: number; errors: Array<{ line: number; message: string }> }>(`${ROOT}/import/preview`,
        { method: 'POST', body: JSON.stringify({ text: importText }) });
      if (preview.errors.length) { setImportErrors(preview.errors); return; }
      const saved = await api<{ saved_count: number }>(`${ROOT}/import`, { method: 'POST', body: JSON.stringify({ text: importText }) });
      await refreshCases(); setImportOpen(false); setImportText(''); messageApi.success(`已追加 ${saved.saved_count} 条用例。`);
    } catch (e) { fail(e); } finally { setBusy(false); }
  }
  async function start() {
    setBusy(true);
    try {
      await api(`${ROOT}/runs`, { method: 'POST', body: JSON.stringify({ case_ids: selected, repeats }) });
      await refreshRun(); messageApi.success('预检完成，评测已启动。');
    } catch (e) { fail(e); } finally { setBusy(false); }
  }
  const summary = run?.summary_metrics ?? {};
  const label = (value: string) => catalog.find(i => i.value === value)?.label ?? value;
  function decisionLabel(field: string, value: unknown): string {
    if (value == null) return '未记录';
    if (field === 'expected_policy') return value === 'allow' ? EVAL_POLICY_LABELS.allow : ['refuse', 'refuse_sensitive', 'refuse_unauthorized'].includes(String(value)) ? EVAL_POLICY_LABELS.refuse : '未记录';
    if (field === 'expected_route') return EVAL_ROUTE_LABELS[String(value)] ?? String(value);
    if (Array.isArray(value)) return value.map(v => field === 'expected_agents' ? EVAL_AGENT_LABELS[v] ?? v : label(v)).join('、') || '无';
    return label(String(value));
  }
  const grouped = groupResults(results);
  return <>{context}<Tabs activeKey={area} onChange={setArea} items={[
    { key: 'cases', label: '评测集管理', children: <>
      <div className="eval-toolbar"><Button type="primary" onClick={() => openEditor()}>创建用例</Button>
        <Button onClick={() => { setImportErrors([]); setImportOpen(true); }}>批量导入</Button>
        <Button onClick={() => void refreshCases().catch(fail)}>刷新</Button>
</div>
      <Table<CaseView> rowKey="case_id" dataSource={cases} scroll={{ x: 900 }} pagination={{ pageSize: 10 }} columns={[
        { title: '编号', dataIndex: 'case_label', width: 120 },
        { title: '输入', render: (_, c) => <Typography.Paragraph className="eval-query" style={{ margin: 0 }}>{c.payload.query}</Typography.Paragraph> },
        { title: '标准意图', width: 160, render: (_, c) => label(c.payload.expected_intent) },
        { title: '评测范围', width: 190, render: (_, c) => `意图、生成${c.payload.evidence.length > 0 ? '、检索' : ''}` },
        { title: '操作', width: 150, render: (_, c) => <Space><Button type="link" onClick={() => openEditor(c)}>编辑</Button>
          <Button type="link" danger onClick={() => Modal.confirm({ title: `永久移除 ${c.case_label}？`,
            content: '当前用例记录不可恢复，编号不复用。已有评测结果的独立快照保留。', okText: '永久移除', okButtonProps: { danger: true }, cancelText: '取消',
            onOk: async () => { try { await api(`${ROOT}/cases/${c.case_id}`, { method: 'DELETE' }); await refreshCases(); }
              catch (e) { fail(e); throw e; } } })}>移除</Button></Space> }
      ]} /></> },
    { key: 'run', label: '评测运行', children: <>
      <div className="eval-toolbar"><Typography.Text>重复次数</Typography.Text><InputNumber min={1} max={5} precision={0} value={repeats} onChange={v => setRepeats(v ?? 1)} disabled={active} />
        <Button type="primary" disabled={active || !selected.length} loading={busy} onClick={() => void start()}>运行所选</Button>
        <Typography.Text>{run ? `${run.progress.completed_attempts} / ${run.progress.total_attempts} 次已完成` : '尚未运行'}</Typography.Text>
        {active && <Button danger disabled={run?.stop_requested} onClick={async () => {
          try { await api(`${ROOT}/runs/${run!.run_id}/stop`, { method: 'POST' }); await refreshRun(); }
          catch (e) { fail(e); }
        }}>{run?.stop_requested ? '正在停止' : '停止'}</Button>}
        <Button onClick={() => void refreshRun().catch(fail)}>刷新进度</Button></div>
      {run && <Alert type={run.status === 'failed' ? 'error' : active ? 'info' : 'success'} style={{ marginBottom: 16 }}
        title={`${STATUS[run.status] ?? run.status} · ${run.progress.current_case ?? ''}${run.progress.trial_index ? ` 第${run.progress.trial_index}次` : ''}`}
        description={<Space wrap><span>{run.progress.stage}</span>{!active && <Button size="small" onClick={() => setArea('results')}>查看评测结果</Button>}</Space>} />}
      <Space style={{ marginBottom: 12 }}><Button disabled={active || !cases.length} onClick={() => setSelected(cases.map(c => c.case_id))}>全选全部 {cases.length} 个 Case</Button>
        <Button disabled={active} onClick={() => setSelected([])}>清空选择</Button><span>已选择 {selected.length} 个</span></Space>
      <Table<CaseView> rowKey="case_id" dataSource={cases} pagination={{ pageSize: 10 }} rowSelection={{ selectedRowKeys: selected,
        preserveSelectedRowKeys: true, onChange: keys => setSelected(keys.map(String)), getCheckboxProps: () => ({ disabled: active }) }}
        columns={[{ title: '编号', dataIndex: 'case_label', width: 120 }, { title: '问题', render: (_, c) => c.payload.query }]} />
    </> },
    { key: 'results', label: '评测结果', children: !run ? <Empty description="尚未运行评测" /> : <>
      <div className="eval-toolbar"><Tag>{STATUS[run.status] ?? run.status}</Tag><span>{formatBeijingTime(run.started_at)}</span>
        <span>{summary.case_count ?? 0} 个 Case · {summary.completed_attempts ?? 0} 次完成</span>
        <Button onClick={() => void refreshRun(true).catch(fail)}>刷新结果</Button>
        <span>执行异常 {summary.execution_failure_count ?? 0} · 评分异常 {summary.judge_failure_count ?? 0}</span></div>
      <Typography.Paragraph type="secondary">只展示最新运行。先汇总同一 Case 的重复执行和检索调用，再按 Case 等权统计；不适用或未评分项不填满分。</Typography.Paragraph>
      <div className="eval-result-groups"><Card title="意图识别">
        <MetricGrid entries={METRIC_GROUPS.flatMap(g => g.metrics).map(m => [m.title,
          m.kind === 'duration' ? seconds(summary.intent?.[m.key]) : m.key === 'macro_f1' ? (summary.intent?.[m.key]?.toFixed(3) ?? '未评分') : percent(summary.intent?.[m.key])])} />
        <MetricGrid entries={[["降级率", percent(summary.intent?.degraded_rate)],
          ["重复意图一致率", percent(summary.intent?.agreement_rate)], ["重复决策一致率", percent(summary.intent?.decision_agreement_rate)]]} />
        <Typography.Title level={5}>各意图表现</Typography.Title>
        <Table size="small" pagination={false} rowKey="intent" dataSource={Object.entries(summary.intent?.per_intent ?? {}).filter(([, v]) => (v as any).support > 0).map(([intent, v]) => ({ intent, ...(v as any) }))}
          columns={[{ title: '意图', dataIndex: 'intent', render: label }, { title: '精确率', dataIndex: 'precision', render: percent },
            { title: '召回率', dataIndex: 'recall', render: percent }, { title: 'F1', dataIndex: 'f1-score', render: v => v?.toFixed(3) },
            { title: 'Case权重', dataIndex: 'support', render: v => v?.toFixed(1) }]} />
        <Typography.Title level={5}>意图漏斗</Typography.Title>
        <Table size="small" pagination={false} rowKey="stage" dataSource={summary.intent?.funnel_summary ?? []} columns={[
          { title: '阶段', dataIndex: 'stage', render: v => STAGE_LABELS[v as keyof typeof STAGE_LABELS] ?? v },
          { title: '执行次数', dataIndex: 'executed_count' }, { title: '返回次数', dataIndex: 'returned_count' },
          { title: '返回占比', dataIndex: 'exit_share', render: percent }, { title: '平均耗时', dataIndex: 'mean_duration_ms', render: seconds }
        ]} />
        <Typography.Paragraph type="secondary">已采集模型用量：{JSON.stringify(summary.intent?.model_usage ?? {})}</Typography.Paragraph>
      </Card><Card title="检索">
        <MetricGrid entries={RETRIEVAL_METRICS.map(([key, title]) => [title, percent(summary.retrieval?.[key])])} />
        <Typography.Text type="secondary">仅对实际检索调用且具有标准相关集的记录评分。</Typography.Text>
      </Card><Card title="生成"><MetricGrid entries={GENERATION_METRICS.map(([key, title]) => [title, score(summary.generation?.[key])])} /></Card></div>
      <Typography.Title level={4}>逐条结果</Typography.Title>
      <Table className="eval-results-table" rowKey={g => g.case.case_id} dataSource={grouped} scroll={{ x: 1500 }} pagination={{ pageSize: 10 }} columns={[
        { title: '编号', render: (_, g) => g.case.case_label, width: 100 },
        { title: '输入', render: (_, g) => <Typography.Paragraph className="eval-query">{g.case.payload.query}</Typography.Paragraph>, width: 280 },
        { title: '标准意图', render: (_, g) => label(g.case.payload.expected_intent), width: 140 },
        { title: '实际意图', render: (_, g) => [...new Set(g.rows.map(r => label(r.prediction.primary_intent ?? '未记录')))].join('、'), width: 140 },
        { title: '意图匹配', className: 'eval-single-line', width: 115, render: (_, g) => percent(average(g.rows, r => r.intent_metrics.intent_accuracy)) },
        { title: '检索命中', className: 'eval-single-line', width: 115, render: (_, g) => percent(average(g.rows, r => r.retrieval.metrics.hit)) },
        { title: '正确性', className: 'eval-single-line', width: 120, render: (_, g) => score(average(g.rows, r => r.generation.metrics.correctness)) },
        { title: '忠实度', className: 'eval-single-line', width: 120, render: (_, g) => score(average(g.rows, r => r.generation.metrics.faithfulness)) },
        { title: `执行/${run.repeats}次`, width: 115, render: (_, g) => <div className="eval-execution">
          <span>{g.rows.filter(r => r.status === 'cancelled').length} 中止</span>
          <span>{g.rows.filter(r => r.error_code && r.status !== 'cancelled').length} 异常</span>
        </div> },
        { title: '详情', width: 75, render: (_, g) => <Button type="link" onClick={() => setDetail(g.rows[0])}>详情</Button> },
      ]} expandable={{ expandedRowRender: g => <Table<UnifiedResult> size="small" pagination={false} rowKey="result_id" dataSource={g.rows} scroll={{ x: 1350 }}
        columns={[{ title: '重复', dataIndex: 'trial_index', width: 70 }, { title: '路由', render: (_, r) => ROUTE_LABELS[r.prediction.route] ?? '未记录' },
          ...MATCH_FIELDS.map(f => ({ title: f.title, width: 100, render: (_: unknown, r: UnifiedResult) => percent(r.intent_metrics[f.key]) })),
          { title: '检索', render: (_, r) => STATUS[r.retrieval.status] ?? r.retrieval.status },
          { title: '生成', render: (_, r) => STATUS[r.generation.status] ?? r.generation.status },
          { title: '耗时', dataIndex: 'duration_ms', render: seconds },
          { title: '详情', render: (_, r) => <Button type="link" onClick={() => setDetail(r)}>详情</Button> }]} /> }} />
    </> },
  ]} />
  {editor && <UnifiedCaseEditor editor={editor} catalog={catalog} busy={busy} errors={editorErrors}
    onChange={value => { setEditor({ ...editor, value }); setEditorErrors([]); }} onCancel={() => setEditor(undefined)} onSave={() => void saveCase()} />}
  <Modal title="批量追加 JSONL" open={importOpen} width={900} onCancel={() => setImportOpen(false)} onOk={() => void importCases()} confirmLoading={busy} okText="校验并导入" cancelText="取消">
    <Typography.Paragraph>每行一个完整 Case，不填编号。任何一行错误时整批不写入；只追加，不覆盖现有用例。</Typography.Paragraph>
    <input type="file" accept=".jsonl,.txt" onChange={async e => { const file = e.target.files?.[0]; if (file) { setImportText(await file.text()); setImportErrors([]); } }} />
    <Input.TextArea value={importText} onChange={e => setImportText(e.target.value)} rows={14} style={{ marginTop: 12, fontFamily: 'monospace' }} />
    {importErrors.length > 0 && <Alert type="error" title="整批未写入" description={<ul>{importErrors.map((e, i) => <li key={i}>第 {e.line} 行：{e.message}</li>)}</ul>} />}
  </Modal>
  <Drawer title={detail ? `${detail.case.case_label} · 第${detail.trial_index}次` : '结果详情'} open={!!detail} onClose={() => setDetail(undefined)} size={Math.min(1000, window.innerWidth - 24)}>
    {detail && <><Space wrap><Button onClick={() => setTraceId(detail.trace_id)}>打开对应评测 Trace</Button>
      {grouped.find(g => g.case.case_id === detail.case.case_id)?.rows.map(r => <Button key={r.result_id} type={r.result_id === detail.result_id ? 'primary' : 'default'} onClick={() => setDetail(r)}>第{r.trial_index}次</Button>)}</Space>
      {detail.error_message && <Alert type="error" title={detail.error_message} />}
      <Tabs items={[
        { key: 'intent', label: '意图与路由', children: <><Typography.Title level={5}>{detail.case.payload.query}</Typography.Title>
          <Table size="small" rowKey="key" pagination={false} dataSource={[...MATCH_FIELDS]} columns={[
            { title: '字段', dataIndex: 'title' }, { title: '人工标准', render: (_, f) => decisionLabel(f.reference, decisionReference(detail.case.payload)[f.reference]) },
            { title: '实际结果', render: (_, f) => decisionLabel(f.reference, detail.prediction[f.actual]) },
            { title: '匹配', render: (_, f) => percent(detail.intent_metrics[f.key]) }]} />
          <Typography.Paragraph type="secondary">处理策略统一比较允许／拒绝；原始拒绝原因保留在下方实际预测中。</Typography.Paragraph><DataBlock value={detail.prediction} /></> },
        { key: 'retrieval', label: '检索与证据', children: <><Tag>{STATUS[detail.retrieval.status] ?? detail.retrieval.status}</Tag>
          {detail.retrieval.reason && <Typography.Paragraph>{detail.retrieval.reason}</Typography.Paragraph>}
          {(detail.retrieval.calls ?? []).map((call: any) => <Card key={call.call_id} size="small" title={`${call.agent} · ${call.query ?? ''} · ${seconds(call.duration_ms)}`}>
            <MetricGrid entries={RETRIEVAL_METRICS.map(([key, title]) => [title, percent(call.metrics[key])])} />{call.reason && <Typography.Paragraph>{call.reason}</Typography.Paragraph>}
            <Table<any> size="small" rowKey="key" pagination={false} dataSource={call.documents.map((d: any, i: number) => ({ ...d, rank: i + 1 }))}
              columns={[{ title: '排名', dataIndex: 'rank', width: 65 }, { title: '来源', render: (_, d) => d.metadata.filename || d.metadata.source_url || d.metadata.title },
                { title: '原文', dataIndex: 'content', render: text => <Typography.Paragraph ellipsis={{ rows: 3, expandable: true }}>{text}</Typography.Paragraph> }]}
              expandable={{ expandedRowRender: d => <DataBlock value={d.metadata} /> }} />
          </Card>)}
          <Typography.Title level={5}>标准证据与实际取得情况</Typography.Title><DataBlock value={{ evidence: detail.case.payload.evidence,
            obtained: detail.retrieval.evidence_details }} /></> },
        { key: 'generation', label: '回答与评分', children: <><Tag>{STATUS[detail.generation.status] ?? detail.generation.status}</Tag>
          <Typography.Paragraph type="secondary">业务链路耗时：{seconds(detail.duration_ms)}；评分耗时：{seconds(detail.generation.judge_duration_ms)}</Typography.Paragraph>
          <Typography.Title level={5}>实际回答</Typography.Title><div className="developer-prose">{detail.generation.answer ?? '无有效回答'}</div>
          <Typography.Title level={5}>参考回答</Typography.Title><div className="developer-prose">{detail.case.payload.reference_answer}</div>
          <MetricGrid entries={GENERATION_METRICS.map(([key, title]) => [title, score(detail.generation.metrics[key])])} />
          <DataBlock value={{ reasons: detail.generation.reasons, error: detail.generation.error_message, citations: detail.generation.citations }} /></> },
        { key: 'context', label: '实际可见上下文', children: <DataBlock value={detail.visible_context} /> },
        { key: 'case', label: 'Case 快照', children: <DataBlock raw value={detail.case.payload} /> },
      ]} /></>}
  </Drawer>
  <TraceDrawer traceId={traceId} onClose={() => setTraceId(undefined)} />
  </>;
}
