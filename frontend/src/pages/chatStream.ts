import type { StreamEvent } from "../api/sse.ts";
import type { Citation, Message } from "../types.ts";

export interface PendingResponse {
  requestId: string;
  conversationId: string;
  cursor: number;
  draft: Message;
  query: string;
  progress: string;
  status: "streaming" | "interrupted" | "failed";
}

export function createPendingResponse(
  conversationId: string, requestId: string, query: string,
): PendingResponse {
  return {
    requestId, conversationId, query, cursor: 0,
    status: "streaming", progress: "正在分析问题…",
    draft: {
      message_id: `stream:${requestId}`, request_id: requestId,
      role: "assistant", content: "", status: "running", agent_names: [],
      display_metadata: { generating: true }, citations: [],
      created_at: new Date().toISOString(),
    },
  };
}

export function applyStreamEvent(pending: PendingResponse, event: StreamEvent): PendingResponse {
  if (event.id > 0 && event.id <= pending.cursor) return pending;
  if (typeof event.data.response_id === "string" && event.data.response_id !== pending.requestId) {
    throw new Error("流式事件与当前请求不一致。");
  }
  const next: PendingResponse = {
    ...pending, cursor: Math.max(pending.cursor, event.id),
    draft: { ...pending.draft, display_metadata: { ...pending.draft.display_metadata } },
  };
  if (typeof event.data.message === "string") next.progress = event.data.message;
  if (event.data.display_metadata && typeof event.data.display_metadata === "object") {
    Object.assign(next.draft.display_metadata, event.data.display_metadata);
  }
  if (Array.isArray(event.data.citations)) next.draft.citations = event.data.citations as Citation[];
  if (event.type === "route.completed" && Array.isArray(event.data.assignments)) {
    next.draft.agent_names = event.data.assignments.flatMap((item: unknown) => {
      if (item && typeof item === "object" && "agent_name" in item && typeof item.agent_name === "string") {
        return [item.agent_name];
      }
      return [];
    });
  }
  if (event.type === "response.delta" && typeof event.data.delta === "string") {
    next.draft.content += event.data.delta;
  } else if (event.type === "response.replace" && typeof event.data.content === "string") {
    next.draft.content = event.data.content;
  } else if (event.type === "response.completed") {
    if (typeof event.data.content !== "string" || typeof event.data.message_id !== "string") {
      throw new Error("最终回答的数据格式无效。");
    }
    next.draft = {
      ...next.draft, content: event.data.content, message_id: event.data.message_id,
      status: typeof event.data.status === "string" ? event.data.status : "success",
      agent_names: Array.isArray(event.data.agents) ? event.data.agents as string[] : [],
      citations: Array.isArray(event.data.citations) ? event.data.citations as Citation[] : [],
      display_metadata: {
        ...(event.data.display_metadata && typeof event.data.display_metadata === "object"
          ? event.data.display_metadata : {}),
        partial: Boolean(event.data.partial),
        unresolved_points: event.data.unresolved_points ?? [], generating: false,
      },
    };
  } else if (event.type === "request.failed") {
    next.status = "failed";
    next.draft.status = "failed";
    next.draft.display_metadata = {
      ...next.draft.display_metadata,
      generating: false, incomplete: true,
      error_message: next.progress || "本次回答未能完成。",
    };
  }
  return next;
}

export function pendingStorageKey(storageScope: string, conversationId: string): string {
  return `qust-stream:${encodeURIComponent(storageScope)}:${conversationId}`;
}

export function savePendingResponse(
  storage: Pick<Storage, "setItem">, storageScope: string, pending: PendingResponse,
): void {
  try {
    // 草稿与游标作为一个快照写入，避免恢复后丢字或重字。
    storage.setItem(pendingStorageKey(storageScope, pending.conversationId), JSON.stringify(pending));
  } catch {
    // 存储额度不足不影响当前连接。
  }
}

export function loadPendingResponse(
  storage: Pick<Storage, "getItem">, storageScope: string, conversationId: string,
): PendingResponse | undefined {
  try {
    const raw = storage.getItem(pendingStorageKey(storageScope, conversationId));
    if (!raw) return undefined;
    const value = JSON.parse(raw) as PendingResponse;
    if (
      value.conversationId !== conversationId || !value.requestId || !value.draft
      || value.draft.request_id !== value.requestId || typeof value.draft.content !== "string"
      || !Number.isSafeInteger(value.cursor) || value.cursor < 0
      || !["streaming", "interrupted", "failed"].includes(value.status)
    ) return undefined;
    return value;
  } catch {
    return undefined;
  }
}

export function mergeMessages(previous: Message[], incoming: Message[]): Message[] {
  const values = new Map<string, Message>();
  for (const item of [...previous, ...incoming]) {
    const key = item.request_id ? `${item.request_id}:${item.role}` : item.message_id;
    values.set(key, item);
  }
  return [...values.values()].sort((a, b) => {
    const first = Date.parse(a.created_at), second = Date.parse(b.created_at);
    return Number.isFinite(first) && Number.isFinite(second) ? first - second : 0;
  });
}
