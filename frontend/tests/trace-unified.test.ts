import test from 'node:test';
import assert from 'node:assert/strict';
import { chainRows, chineseFields, cumulativeDuration, seconds, spanDuration, spanRows, timingSeconds } from '../src/pages/traceDetails.ts';
import type { Span } from '../src/pages/traceDetails.ts';
import { groupResults } from '../src/pages/unifiedEvaluation.ts';
import type { UnifiedResult } from '../src/pages/unifiedEvaluation.ts';
import { formatBeijingTime } from '../src/utils/time.ts';

test('timing seconds preserve short durations and other tabs retain their existing formats', () => {
  assert.equal(timingSeconds(1234.567), '1.235 秒');
  assert.equal(timingSeconds(1000), '1.000 秒');
  assert.equal(timingSeconds(1), '0.001 秒');
  assert.equal(timingSeconds(.999), '<0.001 秒');
  assert.equal(timingSeconds(.0001), '<0.001 秒');
  assert.equal(timingSeconds(0), '0.000 秒');
  for (const value of [undefined, null, NaN, Infinity, -Infinity, -1, '1000']) {
    assert.equal(timingSeconds(value), '未记录');
  }
  assert.equal(seconds(1234.567), '1.23 秒');
  assert.equal(spanDuration(.123), '0.12 毫秒');
});

test('parallel and nested steps use their own finish offset and exclude background rows', () => {
  const spans = [
    timedSpan('parent', 'execute_agents', 1200, 5000),
    timedSpan('parallel', 'compose', 2000, 4000),
    timedSpan('child', 'tool', 1250, 1400, { parent_span_id: 'parent' }),
    timedSpan('memory', 'memory.maintenance', 5000, 6000, { background: true }),
  ];
  const before = JSON.stringify(spans);
  const rows = spanRows(spans);
  assert.deepEqual(rows.map(s => s.span_id), ['parent', 'parallel']);
  assert.equal(cumulativeDuration(rows[0], 1000), '4.000 秒');
  assert.equal(cumulativeDuration(rows[1], 1000), '3.000 秒');
  assert.equal(cumulativeDuration(rows[0].children![0], 1000), '0.400 秒');
  assert.equal(JSON.stringify(spans), before);
});

test('completion offsets retain failed and cancelled endpoints without inventing missing times', () => {
  for (const status of ['success', 'failed', 'cancelled', 'skipped']) {
    assert.equal(cumulativeDuration(timedSpan(status, 'tool', 3000, 5000, { status }), 0), '5.000 秒');
  }
  assert.equal(cumulativeDuration(timedSpan('running', 'tool', 3000), 0), '执行中');
  const historical = timedSpan('old', 'tool', 3000, undefined, { status: 'success', duration_ms: 2000 });
  assert.equal(cumulativeDuration(historical, 0), '未记录');
  const finished = timedSpan('finished', 'tool', 3000, 5000);
  assert.equal(cumulativeDuration({ ...finished, finished_at: 'invalid' }, 0), '未记录');
  assert.equal(cumulativeDuration(finished, NaN), '未记录');
  assert.equal(cumulativeDuration(finished, Infinity), '未记录');
  assert.equal(cumulativeDuration(finished, 6000), '未记录');
  assert.equal(cumulativeDuration(timedSpan('instant', 'tool', 0, 0), 0), '0.000 秒');
});
test('direct route displays skipped branches and missing history remains unrecorded', () => {
  assert.ok(chainRows([], 'direct_response', 'success').every(s => s.status === 'unrecorded'));
  const stages = [{ span_id: 'context', node: 'load_context', label: 'context', status: 'success', started_at: '' }] as Span[];
  const rows = chainRows(stages, 'direct_response', 'success');
  assert.equal(rows.find(s => s.node === 'execute_agents')!.status, 'skipped');
  assert.equal(rows.find(s => s.node === 'direct_response')!.status, 'unrecorded');
});
test('deleted case number holes do not alter grouping and repeats sort within Case', () => {
  const row = (id: string, number: number, trial: number) => ({ case: { case_id: id, case_number: number }, trial_index: trial }) as UnifiedResult;
  const groups = groupResults([row('third', 3, 2), row('first', 1, 1), row('third', 3, 1)]);
  assert.deepEqual(groups.map(g => g.case.case_number), [1, 3]);
  assert.deepEqual(groups[1].rows.map(r => r.trial_index), [1, 2]);
  assert.equal(formatBeijingTime('2026-10-10T08:07:00Z', false, '  '), '2026/10/10  16:07');
});

