import { DatabaseOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Drawer, Empty, Input, Select, Space, Spin, Table, Tabs, Tag, Tree, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { beijingExport, formatBeijingTime } from "../utils/time";

interface Reference { table: string; column: string }
interface ColumnInfo {
  name: string;
  type: string;
  kind: "scalar" | "text" | "json" | "vector";
  nullable: boolean;
  primary_key: boolean;
  foreign_keys: string[];
  sensitive: boolean;
  sortable: boolean;
  filterable: boolean;
  reference: Reference | null;
}
interface TableInfo {
  schema: string;
  name: string;
  key: string;
  description: string;
  primary_key: string;
  row_count: number;
  columns: ColumnInfo[];
  default_columns: string[];
  relations: Array<Reference & { source_column: string; label: string }>;
}
interface Catalog { database: string; schemas: string[]; tables: TableInfo[] }
interface DatabaseRow { key: string; values: Record<string, unknown>; preview_fields?: string[] }
interface RowPage { rows: DatabaseRow[]; total: number; page: number; page_size: number }

const LABELS: Record<string, string> = {
  case_number: "Case 编号", progress: "运行进度", snapshot: "固定运行快照", stop_requested: "已请求停止",
  description: "说明", case_snapshot: "本次评测的输入与标准答案",
  intent_case_id: "意图用例编号", external_id: "用例标识", inputs: "输入条件", reference_outputs: "标准答案",
  intent_run_id: "意图评测运行编号", intent_result_id: "意图评测结果编号", repeats: "重复次数",
  selection: "用例选择范围", source_hashes: "代码校验值", trial_index: "第几次运行",
  intent_duration_ms: "意图识别耗时（毫秒）", route_duration_ms: "路由耗时（毫秒）",
  user_id: "用户编号", username: "用户名", role: "角色", is_active: "启用", password_hash: "密码哈希",
  conversation_id: "对话编号", title: "标题", status: "状态", created_at: "创建时间", updated_at: "更新时间",
  message_id: "消息编号", request_id: "请求编号", trace_id: "Trace 编号", content: "正文", agent_names: "Agents",
  display_metadata: "展示信息", citation_id: "引用编号", citation_index: "引用序号", document_id: "文档编号",
  chunk_id: "切片编号", filename: "文件名", page_number: "页码", section_path: "章节", source_url: "来源网址",
  quoted_excerpt: "引用原文", fetched_at: "获取时间", file_id: "文件编号", original_filename: "原文件名",
  relative_path: "相对路径", extension: "扩展名", mime_type: "文件类型", size_bytes: "字节数", content_hash: "内容校验值",
  parse_status: "解析状态", parse_error: "解析错误", extracted_text: "解析文本", page_map: "页码映射", extracted_at: "解析时间",
  primary_intent: "主要意图", selected_agents: "Agents", selected_skills: "Skills", retrieval_rounds: "检索轮数",
  tool_call_count: "工具调用数", prompt_tokens: "输入 Token", completion_tokens: "输出 Token", total_tokens: "总 Token",
  started_at: "开始时间", finished_at: "结束时间", duration_ms: "耗时", error_code: "错误码", event_id: "事件编号",
  sequence_number: "顺序编号", node_name: "节点", event_type: "事件类型", input_summary: "输入摘要", output_summary: "输出摘要",
  payload: "完整数据", summary_id: "摘要编号", covered_until_message_id: "摘要截止消息", message_count: "消息数量",
  memory_id: "记忆编号", memory_type: "记忆类型", embedding: "向量", importance: "重要程度",
  source_conversation_id: "来源对话", source_message_ids: "来源消息", source_type: "来源类型",
  published_at: "发布日期", metadata_extra: "补充信息",
  chunk_index: "切片序号", chunk_count: "切片总数", source_key: "来源标识", display_name: "显示名称", url: "网址",
  adapter: "适配器", cache_ttl_seconds: "缓存时长", enabled: "启用", config_snapshot: "配置快照", snapshot_id: "快照编号",
  normalized_text: "整理后文本", expires_at: "过期时间", job_id: "任务编号", total_items: "文件总数", completed_items: "已完成",
  failed_items: "失败数", item_id: "文件任务编号", detected_metadata: "分类建议", confirmed_metadata: "确认分类", error_message: "错误信息",
  case_id: "用例编号", name: "名称", query: "问题", conversation_context: "对话上下文", expected_intent: "预期意图",
  expected_agents: "预期 Agents", expected_skills: "预期 Skills", relevant_document_ids: "相关文档", relevant_chunk_ids: "相关切片",
  reference_answer: "参考回答", should_answer: "应当回答", run_id: "运行编号", model_snapshot: "模型快照", prompt_snapshot: "提示词快照",
  summary_metrics: "汇总指标", result_id: "结果编号", prediction: "预测结果", deterministic_metrics: "确定性指标", judge_metrics: "评判指标",
};
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f-]{27}$/i;
const NULL_FILTER = "__NULL__";

