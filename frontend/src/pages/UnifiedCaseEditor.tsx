import { Alert, Button, Card, Descriptions, Input, Modal, Radio, Select, Space, Spin, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { AGENT_FOR_INTENT, BEHAVIORS, EVAL_AGENT_LABELS, EVAL_POLICY_LABELS, EVAL_ROUTE_LABELS, blankEvidence, changeGold, decisionReference } from './unifiedEvaluation';
import type { CaseEvidence, CasePayload } from './unifiedEvaluation';

interface Source { id: string; label: string; filename?: string; source_key?: string }
interface Sources { local: Source[]; web: Source[] }
interface Section { section_id: string; page_number: number | null; section_path: string; source_url: string; content: string }
interface Preview { filename: string; source_key: string; sections: Section[] }
type Choice = { value: string; label: string };

function EvidenceCard({ item, index, sources, parts, compound, update, remove }: {
  item: CaseEvidence; index: number; sources: Sources; parts: Choice[]; compound: boolean;
  update: (item: CaseEvidence) => void; remove: () => void;
}) {
  const choices = item.source_kind === 'knowledge_file' ? sources.local : sources.web;
  const identifier = choices.find(s => item.source_kind === 'knowledge_file' ? s.filename === item.filename : s.source_key === item.source_key)?.id;
  const [preview, setPreview] = useState<Preview>();
  const [sectionId, setSectionId] = useState('');
  const [selectedText, setSelectedText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const section = preview?.sections.find(s => s.section_id === sectionId);
  useEffect(() => {
    setPreview(undefined); setSelectedText(''); setError('');
    if (!identifier) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    const path = item.source_kind === 'knowledge_file' ? 'local' : 'web';
    void api<Preview>(`/developer/evaluation/sources/${path}/${encodeURIComponent(identifier)}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) { setPreview(value); setSectionId(value.sections[0]?.section_id ?? ''); } })
      .catch(e => { if (!controller.signal.aborted) setError((e as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [identifier, item.source_kind]);
  function selection(target: HTMLTextAreaElement) { setSelectedText(target.value.slice(target.selectionStart, target.selectionEnd)); }
  const invalid = item.related_intents.some(intent => !parts.some(p => p.value === intent));
  return <Card size="small" className="case-evidence" title={`标准证据 ${index + 1}`} extra={<Button type="text" danger onClick={remove}>移除</Button>}>
    <Space orientation="vertical" className="developer-full" size="middle">
      <Radio.Group aria-label={`第${index + 1}条证据资料类型`} value={item.source_kind} onChange={e => update({ ...item, source_kind: e.target.value,
        filename: '', source_key: '', source_url: '', page_number: null, section_path: '' })} options={[{ value: 'knowledge_file', label: '知识库文档' }, { value: 'official_web', label: '官网缓存' }]} />
      <label className="case-inline"><span>选择资料</span><Select aria-label={`第${index + 1}条证据资料`} className="case-source-select" showSearch optionFilterProp="label" value={identifier}
        placeholder={choices.length ? '选择资料以预览原文' : item.source_kind === 'knowledge_file' ? '暂无已入库文档' : '暂无成功的官网缓存'}
        options={choices.map(s => ({ value: s.id, label: s.label }))} onChange={id => {
          const source = choices.find(s => s.id === id)!;
          update({ ...item, filename: source.filename ?? '', source_key: source.source_key ?? '', source_url: '', page_number: null, section_path: '' });
        }} /></label>
      {!identifier && (item.filename || item.source_key) && <Alert type="warning" title="原来源当前不可预览；已填写原文仍保留，运行前会重新校验。" />}
      {error && <Alert type="error" title={error} />}
      {loading && <Spin tip="正在读取原文"><div style={{ minHeight: 60 }} /></Spin>}
      {preview && <div className="case-preview">
        <label className="case-inline"><span>预览位置</span><Select aria-label={`第${index + 1}条证据预览位置`} value={sectionId} className="case-source-select" onChange={id => { setSectionId(id); setSelectedText(''); }}
          options={preview.sections.map((s, i) => ({ value: s.section_id, label: [s.page_number ? `第 ${s.page_number} 页` : `片段 ${i + 1}`, s.section_path].filter(Boolean).join(' · ') }))} /></label>
        <Input.TextArea aria-label={`第${index + 1}条证据原文预览`} readOnly value={section?.content ?? ''} rows={7}
          onMouseUp={e => selection(e.currentTarget)} onKeyUp={e => selection(e.currentTarget)} />
        <Space wrap><Button disabled={!selectedText.trim()} onClick={() => {
          if (section) update({ ...item, filename: preview.filename, source_key: preview.source_key, source_url: section.source_url,
            page_number: section.page_number, section_path: section.section_path, content: selectedText });
        }}>使用选中原文</Button><Typography.Text type="secondary">在预览中选中文字，自动填入原文和来源位置。</Typography.Text></Space>
      </div>}
      <label className="case-field"><span>标准原文</span><Input.TextArea aria-label={`第${index + 1}条标准原文`} placeholder="摘取能够回答问题的资料原文，也可以编辑粘贴内容。" rows={3} value={item.content} onChange={e => update({ ...item, content: e.target.value })} /></label>
      {(item.filename || item.source_key) && <Typography.Text type="secondary">来源：{item.filename || choices.find(s => s.source_key === item.source_key)?.label || '官网缓存'}
        {item.page_number ? ` · 第 ${item.page_number} 页` : ''}{item.section_path ? ` · ${item.section_path}` : ''}{item.source_url ? ` · ${item.source_url}` : ''}</Typography.Text>}
      {compound && <label className="case-inline"><span>用于哪个子任务</span><Select aria-label={`第${index + 1}条证据关联子任务`} mode="multiple" className="case-source-select"
        placeholder="选择一个或两个子任务" status={invalid || !item.related_intents.length ? 'warning' : undefined}
        value={item.related_intents} options={parts} onChange={values => update({ ...item, related_intents: values })}
        tagRender={props => <span className="case-task-tag">{parts.find(p => p.value === props.value)?.label ?? '原子任务已变更'}<Button size="small" type="text" onClick={props.onClose}>×</Button></span>} /></label>}
      {compound && invalid && <Alert type="warning" title="子意图已改变，请重新选择证据关联。原文内容已保留。" />}
    </Space>
  </Card>;
}

export function UnifiedCaseEditor({ editor, catalog, busy, errors, onChange, onCancel, onSave }: {
  editor: { id?: string; value: CasePayload }; catalog: Choice[]; busy: boolean; errors: string[];
  onChange: (value: CasePayload) => void; onCancel: () => void; onSave: () => void;
}) {
  const value = editor.value, gold = decisionReference(value), compound = gold.expected_intent === 'compound';
  const [sources, setSources] = useState<Sources>({ local: [], web: [] });
  const [sourceError, setSourceError] = useState('');
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (errors.length) errorRef.current?.scrollIntoView({ block: 'nearest' }); }, [errors]);
  useEffect(() => {
    const controller = new AbortController();
    void api<Sources>('/developer/evaluation/sources', { signal: controller.signal }).then(setSources)
      .catch(e => { if (!controller.signal.aborted) setSourceError((e as Error).message); });
    return () => controller.abort();
  }, []);
  const parts = (compound ? gold.expected_sub_intents : [gold.expected_intent]).map(intent => ({ value: intent, label: catalog.find(c => c.value === intent)?.label ?? '请选择子意图' }));
  function updateEvidence(index: number, item: CaseEvidence) { const next = structuredClone(value); next.evidence[index] = item; onChange(next); }
  return <Modal title={editor.id ? '编辑用例' : '创建用例（保存后自动编号）'} open width={1000} className="unified-case-editor"
    styles={{ body: { maxHeight: '70vh', overflowY: 'auto', paddingRight: 12 } }} onCancel={onCancel} onOk={onSave} confirmLoading={busy} okText="保存" cancelText="取消">
    <Space orientation="vertical" size="large" className="developer-full">
      {errors.length > 0 && <div ref={errorRef}><Alert type="error" title="请完善以下内容" description={<ul>{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>} /></div>}
      <label className="case-field"><span>输入问题</span><Input.TextArea aria-label="输入问题" value={value.query} autoSize={{ minRows: 2, maxRows: 6 }} onChange={e => onChange({ ...value, query: e.target.value })} /></label>
      <section className="case-section"><Typography.Title level={5}>意图标准</Typography.Title>
        <div className="case-intent-row"><label className="case-inline"><span>主意图</span><Select aria-label="主意图" value={gold.expected_intent} options={catalog} style={{ width: 210 }} onChange={v => onChange(changeGold(value, 'expected_intent', v))} /></label></div>
        {compound && <label className="case-inline case-compound"><span>复合子意图（两个领域）</span><Select aria-label="复合子意图" mode="multiple" maxCount={2} className="case-source-select" value={gold.expected_sub_intents}
          options={catalog.filter(c => AGENT_FOR_INTENT[c.value])} onChange={v => onChange(changeGold(value, 'expected_sub_intents', v))} /></label>}
        <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
          { key: 'policy', label: '处理策略', children: EVAL_POLICY_LABELS[gold.expected_policy] },
          { key: 'route', label: '预期路由', children: EVAL_ROUTE_LABELS[gold.expected_route] },
          { key: 'agents', label: '预期助手', children: gold.expected_agents.map(a => EVAL_AGENT_LABELS[a]).join('、') || '无需助手' },
        ]} />
      </section>
      <section className="case-section"><Typography.Title level={5}>回答标准</Typography.Title>
        <label className="case-inline"><span>预期回答行为</span><Select aria-label="预期回答行为" style={{ width: 220 }} popupMatchSelectWidth={260} value={value.expected_behavior}
          options={BEHAVIORS} onChange={v => onChange({ ...value, expected_behavior: v })} /></label>
        <label className="case-field"><span>参考回答</span><Input.TextArea aria-label="参考回答" value={value.reference_answer} rows={4} placeholder="填写该问题的规范回答，保留条件、例外和资料适用范围。"
          onChange={e => onChange({ ...value, reference_answer: e.target.value })} /></label>
      </section>
      <section className="case-section"><Typography.Title level={5}>标准原文证据（可为空）</Typography.Title>
        <Typography.Paragraph type="secondary">从已入库文档或已有官网缓存中摘取原文。没有标准原文时，检索指标不评分；标准原文只用于评分，不注入被测助手。</Typography.Paragraph>
        {sourceError && <Alert type="error" title={sourceError} />}
        <Space orientation="vertical" className="developer-full" size="middle">
          {value.evidence.map((item, index) => <EvidenceCard key={index} item={item} index={index} sources={sources} parts={parts} compound={compound}
            update={item => updateEvidence(index, item)} remove={() => onChange({ ...value, evidence: value.evidence.filter((_, i) => i !== index) })} />)}
          <Button disabled={value.evidence.length >= 50} onClick={() => onChange({ ...value, evidence: [...value.evidence, { ...blankEvidence(), related_intents: [] }] })}>添加标准证据</Button>
        </Space>
      </section>
      <section className="case-section"><Typography.Title level={5}>固定历史消息（可为空，最多八条）</Typography.Title>
        <Typography.Paragraph type="secondary">按对话发生顺序填写。全部历史消息直接进入上下文，不使用真实用户的历史、摘要或长期记忆。</Typography.Paragraph>
        <Space orientation="vertical" className="developer-full">
          {value.conversation_context.map((item, index) => <div className="case-history-row" key={index}>
            <Select aria-label={`第${index + 1}条历史消息角色`} value={item.role} style={{ width: 110 }} options={[{ value: 'user', label: '用户' }, { value: 'assistant', label: '助手' }]}
              onChange={role => { const next = structuredClone(value); next.conversation_context[index].role = role; onChange(next); }} />
            <Input.TextArea aria-label={`第${index + 1}条历史消息`} value={item.content} autoSize={{ minRows: 1, maxRows: 5 }} placeholder={`第 ${index + 1} 条历史消息`} onChange={e => {
              const next = structuredClone(value); next.conversation_context[index].content = e.target.value; onChange(next);
            }} /><Button danger type="text" onClick={() => onChange({ ...value, conversation_context: value.conversation_context.filter((_, i) => i !== index) })}>移除</Button>
          </div>)}
          <Button disabled={value.conversation_context.length >= 8} onClick={() => onChange({ ...value, conversation_context: [...value.conversation_context, { role: 'user', content: '' }] })}>添加历史消息</Button>
        </Space>
      </section>
    </Space>
  </Modal>;
}
