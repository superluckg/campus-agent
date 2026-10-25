import assert from "node:assert/strict";
import test from "node:test";
import { beijingExport, formatBeijingTime } from "../src/utils/time.ts";

test("Beijing display is fixed across midnight and explicit timezone offsets", () => {
  assert.equal(formatBeijingTime("2026-10-10T16:03:04Z", true), "2026/10/11 · 00:03:04");
  assert.equal(formatBeijingTime("2026-10-10T12:03:04-04:00"), "2026/10/11 · 00:03");
  assert.equal(formatBeijingTime("2026-10-11T00:03:04+08:00"), "2026/10/11 · 00:03");
  assert.equal(formatBeijingTime("2026-10-10 16:03:04"), "2026/10/11 · 00:03");
  assert.equal(formatBeijingTime(null), "—");
  assert.equal(formatBeijingTime("invalid"), "—");
});

test("exports convert nested timestamp fields without mutating saved data or date-only values", () => {
  const original = { started_at: "2026-10-10T16:03:04Z", rows: [
    { fetched_at: "2026-10-10T12:03:04-04:00", created_at: "2026-10-10", content: "2026-10-10T16:03:04Z" },
  ], finished_at: null };
  const converted = beijingExport(original);
  assert.equal(converted.started_at, "2026-10-11T00:03:04.000+08:00");
  assert.equal(converted.rows[0].fetched_at, converted.started_at);
  assert.equal(converted.rows[0].created_at, "2026-10-10");
  assert.equal(converted.rows[0].content, original.rows[0].content);
  assert.equal(converted.finished_at, null);
  assert.equal(original.started_at, "2026-10-10T16:03:04Z");
  assert.notEqual(converted.rows, original.rows);
});
