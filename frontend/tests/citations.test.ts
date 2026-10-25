import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";

import { citationSuperscripts } from "../src/utils/citationSuperscript.ts";
import { applyStreamEvent, createPendingResponse, loadPendingResponse, savePendingResponse } from "../src/pages/chatStream.ts";
import { spanRows, type Span } from "../src/pages/traceDetails.ts";

test("registered references superscript brackets and digits; code, links and other brackets stay unchanged", () => {
  const html = renderToStaticMarkup(createElement(ReactMarkdown, {
    rehypePlugins: [[citationSuperscripts, { indexes: [1, 2] }]],
    children: "正文[1][2] [99] [普通] `[1]` [1](https://example.org) \\[1]\n\n```text\n[2]\n```",
  }));
  assert.equal((html.match(/<sup /g) ?? []).length, 2);
  assert.match(html, /<sup class="citation-reference">\[1\]<\/sup>/);
  assert.match(html, /<code>\[1\]<\/code>/);
  assert.match(html, /<a href="https:\/\/example.org">1<\/a>/);
  assert.match(html, /\[99\] \[普通\]/);
  const plain = renderToStaticMarkup(createElement(ReactMarkdown, { children: "普通括号[1]" }));
  assert.doesNotMatch(plain, /<sup/);
});

test("stream metadata and citations survive completion, reconnection snapshot and reload", () => {
  let pending = createPendingResponse("conversation", "request", "问题");
  const citations = [{ citation_index: 1, title: "手册", page_numbers: [2, 3] }];
  const metadata = { render_citations: true, citation_warning: "引用需核验" };
  pending = applyStreamEvent(pending, { id: 1, type: "response.delta", data: {
    response_id: "request", delta: "事实[1]", citations, display_metadata: metadata,
  } });
  assert.deepEqual(pending.draft.citations, citations);
  assert.equal(pending.draft.display_metadata.render_citations, true);
  const values = new Map<string, string>();
  const storage = { setItem: (key: string, value: string) => { values.set(key, value); }, getItem: (key: string) => values.get(key) ?? null };
  savePendingResponse(storage, "user", pending);
  const recovered = loadPendingResponse(storage, "user", "conversation")!;
  assert.deepEqual(recovered.draft, pending.draft);
  pending = applyStreamEvent(recovered, { id: 2, type: "response.completed", data: {
    response_id: "request", content: "事实[1]", message_id: "saved", status: "partial",
    citations, display_metadata: metadata, partial: true,
  } });
  assert.equal(pending.draft.display_metadata.citation_warning, "引用需核验");
  assert.equal(pending.draft.display_metadata.generating, false);
  assert.deepEqual(pending.draft.citations, citations);
});

test("retrieval shows actual batches and cache calls without synthetic planning groups", () => {
  const time = "2026-10-21T00:00:00Z";
  const spans = [
    { span_id: "tool", node: "tool", tool: "agentic_retrieval", label: "检索工具", status: "success", started_at: time },
    { span_id: "batch", parent_span_id: "tool", node: "retrieval.retrieve", label: "执行", protocol: "agentic", round_number: 1, status: "success", started_at: time },
    { span_id: "tool2", node: "tool", tool: "agentic_retrieval", label: "检索工具", status: "success", started_at: time },
    { span_id: "cached", parent_span_id: "tool2", node: "retrieval.retrieve", label: "执行", protocol: "agentic", round_number: 1, status: "skipped", started_at: time },
  ] as Span[];
  const rows = spanRows(spans, true);
  assert.equal(rows[0].children?.[0].label, "第 1 批实际检索");
  assert.equal(rows[1].children?.[0].label, "复用成功查询缓存");
  assert.ok(rows.every(r => r.children?.every(c => !c.synthetic)));
});
