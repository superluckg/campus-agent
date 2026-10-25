import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from pydantic import ValidationError

from app.api.dependencies import workspace_user, get_container
from app.api.routes.unified_evaluation import router
from app.evaluation.sources import preview_sections
from app.evaluation.unified_scoring import grade_intent, parse_import, prepare_case, retrieval_scores, summarize
from app.evaluation.unified_service import FixedInputs, UnifiedJudge
from app.schemas.unified_evaluation import UnifiedCase, decision_reference
from tests.test_unified_evaluation import case_payload, corpus, view


@pytest.mark.parametrize('policy', ['refuse_sensitive', 'refuse_unauthorized'])
def test_refusal_reasons_share_eval_policy_but_raw_prediction_remains(policy):
    gold = decision_reference({**case_payload('campus_affairs'), 'expected_behavior': 'refuse'})
    prediction = dict(primary_intent='campus_affairs', policy_decision=policy, route='direct_response',
                      agents=[], sub_intents=[], missing_fields=['file_id'], selected_file_refs=['bad'])
    metrics = grade_intent(gold, prediction)
    assert metrics['policy_accuracy'] == metrics['decision_exact_match'] == 1
    assert len(metrics) == 6 and prediction['policy_decision'] == policy


def test_repeat_decision_uses_only_five_fields_and_normalized_policy():
    c = view(case_payload('campus_affairs'))
    c['payload']['expected_behavior'] = 'refuse'
    rows = []
    for policy, missing in [('refuse_sensitive', []), ('refuse_unauthorized', ['file_id'])]:
        p = dict(primary_intent='campus_affairs', policy_decision=policy, route='direct_response', agents=[], sub_intents=[], missing_fields=missing)
        rows.append(dict(case=c, prediction=p, intent_metrics=grade_intent(decision_reference(c['payload']), p)))
    result = summarize(rows, 2)
    assert result['intent']['decision_agreement_rate'] == result['intent']['policy_accuracy'] == 1
    assert 'point_coverage' not in result['retrieval']


def test_retired_fields_rejected_and_errors_localized():
    for field in ('inputs', 'reference_outputs', 'generation', 'annotation_notes', 'updated_at', 'tasks', 'expected_route', 'expected_agents', 'expected_policy'):
        payload = case_payload()
        payload[field] = {}
        values, errors = parse_import(json.dumps(payload))
        assert not values and errors[0]['line'] == 1
        assert field not in errors[0]['message'] and '当前标注模板' in errors[0]['message']


def test_eight_history_messages_and_empty_gold_are_valid():
    payload = case_payload('campus_affairs')
    payload['conversation_context'] = [{'role': 'user', 'content': str(i)} for i in range(8)]
    assert len(UnifiedCase.model_validate(payload).conversation_context) == 8
    prepared = prepare_case(view(payload), corpus())
    call = dict(tool='agentic_retrieval', call_id='call', agent='CampusAgent', inputs={'query': '政策'},
                duration_ms=1, artifact={'documents': []})
    result = retrieval_scores(prepared, [call], {})
    assert result['status'] == 'not_applicable' and all(v is None for v in result['metrics'].values())
    assert result['calls'][0]['reason']
    payload['conversation_context'].append({'role': 'assistant', 'content': '九条'})
    with pytest.raises(ValidationError):
        UnifiedCase.model_validate(payload)


def compound_case():
    payload = case_payload()
    payload.update(expected_intent='compound', expected_behavior='answer', expected_sub_intents=['campus_affairs', 'learning_plan'])
    payload['evidence'] = [dict(source_kind='knowledge_file', filename='政策.pdf', content='转专业需要满足学校公布的条件', related_intents=['campus_affairs']),
                           dict(source_kind='knowledge_file', filename='学习.pdf', content='学习规划先设置目标再分解任务', related_intents=['learning_plan'])]
    return payload


