import { WORKSPACE_STORAGE_KEY } from "../workspace";
import { CloseOutlined, FileTextOutlined, PlusOutlined, SearchOutlined, SendOutlined, UploadOutlined } from "@ant-design/icons";
import { Button, Empty, Input, Layout, List, Modal, Space, Spin, Tag, Tooltip, Typography, Upload, message } from "antd";
import { useEffect, useRef, useState } from "react";

import { api, streamRequest } from "../api/client";
import { StreamHttpError } from "../api/sse";
import { MessageBubble } from "../components/MessageBubble";
import type { Conversation, Message } from "../types";
import { formatBeijingTime } from "../utils/time";
import {
  applyStreamEvent, createPendingResponse, loadPendingResponse, mergeMessages,
  pendingStorageKey, savePendingResponse, type PendingResponse,
} from "./chatStream";

interface UploadedFile {
  file_id: string;
  filename: string;
  parse_status: string;
  parse_error?: string | null;
}

export function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<string>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [pending, setPending] = useState<PendingResponse>();
  const [search, setSearch] = useState("");
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState<string>();
  const [messageApi, contextHolder] = message.useMessage();
  const [modalApi, modalContext] = Modal.useModal();
  const endRef = useRef<HTMLDivElement>(null);
  const currentRef = useRef<string | undefined>(undefined);
  const pendingRef = useRef<PendingResponse | undefined>(undefined);
  const connectionRef = useRef<AbortController | undefined>(undefined);
  const submittingRef = useRef(false);
  const composingRef = useRef(false);
  const uploadingRef = useRef(false);
  const deletedRef = useRef(new Set<string>());
  const lastConversationKey = `qust-last-conversation:${encodeURIComponent(WORKSPACE_STORAGE_KEY)}`;
  const running = submitting || pending?.status === "streaming";
  const anyBusy = conversations.some(item => item.is_busy);
  const progress = pending?.progress ?? (submitting ? "正在启动 Agent…" : "");

  const updatePending = (value: PendingResponse | undefined) => {
    pendingRef.current = value;
    setPending(value);
    if (value) savePendingResponse(sessionStorage, WORKSPACE_STORAGE_KEY, value);
  };
  const forgetPending = (conversationId: string) => {
    try { sessionStorage.removeItem(pendingStorageKey(WORKSPACE_STORAGE_KEY, conversationId)); } catch { /* 可继续聊天 */ }
  };
  const selectConversation = (id: string) => {
    if (currentRef.current === id) return;
    connectionRef.current?.abort();
    connectionRef.current = undefined;
    currentRef.current = id;
    updatePending(undefined);
    setMessages([]);
    setFiles([]);
    setCurrent(id);
    try { sessionStorage.setItem(lastConversationKey, id); } catch { /* 可继续聊天 */ }
  };
  const refreshConversations = async () => {
    const values = await api<Conversation[]>("/conversations");
    setConversations(values.filter(item => !deletedRef.current.has(item.conversation_id)));
    return values;
  };
  const refreshFiles = async (conversationId: string, signal?: AbortSignal) => {
    const values = await api<UploadedFile[]>(`/conversations/${conversationId}/files`, { signal });
    if (currentRef.current === conversationId && !signal?.aborted) setFiles(values);
  };
  const refreshMessages = async (conversationId: string, signal?: AbortSignal) => {
    const values = await api<Message[]>(`/conversations/${conversationId}/messages`, { signal });
    if (currentRef.current === conversationId && !signal?.aborted) {
      setMessages((previous) => mergeMessages(previous, values));
    }
    return values;
  };

  const connect = async (value: PendingResponse) => {
    connectionRef.current?.abort();
    const controller = new AbortController();
    connectionRef.current = controller;
    updatePending({ ...value, status: "streaming" });
    const active = () => currentRef.current === value.conversationId
      && connectionRef.current === controller && !controller.signal.aborted;

    const recoverSaved = async () => {
      const history = await refreshMessages(value.conversationId, controller.signal);
      if (!active()) return;
      const saved = history.find((item) => item.role === "assistant" && item.request_id === value.requestId);
      if (saved) {
        forgetPending(value.conversationId);
        updatePending(undefined);
      } else {
        const draft = pendingRef.current;
        if (draft) updatePending({
          ...draft, status: "failed", progress: "重放已过期，当前没有已保存的回答。",
          draft: {
            ...draft.draft, status: "failed",
            display_metadata: {
              ...draft.draft.display_metadata, generating: false, incomplete: true,
              error_message: "重放已过期，当前没有已保存的回答。",
            },
          },
        });
      }
    };
    try {
      let expired = false;
      await streamRequest(
        `/conversations/${value.conversationId}/requests/${value.requestId}/events`,
        (event) => {
          if (!active()) return;
          if (event.type === "request.started") void refreshConversations().catch(() => {});
          if (event.type === "stream.expired") { expired = true; return; }
          const draft = pendingRef.current;
          if (!draft || draft.requestId !== value.requestId) return;
          const next = applyStreamEvent(draft, event);
          if (event.type === "response.completed") {
            setMessages((previous) => mergeMessages(previous, [next.draft]));
            forgetPending(value.conversationId);
            updatePending(undefined);
          } else {
            updatePending(next);
          }
        },
        {
          signal: controller.signal, after: value.cursor,
          onRetry: (attempt) => {
            const draft = pendingRef.current;
            if (active() && draft) updatePending({
              ...draft, progress: `连接中断，正在第 ${attempt} 次续接…`,
            });
          },
        },
      );
      if (!active()) return;
      if (expired) await recoverSaved();
      else {
        await refreshMessages(value.conversationId, controller.signal);
        await refreshConversations();
      }
    } catch (error) {
      if (!active()) return;
      if (error instanceof StreamHttpError && error.status === 410) {
        try { await recoverSaved(); return; } catch { /* 保留草稿等待手动续接 */ }
      }
      if (!active()) return;
      const draft = pendingRef.current;
      if (draft) updatePending({
        ...draft, status: "interrupted",
        progress: "连接已中断，草稿已保留。可重新连接继续接收。",
        draft: {
          ...draft.draft,
          display_metadata: { ...draft.draft.display_metadata, generating: false, interrupted: true },
        },
      });
      messageApi.warning((error as Error).message);
    } finally {
      if (connectionRef.current === controller) connectionRef.current = undefined;
    }
  };

  useEffect(() => {
    let alive = true;
    void refreshConversations().then((values) => {
      if (!alive || currentRef.current || !values.length) return;
      let remembered: string | null = null;
      try { remembered = sessionStorage.getItem(lastConversationKey); } catch { /* 默认最新会话 */ }
      selectConversation(values.find((item) => item.conversation_id === remembered)?.conversation_id
        ?? values[0].conversation_id);
    }).catch((error: Error) => { if (alive) messageApi.error(error.message); });
    return () => { alive = false; currentRef.current = undefined; };
  }, [WORKSPACE_STORAGE_KEY]);

  useEffect(() => {
    // 标题在最终回答之后异步完成，同时同步其它标签页的忙碌状态。
    const refresh = () => { void refreshConversations().catch(() => {}); };
    window.addEventListener("focus", refresh);
    const timer = anyBusy || running || uploading
      ? window.setInterval(refresh, 2000) : undefined;
    return () => {
      window.removeEventListener("focus", refresh);
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [WORKSPACE_STORAGE_KEY, anyBusy, running, uploading]);

  useEffect(() => {
    if (!current) return;
    let alive = true;
    currentRef.current = current;
    const fileController = new AbortController();
    void refreshFiles(current, fileController.signal).catch((error: Error) => {
      if (alive && !fileController.signal.aborted) messageApi.error(error.message);
    });
    const restored = pendingRef.current?.conversationId === current
      ? pendingRef.current : loadPendingResponse(sessionStorage, WORKSPACE_STORAGE_KEY, current);
    if (!pendingRef.current && restored) updatePending(restored);
    void refreshMessages(current).then((history) => {
      if (!alive || currentRef.current !== current) return;
      const draft = pendingRef.current;
      if (!draft || draft.conversationId !== current) return;
      if (history.some((item) => item.role === "assistant" && item.request_id === draft.requestId)) {
        // 仍连接时等待最终事件；恢复页面时直接采用数据库结果。
        if (!connectionRef.current) {
          forgetPending(current);
          updatePending(undefined);
        }
      } else if (draft.status !== "failed" && !connectionRef.current) {
        void connect(draft);
      }
    }).catch((error: Error) => { if (alive) messageApi.error(error.message); });
    return () => {
      alive = false;
      fileController.abort();
      connectionRef.current?.abort();
      connectionRef.current = undefined;
    };
  }, [current, WORKSPACE_STORAGE_KEY]);

  const visibleMessages = [...messages];
  if (pending && !messages.some((item) => item.role === "assistant" && item.request_id === pending.requestId)) {
    if (pending.query && !messages.some((item) => item.role === "user" && item.request_id === pending.requestId)) {
      visibleMessages.push({
        message_id: `user:${pending.requestId}`, request_id: pending.requestId,
        role: "user", content: pending.query, status: "success", agent_names: [],
        display_metadata: {}, citations: [], created_at: pending.draft.created_at,
      });
    }
    visibleMessages.push(pending.draft);
  }
  useEffect(() => {
    if (pending?.requestId) endRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
  }, [pending?.requestId]);

  useEffect(() => {
    // 只在用户靠近底部时跟随增量，允许用户阅读前文。
    const node = endRef.current;
    const scroller = node?.parentElement;
    if (node && scroller && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 160) {
      node.scrollIntoView({ behavior: "auto", block: "end" });
    }
  }, [messages, pending?.draft.content, progress]);

  const createConversation = async () => {
    const value = await api<Conversation>("/conversations", {
      method: "POST", body: JSON.stringify({ title: "新对话" }),
    });
    await refreshConversations();
    selectConversation(value.conversation_id);
  };

  const deleteConversation = (item: Conversation) => {
    modalApi.confirm({
      title: "永久删除这个聊天？",
      content: <div><p>“{item.title}”的消息、引用、会话摘要和上传文件副本将一并删除，无法恢复。</p>
        <p>长期记忆与调试记录会保留。</p></div>,
      okText: "永久删除", cancelText: "取消", okButtonProps: { danger: true },
      onOk: async () => {
        setDeleting(item.conversation_id);
        try {
          await api(`/conversations/${item.conversation_id}`, { method: "DELETE" });
          deletedRef.current.add(item.conversation_id);
          forgetPending(item.conversation_id);
          setConversations(values => values.filter(value => value.conversation_id !== item.conversation_id));
          if (currentRef.current === item.conversation_id) {
            connectionRef.current?.abort();
            connectionRef.current = undefined;
            currentRef.current = undefined;
            setCurrent(undefined);
            updatePending(undefined);
            setMessages([]);
            setFiles([]);
            setQuery("");
            try { sessionStorage.removeItem(lastConversationKey); } catch { /* 可继续聊天 */ }
          }
          messageApi.success("聊天及上传文件副本已删除");
          void refreshConversations().catch(() => {});
        } catch (error) {
          messageApi.error((error as Error).message);
          throw error;
        } finally {
          setDeleting(undefined);
        }
      },
    });
  };

  const send = async () => {
    if (!query.trim() || submittingRef.current || uploadingRef.current || running || pending?.status === "interrupted") return;
    submittingRef.current = true;
    setSubmitting(true);
    const content = query.trim();
    setQuery("");
    try {
      let conversationId = currentRef.current;
      if (!conversationId) {
        const created = await api<Conversation>("/conversations", {
          method: "POST", body: JSON.stringify({ title: "新对话" }),
        });
        conversationId = created.conversation_id;
        selectConversation(conversationId);
      }
      const accepted = await api<{ request_id: string }>(
        `/conversations/${conversationId}/messages`,
        { method: "POST", body: JSON.stringify({ content }) },
      );
      const draft = createPendingResponse(conversationId, accepted.request_id, content);
      savePendingResponse(sessionStorage, WORKSPACE_STORAGE_KEY, draft);
      if (currentRef.current === conversationId) {
        const previous = pendingRef.current;
        if (previous?.status === "failed") {
          setMessages((history) => mergeMessages(history, [previous.draft]));
        }
        void connect(draft);
        void refreshMessages(conversationId).catch(() => {});
      }
      void refreshConversations().catch(() => {});
    } catch (error) {
      messageApi.error((error as Error).message);
      setQuery((previous) => previous || content);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };
  return (
    <Layout className="chat-layout">
      {contextHolder}
      {modalContext}
      <Layout.Sider width={260} theme="light" className="conversation-sider">
        <div className="sider-heading">
          <Typography.Title level={4}>最近聊天</Typography.Title>
          <Button type="text" size="small" icon={<PlusOutlined />} disabled={uploading} onClick={() => void createConversation().catch((error: Error) => messageApi.error(error.message))}>新对话</Button>
        </div>
        <div className="conversation-search"><Input allowClear prefix={<SearchOutlined />} placeholder="搜索最近聊天…" value={search} onChange={event => setSearch(event.target.value)} /></div>
        <List
          dataSource={conversations.filter(item => item.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))}
          locale={{ emptyText: search ? "没有匹配的聊天" : "还没有聊天，点击新对话开始吧" }}
          renderItem={(item) => {
            const blocked = item.is_busy || deleting === item.conversation_id || (item.conversation_id === current && (running || uploading));
            return (
            <List.Item
              key={item.conversation_id}
              className={item.conversation_id === current ? "conversation active" : "conversation"}
            ><button className="conversation-select" onClick={() => selectConversation(item.conversation_id)} aria-current={item.conversation_id === current ? "true" : undefined}>
                <span className="conversation-title" title={item.title}>{item.title}</span>
                <span className="conversation-time" title={item.last_user_message_at ? `最新用户消息：${formatBeijingTime(item.last_user_message_at, true)} UTC+8` : "这个聊天尚未发送消息"}>
                  {item.last_user_message_at ? formatBeijingTime(item.last_user_message_at) : "尚未开始"}
                </span>
              </button>
              <Tooltip title={blocked ? "正在生成、上传或整理，完成后可删除" : "删除聊天"}>
                <span className="conversation-delete"><Button type="text" size="small" danger icon={<CloseOutlined />} aria-label={`删除聊天：${item.title}`} disabled={blocked} loading={deleting === item.conversation_id} onClick={() => deleteConversation(item)} /></span>
              </Tooltip>
            </List.Item>
          ); }}
        />
      </Layout.Sider>
      <Layout.Content className="chat-main">
        <div className="messages">
          {!visibleMessages.length && <Empty description="问我一个校园生活问题吧" />}
          {visibleMessages.map((item) => <MessageBubble key={item.message_id} message={item}
            progress={running && !!pending && item.role === "assistant" && item.request_id === pending.requestId ? progress : undefined} />)}
          {running && !pending && <div className="agent-progress" role="status"><Spin size="small" /> {progress}</div>}
          {pending?.status === "interrupted" && (
            <Space className="agent-progress">
              <Typography.Text type="warning">{progress}</Typography.Text>
              <Button size="small" onClick={() => void connect(pending)}>重新连接</Button>
            </Space>
          )}
          <div ref={endRef} />
        </div>
        <div className="composer">
          <div className="composer-box">
            <Input.TextArea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              autoSize={{ minRows: 2, maxRows: 6 }}
              placeholder="询问求职、校园事务或学习问题…"
              aria-label="聊天消息"
              onCompositionStart={() => { composingRef.current = true; }}
              onCompositionEnd={() => { composingRef.current = false; }}
              onPressEnter={(event) => {
                if (!event.shiftKey && !composingRef.current && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
                  event.preventDefault(); void send();
                }
              }}
            />
            {files.length > 0 && <div className="composer-files">{files.map(file => <Tooltip key={file.file_id} title={file.parse_error || file.filename}>
              <Tag icon={<FileTextOutlined />} color={file.parse_status === "failed" ? "error" : undefined}><span className="composer-file-name">{file.filename}</span> · {file.parse_status === "parsed" ? "已解析" : file.parse_status === "failed" ? "解析失败" : "待解析"}</Tag>
            </Tooltip>)}</div>}
            <div className="composer-toolbar">
            <Upload
              showUploadList={false}
              disabled={!current || running || uploading}
              accept=".pdf,.docx,.pptx,.md,.txt,.xlsx"
              customRequest={async ({ file, onSuccess, onError }) => {
                const conversationId = currentRef.current;
                if (!conversationId || uploadingRef.current) return;
                uploadingRef.current = true;
                setUploading(true);
                const form = new FormData();
                form.append("file", file as File);
                try {
                  await api(`/conversations/${conversationId}/files`, { method: "POST", body: form });
                  messageApi.success("文件已上传并解析");
                  onSuccess?.({});
                } catch (error) {
                  messageApi.error((error as Error).message);
                  onError?.(error as Error);
                } finally {
                  uploadingRef.current = false;
                  setUploading(false);
                  void refreshFiles(conversationId).catch(() => {});
                  void refreshConversations().catch(() => {});
                }
              }}
            >
              <Tooltip title={!current ? "先点击新对话，即可上传资料" : "支持 PDF、Word、PPT、Markdown、TXT 和 XLSX"}>
                <Button type="text" icon={<UploadOutlined />} disabled={!current || running || uploading} loading={uploading}>{uploading ? "上传并解析中…" : "上传资料"}</Button>
              </Tooltip>
            </Upload>
            <span className="composer-shortcut">Enter 发送 · Shift + Enter 换行</span>
            <Button type="primary" icon={<SendOutlined />} loading={running} disabled={!query.trim() || uploading || pending?.status === "interrupted"} onClick={() => void send()}>
              发送
            </Button>
            </div>
          </div>
          <Typography.Text type="secondary" className="composer-hint">
            Agent 可能出错；涉及规章与时效信息请核对引用来源。
          </Typography.Text>
        </div>
      </Layout.Content>
    </Layout>
  );
}