function displayTime(value: string): string {
  return formatBeijingTime(value);
}

function rawText(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "null";
}

function columnWidth(column: ColumnInfo): number {
  if (column.type.startsWith("TIMESTAMP")) return 190;
  if (column.type === "UUID") return 150;
  if (["role", "status", "enabled", "is_active", "duration_ms", "page_number", "sequence_number"].includes(column.name)) return 120;
  if (column.kind === "json" || ["content", "title", "query", "extracted_text", "normalized_text", "error_message"].includes(column.name)) return 260;
  return 165;
}

function JsonBranch({ value, label, root = false }: { value: unknown; label: string; root?: boolean }) {
  const [open, setOpen] = useState(root);
  if (value === null || typeof value !== "object") {
    return <div className="database-json-leaf"><strong>{label}</strong>: {JSON.stringify(value)}</div>;
  }
  const entries = Object.entries(value);
  return <div className="database-json-branch">
    <Button type="text" size="small" onClick={() => setOpen(!open)}>
      {open ? "▾" : "▸"} {label} <Typography.Text type="secondary">{Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`}</Typography.Text>
    </Button>
    {open && <div className="database-json-children">{entries.map(([key, item]) => <JsonBranch key={key} label={key} value={item} />)}</div>}
  </div>;
}

function FullValue({ value, column }: { value: unknown; column: ColumnInfo }) {
  if (value === null) return <Tag>NULL</Tag>;
  if (column.sensitive) return <Tag>已隐藏</Tag>;
  const text = rawText(value);
  const copy = <Typography.Text copyable={{ text }}>复制完整值</Typography.Text>;
  const raw = <div>{copy}<pre className="database-full-value">{text}</pre></div>;
  if (column.kind === "json") return <Tabs size="small" destroyOnHidden items={[
    { key: "tree", label: "结构", children: <div className="database-json-view"><JsonBranch key={text} label="根节点" value={beijingExport(value)} root /></div> },
    { key: "raw", label: "JSON 原文", children: raw },
  ]} />;
  if (column.kind === "vector") return <div>
    <Tag>{Array.isArray(value) ? value.length : "未知"} 维向量</Tag>
    <details><summary>展开完整向量</summary>{raw}</details>
  </div>;
  if (typeof value === "string" && ["content", "extracted_text", "normalized_text", "quoted_excerpt", "reference_answer"].includes(column.name)) {
    return <Tabs size="small" destroyOnHidden items={[
      { key: "raw", label: "原始文本", children: raw },
      { key: "markdown", label: "阅读预览", children: <div className="database-markdown"><ReactMarkdown>{value}</ReactMarkdown></div> },
    ]} />;
  }
  if (typeof value === "string" && column.type.startsWith("TIMESTAMP")) return <div>
    <span className="database-time">{displayTime(value)}</span> <Tag>UTC+8</Tag>{raw}
  </div>;
  if (column.name === "duration_ms" && typeof value === "number") return <div>
    <Tag>{(value / 1000).toFixed(2)} 秒</Tag>{raw}
  </div>;
  return raw;
}

function readFilters(raw: string | null): Record<string, string> {
  try {
    const value: unknown = JSON.parse(raw ?? "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
  } catch { return {}; }
}

export function DatabasePage() {
  const [params, setParams] = useSearchParams();
  const [catalog, setCatalog] = useState<Catalog>();
  const [catalogError, setCatalogError] = useState("");
  const [catalogBusy, setCatalogBusy] = useState(true);
  const [data, setData] = useState<RowPage>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [tableSearch, setTableSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [filterColumn, setFilterColumn] = useState<string>();
  const [filterInput, setFilterInput] = useState("");
  const [columnSelections, setColumnSelections] = useState<Record<string, string[]>>({});
  const [detail, setDetail] = useState<DatabaseRow>();
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailError, setDetailError] = useState("");
  const detailGeneration = useRef(0);
  const [messageApi, contextHolder] = message.useMessage();
  const tableKey = `${params.get("schema") ?? "app"}.${params.get("table") ?? "conversations"}`;
  const info = catalog?.tables.find((table) => table.key === tableKey);
  const filterText = params.get("filters");
  const filters = useMemo(() => readFilters(filterText), [filterText]);
  const query = params.get("q") ?? "";
  const page = Math.max(1, Math.min(100000, Number(params.get("page")) || 1));
  const pageSize = [20, 50, 100].includes(Number(params.get("size"))) ? Number(params.get("size")) : 20;
  const sortBy = params.get("sort") ?? "";
  const descending = params.get("direction") !== "asc";
  const selectedColumns = info ? columnSelections[tableKey] ?? info.default_columns : [];

  useEffect(() => {
    const controller = new AbortController();
    setCatalogBusy(true);
    setCatalogError("");
    api<Catalog>("/developer/database", { signal: controller.signal })
      .then(setCatalog)
      .catch((failure: Error) => { if (!controller.signal.aborted) setCatalogError(failure.message); })
      .finally(() => { if (!controller.signal.aborted) setCatalogBusy(false); });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => { setSearchInput(query); }, [query]);
  useEffect(() => {
    setDetailOpen(false);
    setDetail(undefined);
    detailGeneration.current += 1;
    setFilterColumn(undefined);
    setFilterInput("");
  }, [tableKey]);

  useEffect(() => {
    if (!info) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setData(undefined);
    const options = new URLSearchParams({ page: String(page), page_size: String(pageSize), filters: JSON.stringify(filters), q: query, descending: String(descending) });
    if (sortBy) options.set("sort_by", sortBy);
    api<RowPage>(`/developer/database/${info.schema}/${info.name}?${options}`, { signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return;
        if (page > 1 && value.total <= (page - 1) * pageSize) {
          setParams((current) => { const next = new URLSearchParams(current); next.set("page", "1"); return next; }, { replace: true });
        } else setData(value);
      })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [info?.key, page, pageSize, filterText, query, sortBy, descending, revision]);

  const updateParams = (values: Record<string, string | null>) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      Object.entries(values).forEach(([key, value]) => value === null ? next.delete(key) : next.set(key, value));
      return next;
    });
  };
  const navigateTable = (key: string, targetFilters: Record<string, string> = {}) => {
    const [schema, table] = key.split(".");
    setParams({ schema, table, ...(Object.keys(targetFilters).length ? { filters: JSON.stringify(targetFilters) } : {}) });
    setDetailOpen(false);
    detailGeneration.current += 1;
  };
  const openDetail = async (row: DatabaseRow) => {
    if (!info) return;
    const generation = ++detailGeneration.current;
    setDetailOpen(true);
    setDetail(undefined);
    setDetailError("");
    setDetailBusy(true);
    try {
      const value = await api<DatabaseRow>(`/developer/database/${info.schema}/${info.name}/records/${encodeURIComponent(row.key)}`);
      if (detailGeneration.current === generation) setDetail(value);
    } catch (failure) {
      if (detailGeneration.current === generation) setDetailError((failure as Error).message);
    } finally { if (detailGeneration.current === generation) setDetailBusy(false); }
  };
  const addFilter = (value = filterInput) => {
    if (!filterColumn) { messageApi.warning("先选择筛选字段。"); return; }
    if (value === "") { messageApi.warning("请输入筛选值，或使用“筛选 NULL”。"); return; }
    updateParams({ filters: JSON.stringify({ ...filters, [filterColumn]: value }), page: "1" });
    setFilterInput("");
  };

  const cell = (value: unknown, column: ColumnInfo, row: DatabaseRow) => {
    if (value === null) return <Tag>NULL</Tag>;
    if (column.sensitive) return <Tag>已隐藏</Tag>;
    if (column.kind === "vector") return <Typography.Text type="secondary">{(value as { dimensions: number })?.dimensions ?? "?"} 维 · 详情中展开</Typography.Text>;
    if (typeof value === "boolean") return <Tag color={value ? "green" : "default"}>{value ? "true" : "false"}</Tag>;
    if (column.name === "duration_ms" && typeof value === "number") return (value / 1000).toFixed(2);
    if (typeof value === "string" && column.type.startsWith("TIMESTAMP")) return <span className="database-time">{displayTime(value)}</span>;
    if (typeof value === "string" && (UUID_PATTERN.test(value) || column.reference)) {
      return <Space size={4}>
        {column.reference ? <Button className="database-id-link" type="link" onClick={() => navigateTable(column.reference!.table, { [column.reference!.column]: value })} title={`查看 ${column.reference.table}`}>
          {UUID_PATTERN.test(value) ? `${value.slice(0, 8)}…` : value}
        </Button> : <Typography.Text code>{`${value.slice(0, 8)}…`}</Typography.Text>}
        <Typography.Text copyable={{ text: value }} />
      </Space>;
    }
    if (column.kind === "text" || column.kind === "json") return <div className="database-cell-content" onClick={() => void openDetail(row)} title="点击查看完整记录">
      <span>{rawText(value)}</span>
      {(column.kind === "json" || rawText(value).length >= 120) && <Typography.Text type="secondary"> · 展开</Typography.Text>}
    </div>;
    return String(value);
  };
  const columns: ColumnsType<DatabaseRow> = info ? [
    ...selectedColumns.flatMap((name) => {
      const column = info.columns.find((item) => item.name === name);
      if (!column) return [];
      return [{
        key: name,
        title: <div>{LABELS[name] ?? name}<div className="database-column-name">{name}{name === "duration_ms" ? " · 秒" : ""}</div></div>,
        width: columnWidth(column),
        sorter: column.sortable,
        sortOrder: sortBy === name ? descending ? "descend" as const : "ascend" as const : null,
        render: (_: unknown, row: DatabaseRow) => cell(row.values[name], column, row),
      }];
    }),
    { key: "actions", title: "操作", width: 90, fixed: "right", render: (_, row) => <Button type="link" onClick={() => void openDetail(row)}>详情</Button> },
  ] : [];

  const tableNodes = catalog?.schemas.map((schema) => ({
    key: schema,
    title: <strong>{schema}</strong>,
    selectable: false,
    children: catalog.tables.filter((table) => table.schema === schema && `${table.key} ${table.description}`.toLowerCase().includes(tableSearch.toLowerCase())).map((table) => ({
      key: table.key,
      title: <div className="database-tree-table"><span>{table.name}</span><Typography.Text type="secondary">{table.row_count}</Typography.Text></div>,
    })),
  })).filter((node) => node.children.length);
  const detailRelations = info && detail ? [
    ...info.relations.map((relation) => ({ ...relation, value: detail.values[relation.source_column] })),
    ...info.columns.filter((column) => column.reference && detail.values[column.name] != null).map((column) => ({
      ...column.reference!, label: `查看${LABELS[column.name] ?? column.name}`, value: detail.values[column.name],
    })),
  ].filter((relation) => relation.value != null) : [];

  return <div className="database-page">
    {contextHolder}
    <div className="database-heading">
      <div><Typography.Title level={2}>数据库</Typography.Title><Typography.Text type="secondary">{catalog?.database ?? "qust_campus_agent"} · 业务表浏览与关联查询</Typography.Text></div>
      <Button icon={<ReloadOutlined />} loading={loading || catalogBusy} onClick={() => setRevision((value) => value + 1)}>刷新数据</Button>
    </div>
    {catalogError && <Alert type="error" showIcon title={catalogError} />}
    <div className="database-layout">
      <Card className="database-sidebar" size="small" title={<Space><DatabaseOutlined />表目录 <Tag>{catalog?.tables.length ?? "…"}</Tag></Space>}>
        <Input prefix={<SearchOutlined />} placeholder="查找表名或中文用途" value={tableSearch} allowClear onChange={(event) => setTableSearch(event.target.value)} />
        {catalogBusy && !catalog ? <Spin className="database-catalog-spin" /> : <Tree blockNode defaultExpandAll selectedKeys={[tableKey]} treeData={tableNodes} onSelect={(keys) => { if (keys[0]) navigateTable(String(keys[0])); }} />}
      </Card>
      <Card className="database-main" title={info ? <Space wrap><span>{info.description}</span><Typography.Text code>{info.key}</Typography.Text><Tag>{info.row_count} 条记录</Tag></Space> : "选择一张表"}>
        {!info ? (catalogBusy ? <Spin /> : <Empty description="表不存在，请从左侧选择业务表" />) : <>
          <Tabs items={[
            { key: "data", label: "数据", children: <>
              <div className="database-toolbar">
                <Input.Search placeholder="搜索文本内容" allowClear value={searchInput} onChange={(event) => setSearchInput(event.target.value)} onSearch={(value) => updateParams({ q: value.trim() || null, page: "1" })} />
                <Select mode="multiple" maxTagCount={1} value={selectedColumns} placeholder="选择显示列" options={info.columns.map((column) => ({ value: column.name, label: `${LABELS[column.name] ?? column.name} · ${column.name}` }))} onChange={(values) => setColumnSelections((current) => ({ ...current, [tableKey]: values }))} />
              </div>
              <Space wrap className="database-filters">
                <Select placeholder="筛选字段" value={filterColumn} style={{ width: 200 }} options={info.columns.filter((column) => column.filterable).map((column) => ({ value: column.name, label: `${LABELS[column.name] ?? column.name} · ${column.name}` }))} onChange={setFilterColumn} />
                <Input placeholder="完整编号或精确值" style={{ width: 260 }} maxLength={500} value={filterInput} onChange={(event) => setFilterInput(event.target.value)} onPressEnter={() => addFilter()} />
                <Button onClick={() => addFilter()}>添加筛选</Button>
                <Button disabled={!info.columns.find((column) => column.name === filterColumn)?.nullable} onClick={() => addFilter(NULL_FILTER)}>筛选 NULL</Button>
                {(Object.keys(filters).length > 0 || query) && <Button onClick={() => updateParams({ filters: null, q: null, page: "1" })}>清空条件</Button>}
              </Space>
              <div className="database-filter-tags">{Object.entries(filters).map(([name, value]) => <Tag key={name} closable onClose={() => { const next = { ...filters }; delete next[name]; updateParams({ filters: JSON.stringify(next), page: "1" }); }}>
                {LABELS[name] ?? name} = {value === NULL_FILTER ? "NULL" : value}
              </Tag>)}</div>
              {error && <Alert type="error" showIcon title={error} />}
              <Table<DatabaseRow> rowKey="key" size="small" loading={loading} dataSource={data?.rows ?? []} columns={columns} scroll={{ x: "max-content" }}
                pagination={{ current: page, pageSize, total: data?.total ?? 0, showSizeChanger: true, pageSizeOptions: [20, 50, 100], showTotal: (total) => `筛选结果 ${total} 条` }}
                onChange={(pagination, _filters, sorter, extra) => {
                  if (extra.action === "paginate") updateParams({ page: String(pagination.current ?? 1), size: String(pagination.pageSize ?? 20) });
                  if (extra.action === "sort") { const selected = Array.isArray(sorter) ? sorter[0] : sorter; updateParams({ sort: selected.order ? String(selected.columnKey) : null, direction: selected.order === "ascend" ? "asc" : "desc", page: "1" }); }
                }} />
              <Typography.Paragraph className="database-table-hint" type="secondary">正文和 JSON 显示摘要；点击内容或“详情”读取完整记录。蓝色编号可跳转关联表。</Typography.Paragraph>
            </> },
            { key: "structure", label: "表结构", children: <Table<ColumnInfo> rowKey="name" size="small" pagination={false} dataSource={info.columns} scroll={{ x: "max-content" }} columns={[
              { title: "字段", dataIndex: "name", width: 240, render: (name: string) => <div><Typography.Text code>{name}</Typography.Text><div className="database-column-name">{LABELS[name] ?? "—"}</div></div> },
              { title: "数据库类型", dataIndex: "type", width: 230 },
              { title: "允许为空", dataIndex: "nullable", width: 100, render: (value: boolean) => value ? "是" : "否" },
              { title: "主键", dataIndex: "primary_key", width: 80, render: (value: boolean) => value ? <Tag color="blue">PK</Tag> : "—" },
              { title: "外键约束", dataIndex: "foreign_keys", width: 330, render: (values: string[]) => values.length ? values.map((value) => <div key={value}><Typography.Text code>{value}</Typography.Text></div>) : "—" },
              { title: "说明", render: (_, column) => column.sensitive ? "认证字段，值已隐藏" : column.reference && !column.foreign_keys.length ? `逻辑关联 ${column.reference.table}.${column.reference.column}` : info.key === "app.messages" && (column.name === "trace_id" || column.name === "request_id") ? "旧记录可为空；新聊天消息自动关联" : "—" },
            ]} /> },
          ]} />
        </>}
      </Card>
    </div>
    <Drawer title={info ? `${info.key} · 完整记录` : "完整记录"} width={900} open={detailOpen} onClose={() => { setDetailOpen(false); detailGeneration.current += 1; }}>
      <Spin spinning={detailBusy}>
        {detailError && <Alert type="error" showIcon title={detailError} />}
        {detail && info && <>
          <Typography.Paragraph copyable={{ text: detail.key }}><Typography.Text code>{detail.key}</Typography.Text></Typography.Paragraph>
          <Space wrap className="database-related-links">{detailRelations.map((relation, index) => <Button key={`${relation.table}.${relation.column}.${index}`} size="small" onClick={() => navigateTable(relation.table, { [relation.column]: String(relation.value) })}>{relation.label}</Button>)}</Space>
          {info.key === "app.messages" && detail.values.trace_id === null && <Alert type="info" showIcon title="此消息没有可确认的请求关联。历史提问或评测上下文可出现 NULL。" />}
          <Descriptions bordered column={1} size="small" className="database-record" items={info.columns.map((column) => ({
            key: column.name,
            label: <div>{LABELS[column.name] ?? column.name}<div className="database-column-name">{column.name}</div></div>,
            children: <FullValue column={column} value={detail.values[column.name]} />,
          }))} />
        </>}
      </Spin>
    </Drawer>
  </div>;
}
