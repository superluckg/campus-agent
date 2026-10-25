import assert from "node:assert/strict";
import test from "node:test";

import { api, streamRequest } from "../src/api/client.ts";

test("workspace requests preserve payloads and cancellation without credentials", async (t) => {
  const controller = new AbortController();
  const requests: Array<{ url: string; options: RequestInit }> = [];
  t.mock.method(globalThis, "fetch", async (input: string | URL, options: RequestInit) => {
    requests.push({ url: String(input), options });
    return Response.json({ conversation_id: "conversation" });
  });
  const value = await api<{ conversation_id: string }>("/conversations", {
    method: "POST", body: JSON.stringify({ title: "新对话" }), signal: controller.signal,
  });
  assert.equal(value.conversation_id, "conversation");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/conversations");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.signal, controller.signal);
  assert.deepEqual(JSON.parse(requests[0].options.body as string), { title: "新对话" });
  const headers = new Headers(requests[0].options.headers);
  assert.equal(headers.get("Content-Type"), "application/json");
  assert.equal(headers.has("Authorization"), false);
});

test("workspace streams resume their cursor without a separate sign-in request", async (t) => {
  const requests: Array<{ url: URL; options: RequestInit }> = [];
  t.mock.method(globalThis, "fetch", async (input: string | URL, options: RequestInit) => {
    requests.push({ url: new URL(input), options });
    return new Response('id: 8\nevent: response.completed\ndata: {"content":"回答"}\n\n');
  });
  const events: string[] = [];
  await streamRequest("/conversations/conversation/requests/request/events", event => {
    events.push(event.type);
  }, { after: 7, retryDelays: [] });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url.pathname, "/api/conversations/conversation/requests/request/events");
  assert.equal(requests[0].url.searchParams.get("after"), "7");
  assert.equal(new Headers(requests[0].options.headers).has("Authorization"), false);
  assert.deepEqual(events, ["response.completed"]);
});