def test_compound_each_call_has_its_own_gold_and_five_limit():
    payload = compound_case()
    docs = [Document(id=str(i), page_content=e['content'], metadata={'chunk_id': str(i), 'document_id': str(i), 'filename': e['filename'], 'chunk_index': 0}) for i, e in enumerate(payload['evidence'])]
    prepared = prepare_case(view(payload), corpus(docs))
    def call(agent, selected):
        return dict(tool='agentic_retrieval', call_id=agent, agent=agent, inputs={'query': '测试'}, duration_ms=1, artifact={'documents': selected})
    result = retrieval_scores(prepared, [call('CampusAgent', [docs[0]]), call('LearningAgent', [docs[0]])], {})
    assert [c['metrics']['hit'] for c in result['calls']] == [1, 0]
    assert result['metrics']['hit'] == .5
    result = retrieval_scores(prepared, [call('CampusAgent', [docs[1]] * 5 + [docs[0]])], {})
    assert len(result['calls'][0]['documents']) == 5 and result['metrics']['hit'] == 0
    payload['evidence'][0]['related_intents'] = ['career_recruitment']
    with pytest.raises(ValidationError):
        UnifiedCase.model_validate(payload)


def test_failed_retrieval_is_not_a_zero_scored_call():
    payload = compound_case()
    docs = [Document(id=str(i), page_content=e['content'], metadata={'chunk_id': str(i), 'document_id': str(i), 'filename': e['filename'], 'chunk_index': 0}) for i, e in enumerate(payload['evidence'])]
    prepared = prepare_case(view(payload), corpus(docs))
    result = retrieval_scores(prepared, [dict(tool='agentic_retrieval', call_id='bad', agent='CampusAgent', inputs={}, duration_ms=1,
        status='execution_failed', error_message='超时', artifact={'documents': docs})], {})
    assert result['status'] == 'execution_failed' and all(v is None for v in result['metrics'].values())
    assert not any(e['obtained'] for e in result['evidence_details'])


@pytest.mark.asyncio
async def test_fixed_inputs_ignore_summary_and_memory_even_if_stale_values_exist():
    fixture = FixedInputs({'query': '本次问题', 'conversation_context': [{'role': 'assistant', 'content': '上下文'}],
                           'conversation_summary': '秘密摘要', 'injected_memories': ['秘密记忆']})
    assert await fixture.load_summary('c') == '' and await fixture.recall('u', 'q', [], 5) == []
    assert [d.page_content for d in await fixture.retrieve('上下文', 'c', 5)] == ['上下文']
    assert (await fixture.load_recent('u', 'c'))[-1]['content'] == '本次问题'


def test_preview_overlap_preserves_page_boundaries_and_nonadjacent_gaps():
    docs = [Document(page_content='前文重复片段', metadata={'chunk_index': 0, 'page_number': 1}),
            Document(page_content='重复片段后文', metadata={'chunk_index': 1, 'page_number': 1}),
            Document(page_content='隔开内容', metadata={'chunk_index': 3, 'page_number': 1}),
            Document(page_content='下一页', metadata={'chunk_index': 4, 'page_number': 2})]
    sections = preview_sections(docs)
    assert sections[0]['content'] == '前文重复片段后文\n\n隔开内容'
    assert sections[1]['page_number'] == 2 and sections[1]['content'] == '下一页'


