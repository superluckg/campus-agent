import test from 'node:test';
import assert from 'node:assert/strict';
import { blankCase, blankEvidence, changeGold, validateCase, EVAL_MATCH_FIELDS, decisionReference, editableCase } from '../src/pages/unifiedEvaluation.ts';

test('intent and policy defaults preserve annotations and only change five-field decisions', () => {
  const original = blankCase(); original.query = '转专业条件是什么？'; original.reference_answer = '按条件直接说明';
  original.conversation_context = [{ role: 'user', content: '固定上下文' }];
  original.evidence = [{ ...blankEvidence(), filename: '政策.pdf', content: '原文条件不能丢失', related_intents: ['small_talk'] }];
  const policy = { ...changeGold(original, 'expected_intent', 'campus_affairs'), expected_behavior: 'refuse' };
  assert.equal(policy.expected_behavior, 'refuse'); assert.deepEqual(decisionReference(policy).expected_agents, []);
  assert.equal(policy.evidence[0].content, original.evidence[0].content); assert.equal(policy.query, original.query);
  assert.deepEqual(policy.conversation_context, original.conversation_context); assert.equal(EVAL_MATCH_FIELDS.length, 5);
  assert.equal(decisionReference({ ...policy, expected_behavior: 'answer' }).expected_policy, 'allow');
  assert.equal(changeGold(original, 'expected_intent', 'unclear').expected_behavior, 'clarify');
  assert.equal(changeGold(original, 'expected_intent', 'out_of_scope').expected_behavior, 'explain_limitation');
});
test('compound associations survive changes and invalid old selections require explicit repair', () => {
  let value = changeGold(blankCase(), 'expected_intent', 'compound');
  value = changeGold(value, 'expected_sub_intents', ['campus_affairs', 'learning_plan']);
  value.query = '问题'; value.reference_answer = '回答';
  value.evidence = [{ ...blankEvidence(), filename: '资料.pdf', content: '标准资料原文', related_intents: ['learning_plan'] }];
  assert.deepEqual(validateCase(value), []);
  const next = changeGold(value, 'expected_sub_intents', ['campus_affairs', 'career_recruitment']);
  assert.deepEqual(next.evidence, value.evidence); assert.ok(validateCase(next).some(e => e.includes('重新选择')));
  assert.ok(validateCase(changeGold(value, 'expected_sub_intents', ['campus_affairs', 'campus_network'])).some(e => e.includes('不同助手')));
});
test('empty evidence is accepted; history is capped at eight and blank rows must be completed', () => {
  const value = blankCase(); value.query = '你好'; value.reference_answer = '你好';
  assert.deepEqual(validateCase(value), []);
  value.conversation_context = Array.from({ length: 8 }, () => ({ role: 'user', content: '上下文' }));
  assert.deepEqual(validateCase(value), []);
  value.conversation_context.push({ role: 'assistant', content: '' });
  assert.equal(validateCase(value).length, 2);
});

test('compact imported cases can be edited with default condition fields', () => {
  const value = editableCase({ query: '转专业', expected_intent: 'campus_affairs', expected_behavior: 'answer',
    reference_answer: '申请流程', evidence: [{ filename: '政策.pdf', page_number: 1, content: '政策原文片段' }] } as any);
  assert.deepEqual(validateCase(value), []);
  assert.deepEqual(decisionReference(value).expected_agents, ['CampusAgent']);
  assert.deepEqual(value.conversation_context, []);
  assert.equal(value.evidence[0].source_kind, 'knowledge_file');
  const next = { ...value, expected_behavior: 'refuse' };
  assert.equal(decisionReference(next).expected_policy, 'refuse');
  assert.equal(decisionReference(next).expected_route, 'direct_response');
});