function timedSpan(id: string, node: string, start: number, end?: number, extra = {}): Span {
  return { span_id: id, node, label: node, status: end == null ? 'running' : 'success',
    started_at: new Date(start).toISOString(),
    finished_at: end == null ? undefined : new Date(end).toISOString(),
    duration_ms: end == null ? undefined : end - start, ...extra };
}


test('retrieval tree preserves actual batches and inherited query context', () => {
  const spans = [
    timedSpan('agent', 'agent', 0, 100, { agent: 'CampusAgent' }),
    timedSpan('tool1', 'tool', 1, 60, { parent_span_id: 'agent', tool: 'agentic_retrieval' }),
    timedSpan('retrieve1', 'retrieval.retrieve', 11, 30, { parent_span_id: 'tool1', round_number: 1 }),
    timedSpan('local', 'retrieval.local', 12, 29, { parent_span_id: 'retrieve1' }),
    timedSpan('dense', 'retrieval.dense', 13, 28, { parent_span_id: 'local', query_index: 2 }),
    timedSpan('embed', 'retrieval.embedding', 14, 25, { parent_span_id: 'dense' }),
    timedSpan('tool2', 'tool', 61, 99, { parent_span_id: 'agent', tool: 'agentic_retrieval' }),
    timedSpan('retrieve2', 'retrieval.retrieve', 62, 98, { parent_span_id: 'tool2', round_number: 2 }),
  ];
  const before = JSON.stringify(spans);
  const tools = spanRows(spans, true);
  assert.deepEqual(tools.map(s => s.label), ['tool 第 1 次调用', 'tool 第 2 次调用']);
  assert.deepEqual(tools[0].children!.map(s => s.label), ['第 1 批实际检索']);
  assert.equal(tools[1].children![0].label, '第 2 批实际检索');
  const batch = tools[0].children![0];
  assert.equal(batch.duration_ms, 19);
  assert.equal(cumulativeDuration(batch, 0), '0.030 秒');
  const embedding = batch.children![0].children![0].children![0];
  assert.equal(embedding.node, 'retrieval.embedding');
  assert.equal(embedding.agent, 'CampusAgent');
  assert.equal(embedding.round_number, 1);
  assert.equal(embedding.query_index, 2);
  assert.equal(JSON.stringify(spans), before);
  assert.equal(spanRows(spans)[0].children![0].span_id, 'tool1');
});
test('cancelled and running batches preserve actual status and time', () => {
  const rows = spanRows([
    timedSpan('tool', 'tool', 0, 50, { status: 'cancelled' }),
    timedSpan('retrieve', 'retrieval.retrieve', 11, 49,
      { parent_span_id: 'tool', round_number: 1, status: 'cancelled' }),
    timedSpan('other', 'tool', 60),
    timedSpan('pending', 'retrieval.retrieve', 61, undefined, { parent_span_id: 'other', round_number: 1 }),
  ], true);
  assert.equal(rows[0].children![0].status, 'cancelled');
  assert.equal(cumulativeDuration(rows[0].children![0], 0), '0.049 秒');
  assert.equal(rows[1].children![0].status, 'running');
  assert.equal(cumulativeDuration(rows[1].children![0], 0), '执行中');
  assert.equal(rows[1].children![0].duration_ms, undefined);
  assert.equal(rows[1].children![0].finished_at, undefined);
});
test('cache-only calls retain skipped status without adding batches', () => {
  const rows = spanRows([
    timedSpan('tool', 'tool', 0, 100),
    timedSpan('cached', 'retrieval.retrieve', 26, 30,
      { parent_span_id: 'tool', round_number: 1, status: 'skipped' }),
  ], true);
  assert.equal(rows[0].children![0].label, '复用成功查询缓存');
  assert.equal(rows[0].children![0].status, 'skipped');
  assert.equal(rows[0].children![0].synthetic, undefined);
  assert.deepEqual(chineseFields({ total_rounds: 1, cache_hit: true }), {
    '累计实际检索批次（total_rounds）': 1, '命中缓存（cache_hit）': true,
  });
});
