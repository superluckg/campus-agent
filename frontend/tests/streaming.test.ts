import assert from "node:assert/strict";
import test from "node:test";
import { consumeEventStream, SSEParser, StreamHttpError } from "../src/api/sse.ts";
import type { StreamEvent } from "../src/api/sse.ts";
import {
  applyStreamEvent, createPendingResponse, loadPendingResponse, mergeMessages,
  savePendingResponse,
} from "../src/pages/chatStream.ts";

function frame(id: number, type: string, data: Record<string, unknown>): string {
  return `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

function response(text: string, split = 1): Response {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({
    start(controller) {
      for (let offset = 0; offset < bytes.length; offset += split) {
        controller.enqueue(bytes.slice(offset, offset + split));
      }
      controller.close();
    },
  }), { headers: { "Content-Type": "text/event-stream" } });
}

test("SSE parses Chinese split across bytes, CRLF, comments and multiple data lines", () => {
  const events: StreamEvent[] = [];
  const parser = new SSEParser((event) => events.push(event));
  const decoder = new TextDecoder();
  const text = ': heartbeat\r\n\r\nid: 8\r\nevent: response.delta\r\ndata: {"delta":\r\ndata: "中文😀"}\r\n\r\n'
    + frame(9, "response.completed", { content: "中文😀", message_id: "saved" });
  for (const byte of new TextEncoder().encode(text)) {
    parser.push(decoder.decode(new Uint8Array([byte]), { stream: true }));
  }
  parser.push(decoder.decode());
  assert.equal(events.length, 2);
  assert.equal(events[0].id, 8);
  assert.equal(events[0].data.delta, "中文😀");
  assert.equal(events[1].type, "response.completed");
});

test("incomplete trailing frame is not treated as a completed event", () => {
  const events: StreamEvent[] = [];
  new SSEParser((event) => events.push(event)).push('id: 4\nevent: response.completed\ndata: {"content":');
  assert.deepEqual(events, []);
});

test("reconnection sends latest cursor, deduplicates deltas and never posts again", async () => {
  const original = globalThis.fetch;
  const urls: URL[] = [];
  const events: StreamEvent[] = [];
  let attempts = 0;
  globalThis.fetch = async (input, options) => {
    const url = new URL(String(input));
    urls.push(url);
    assert.equal(options?.method, undefined);
    attempts += 1;
    return response(attempts === 1
      ? frame(1, "response.started", {}) + frame(2, "response.delta", { delta: "你" })
      : frame(2, "response.delta", { delta: "你" }) + frame(3, "response.delta", { delta: "好" })
        + frame(4, "response.completed", { content: "你好", message_id: "saved" }));
  };
  try {
    await consumeEventStream("http://localhost/api/requests/same/events", {}, (event) => events.push(event), {
      retryDelays: [0],
    });
    assert.deepEqual(urls.map((url) => url.searchParams.get("after")), ["0", "2"]);
    assert.equal(new Set(urls.map((url) => url.pathname)).size, 1);
    assert.deepEqual(events.map((event) => event.id), [1, 2, 3, 4]);
  } finally { globalThis.fetch = original; }
});

test("retries are bounded and missing terminal is a connection failure", async () => {
  const original = globalThis.fetch;
  let attempts = 0;
  globalThis.fetch = async () => { attempts += 1; return response(": heartbeat\n\n"); };
  try {
    await assert.rejects(consumeEventStream("http://localhost/events", {}, () => {}, {
      retryDelays: [0, 0, 0],
    }), /提前断开/);
    assert.equal(attempts, 4);
  } finally { globalThis.fetch = original; }
});

test("expired streams and authentication failures do not retry", async () => {
  const original = globalThis.fetch;
  for (const status of [410, 401]) {
    let attempts = 0;
    globalThis.fetch = async () => { attempts += 1; return new Response("", { status }); };
    try {
      await assert.rejects(consumeEventStream("http://localhost/events", {}, () => {}, {
        retryDelays: [0, 0, 0],
      }), (error: unknown) => error instanceof StreamHttpError && error.status === status);
      assert.equal(attempts, 1);
    } finally { globalThis.fetch = original; }
  }
});

test("switching away aborts a retry and does not open another connection", async () => {
  const original = globalThis.fetch;
  const controller = new AbortController();
  let attempts = 0;
  globalThis.fetch = async () => { attempts += 1; return response(""); };
  try {
    await assert.rejects(consumeEventStream("http://localhost/events", {}, () => {}, {
      signal: controller.signal, retryDelays: [1000], onRetry: () => controller.abort(),
    }), (error: unknown) => error instanceof Error && error.name === "AbortError");
    assert.equal(attempts, 1);
  } finally { globalThis.fetch = original; }
});

test("draft and cursor restore together; completion replaces preview and citations", () => {
  const store = new Map<string, string>();
  const storage = {
    setItem: (key: string, value: string) => { store.set(key, value); },
    getItem: (key: string) => store.get(key) ?? null,
  };
  let pending = createPendingResponse("conversation", "request", "问题");
  pending = applyStreamEvent(pending, { id: 2, type: "response.delta", data: { delta: "草稿" } });
  savePendingResponse(storage, "student", pending);
  const restored = loadPendingResponse(storage, "student", "conversation");
  assert.equal(restored?.cursor, 2);
  assert.equal(restored?.draft.content, "草稿");
  assert.equal(loadPendingResponse(storage, "another-user", "conversation"), undefined);
  assert.strictEqual(applyStreamEvent(pending, { id: 2, type: "response.delta", data: { delta: "重复" } }), pending);
  pending = applyStreamEvent(pending, {
    id: 3, type: "response.replace", data: { response_id: "request", content: "校验后的正文" },
  });
  pending = applyStreamEvent(pending, {
    id: 4, type: "response.completed",
    data: {
      response_id: "request", message_id: "database-id", content: "最终正文",
      citations: [{ citation_index: 1, title: "正式资料" }], status: "partial", partial: true,
    },
  });
  assert.equal(pending.draft.message_id, "database-id");
  assert.equal(pending.draft.content, "最终正文");
  assert.equal(pending.draft.status, "partial");
  assert.equal(pending.draft.citations[0].title, "正式资料");
  assert.equal(mergeMessages([restored!.draft], [pending.draft]).length, 1);
});

test("failure retains preview and mismatched response cannot corrupt the draft", () => {
  let pending = createPendingResponse("conversation", "request", "问题");
  pending = applyStreamEvent(pending, { id: 1, type: "response.delta", data: { delta: "部分正文" } });
  assert.throws(() => applyStreamEvent(pending, {
    id: 2, type: "response.delta", data: { response_id: "another-request", delta: "其他会话" },
  }), /不一致/);
  pending = applyStreamEvent(pending, {
    id: 3, type: "request.failed", data: { message: "回答未能完成。" },
  });
  assert.equal(pending.draft.content, "部分正文");
  assert.equal(pending.draft.display_metadata.incomplete, true);
  assert.equal(pending.status, "failed");
});


test("message order follows actual time across database and browser timezone formats", () => {
  const pending = createPendingResponse("conversation", "request", "问题");
  const user = {
    ...pending.draft, message_id: "user", role: "user" as const, content: "问题",
    created_at: "2026-10-10T16:00:00+08:00",
  };
  const assistant = {
    ...pending.draft, message_id: "assistant", content: "答案",
    created_at: "2026-10-10T08:00:01.000Z",
  };
  assert.deepEqual(mergeMessages([user], [assistant]).map(item => item.role), ["user", "assistant"]);
});
