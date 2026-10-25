import { WORKSPACE_STORAGE_KEY } from "../workspace";
import { Button, Space, Spin, Table, Tabs, Typography, message } from "antd";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { formatBeijingTime } from "../utils/time";
import type { Trace } from "../types";
import { UnifiedEvaluationPanel } from "./UnifiedEvaluationPanel";
import { TraceDrawer } from "./TraceDrawer";
import { ROUTE_LABELS } from "../utils/evaluationLabels";
import { seconds, STATUS } from "./traceDetails";

interface IngestionItem {
  item_id: string;
  relative_path: string;
  status: string;
  detected_metadata: Record<string, string>;
  confirmed_metadata: Record<string, string>;
  error_message?: string;
}

interface IngestionJob {
  job_id: string;
  status: string;
  total_items: number;
  completed_items: number;
  failed_items: number;
  items: IngestionItem[];
}

export function DeveloperPage() {
  const tabStorageKey = `qust-developer-tab:${encodeURIComponent(WORKSPACE_STORAGE_KEY)}`;
  const [activeTab, setActiveTab] = useState(() => {
    try {
      const saved = sessionStorage.getItem(tabStorageKey);
      return saved && ["traces", "evaluation", "ingestion"].includes(saved) ? saved : "traces";
    } catch { return "traces"; }
  });
  const [traces, setTraces] = useState<Trace[]>([]);
  const [job, setJob] = useState<IngestionJob>();
  const [selectedTrace, setSelectedTrace] = useState<{ id: string; section: string }>();
  const [busy, setBusy] = useState(false);
  const [restoringJob, setRestoringJob] = useState(true);
  const [messageApi, contextHolder] = message.useMessage();
  const refresh = async () => {
    setTraces(await api<Trace[]>("/developer/traces"));
  };
  useEffect(() => { void refresh().catch(error => messageApi.error((error as Error).message)); }, []);
  useEffect(() => {
    const controller = new AbortController();
    setRestoringJob(true);
    void api<IngestionJob | null>("/developer/ingestion/current", { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setJob(value ?? undefined); })
      .catch(error => { if (!controller.signal.aborted) messageApi.error((error as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setRestoringJob(false); });
    return () => controller.abort();
  }, [WORKSPACE_STORAGE_KEY]);
  useEffect(() => {
    if (!job || job.status !== "running") return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const updated = await api<IngestionJob>(`/developer/ingestion/${job.job_id}`);
        if (stopped) return;
        setJob(updated);
        if (updated.status === "completed" && updated.completed_items === updated.total_items) {
          messageApi.success("已全部入库");
        } else if (["partial", "failed"].includes(updated.status)) {
          messageApi.error("入库未全部成功，请查看文件状态。");
        } else if (updated.status === "running") {
          timer = setTimeout(() => void poll(), 1000);
        }
      } catch (error) {
        if (stopped) return;
        messageApi.error((error as Error).message);
        timer = setTimeout(() => void poll(), 2000);
      }
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [job?.job_id, job?.status, messageApi]);
  const scan = async () => {
    setBusy(true);
    try {
      setJob(await api<IngestionJob>("/developer/ingestion/scan", { method: "POST" }));
    } catch (error) { messageApi.error((error as Error).message); }
    finally { setBusy(false); }
  };
  const confirmAndStart = async () => {
    if (!job) return;
    setBusy(true);
    try {
      await api(`/developer/ingestion/${job.job_id}/confirm`, {
        method: "POST",
        body: JSON.stringify({
          items: job.items.map((item) => ({ item_id: item.item_id, metadata: item.detected_metadata }))
        })
      });
      await api(`/developer/ingestion/${job.job_id}/start`, { method: "POST" });
      setJob({ ...job, status: "running", completed_items: 0, failed_items: 0 });
    } catch (error) { messageApi.error((error as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <div className="developer-page">
      {contextHolder}
      <Tabs
        activeKey={activeTab}
        onChange={key => {
          setActiveTab(key);
          try { sessionStorage.setItem(tabStorageKey, key); } catch { /* 保留当前页面内的状态 */ }
        }}
        items={[
          {
            key: "traces",
            label: "Trace",
            children: <Space orientation="vertical" className="developer-full">
              <Space><Button onClick={() => void refresh().catch(error => messageApi.error((error as Error).message))}>刷新</Button>
                <Typography.Text type="secondary">聊天请求；评测 Trace 从对应评测结果打开。</Typography.Text></Space>
              <Table<Trace> rowKey="trace_id" dataSource={traces} pagination={{ pageSize: 10 }} scroll={{ x: 1000 }} columns={[
                { title: "开始时间", dataIndex: "started_at", width: 180, render: value => <span style={{ whiteSpace: 'pre' }}>{formatBeijingTime(value, false, '  ')}</span> },
                { title: "输入", width: 280, render: (_, row) => <Button type="link" className="trace-input" title={row.query ?? undefined} onClick={() => setSelectedTrace({ id: row.trace_id, section: 'overview' })}>{row.query ?? '未记录'}</Button> },
                { title: "意图", render: (_, row) => <Button type="link" onClick={() => setSelectedTrace({ id: row.trace_id, section: 'intent' })}>{row.primary_intent ?? '未记录'}</Button> },
                { title: "路由", render: (_, row) => <Button type="link" onClick={() => setSelectedTrace({ id: row.trace_id, section: 'route' })}>{ROUTE_LABELS[row.route ?? ''] ?? '未记录'}</Button> },
                { title: "状态", render: (_, row) => <Button type="link" onClick={() => setSelectedTrace({ id: row.trace_id, section: 'chain' })}>{STATUS[row.status] ?? row.status}</Button> },
                { title: "耗时", render: (_, row) => <Button type="link" onClick={() => setSelectedTrace({ id: row.trace_id, section: 'timing' })}>{seconds(row.duration_ms)}</Button> },
              ]} />
            </Space>
          },
          {
            key: "evaluation",
            label: "评测",
            children: <UnifiedEvaluationPanel />
          },
          {
            key: "ingestion",
            label: "知识库入库",
            children: <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Space>
                <Button type="primary" disabled={restoringJob || job?.status === "running"} loading={busy || restoringJob} onClick={() => void scan()}>扫描raw目录</Button>
                <Button disabled={restoringJob || !job?.total_items || !["awaiting_confirmation", "confirmed"].includes(job.status)} loading={busy} onClick={() => void confirmAndStart()}>确认入库</Button>
              </Space>
              {job && <Space size={8}><Typography.Text role="status" aria-live="polite">
                （{job.completed_items}/{job.total_items}）
                {job.status === "completed" && job.completed_items === job.total_items ? " 已全部入库" : ""}
              </Typography.Text>{job.status === "running" && <Spin size="small" aria-label="正在入库" />}</Space>}
              {job && <Table rowKey="item_id" dataSource={job.items} pagination={{ pageSize: 8 }} columns={[
                { title: "文件", dataIndex: "relative_path" },
                { title: "状态", dataIndex: "status" },
              ]} />}
            </Space>
          }
        ]}
      />
      <TraceDrawer traceId={selectedTrace?.id} section={selectedTrace?.section} onClose={() => setSelectedTrace(undefined)} />
    </div>
  );
}