def test_sources_workspace_boundary_and_no_arbitrary_paths_or_urls():
    from fastapi import HTTPException
    identifier = str(uuid4())
    documents = [Document(page_content='只读原文', metadata={'document_id': identifier, 'filename': '政策.pdf'})]
    knowledge = SimpleNamespace(load_evaluation_chunks=AsyncMock(return_value=documents))
    cache = SimpleNamespace(latest_success=AsyncMock(return_value=[]))
    container = SimpleNamespace(knowledge=knowledge, web_cache=cache, web_registry=SimpleNamespace(keys=('official',)))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_container] = lambda: container
    def deny():
        raise HTTPException(403, '仅接受本机请求')
    app.dependency_overrides[workspace_user] = deny
    with TestClient(app) as client:
        for path in ['/sources', f'/sources/local/{identifier}', '/sources/web/official']:
            assert client.get('/developer/evaluation' + path).status_code == 403
        knowledge.load_evaluation_chunks.assert_not_awaited()
        app.dependency_overrides[workspace_user] = lambda: SimpleNamespace(user_id=identifier)
        assert client.get(f'/developer/evaluation/sources/local/{identifier}').json()['sections'][0]['content'] == '只读原文'
        assert client.get('/developer/evaluation/sources/local/not-a-uuid').status_code == 422
        assert client.get('/developer/evaluation/sources/web/unregistered').status_code == 404
        cache.latest_success.assert_not_awaited()
        bad = copy.deepcopy(case_payload())
        bad.pop('expected_intent')
        response = client.post('/developer/evaluation/cases', json=bad)
        assert response.status_code == 422 and '主意图' in response.json()['detail']
        assert 'expected_intent' not in response.json()['detail']


@pytest.mark.asyncio
async def test_judge_separates_gold_original_evidence_from_actual_visible_context():
    model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kwargs: dict(
        correctness=4, faithfulness=4, relevance=4, completeness=4, helpfulness=4, refusal_quality=None, reasons={}))))
    judge = UnifiedJudge(SimpleNamespace(with_structured_output=lambda *args, **kwargs: model))
    case = compound_case()
    basis = [{'content': '实际检索返回'}]
    await judge.evaluate(case, '候选回答', basis)
    text = model.ainvoke.await_args.args[0]
    payload = json.loads(text[text.index('{'):])
    assert payload['gold_original_evidence'] == case['evidence']
    assert payload['actual_visible_content'] == basis and 'tasks' not in payload


def test_delivery_jsonl_roundtrip_and_derived_decisions():
    path = Path(__file__).parents[2] / 'data/evaluation/cases.jsonl'
    cases, errors = parse_import(path.read_text(encoding='utf-8'))
    assert not errors and len(cases) == 5
    payloads = [case.case_payload() for case in cases]
    assert all(set(case) == {'query', 'expected_intent', 'expected_behavior', 'reference_answer', 'evidence'} for case in payloads)
    assert all(set(item) == {'filename', 'page_number', 'content'} for case in payloads for item in case['evidence'])
    assert all(UnifiedCase.model_validate(case).case_payload() == case for case in payloads)
    gold = [decision_reference(case) for case in payloads]
    assert all(item['expected_route'] == 'single_agent' and item['expected_agents'] == ['CampusAgent'] for item in gold[:3])
    assert gold[3]['expected_policy'] == 'refuse' and gold[4]['expected_policy'] == 'allow'
    assert all(item['expected_route'] == 'direct_response' and not item['expected_agents'] for item in gold[3:])


def test_optional_context_and_web_evidence_survive_compact_roundtrip():
    payload = case_payload('campus_affairs')
    payload['conversation_context'] = [{'role': 'user', 'content': '固定历史'}]
    payload['evidence'] = [{'source_kind': 'official_web', 'source_key': 'official',
                            'source_url': 'https://www.qust.edu.cn/policy', 'content': '校园活动按规定提交申请'}]
    compact = UnifiedCase.model_validate(payload).case_payload()
    assert compact == payload
    doc = Document(id='web-policy', page_content=payload['evidence'][0]['content'], metadata={
        'source_key': 'official', 'source_url': 'https://www.qust.edu.cn/policy'})
    pool = corpus([doc])
    pool['web_sources'] = {'official': {'allowed_agents': ['CampusAgent']}}
    prepared = prepare_case(view(compact), pool)
    assert len(prepared['matched_evidence']['E01']) == 1
    assert prepared['payload'] == compact
