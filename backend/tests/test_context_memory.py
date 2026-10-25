"""近期缓存失效后，真实聊天链路仍应把数据库历史交给意图识别。"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.cache.recent_messages import RecentMessageCache
from app.graph.builder import GlobalGraphDependencies, build_global_graph
from app.intent.signals import IntentSignal
from app.intent.service import IntentRecognitionService
from app.routing.router import AgentRouter
from app.schemas.intent import IntentResult
from app.services.chat_service import ChatRunService
from app.services.context_service import PostgresContextLoader


class MemoryRedis:
    """只模拟测试所需的 Redis 列表语义，不连接外部服务。"""

    def __init__(self):
        self.lists = {}

    async def lrange(self, key, start, stop):
        values = self.lists.get(key, [])
        return values[start:] if stop == -1 else values[start : stop + 1]

    def pipeline(self):
        return MemoryPipeline(self)


class MemoryPipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    def __getattr__(self, name):
        if name not in {"delete", "rpush", "rpushx", "ltrim", "expire"}:
            raise AttributeError(name)

        def queue(*args):
            self.commands.append((name, args))
            return self

        return queue

    async def execute(self):
        for name, args in self.commands:
            key = args[0]
            if name == "delete":
                self.redis.lists.pop(key, None)
            elif name == "rpush":
                self.redis.lists.setdefault(key, []).extend(args[1:])
            elif name == "rpushx" and key in self.redis.lists:
                self.redis.lists[key].extend(args[1:])
            elif name == "ltrim" and key in self.redis.lists:
                start, stop = args[1:]
                values = self.redis.lists[key]
                self.redis.lists[key] = (
                    values[start:] if stop == -1 else values[start : stop + 1]
                )


TOPIC = [
    {"role": "user", "content": "本科生转专业需要什么条件？"},
    {"role": "assistant", "content": "下面介绍本科生转专业的申请条件。"},
]
FOLLOWUP = "那研究生呢？"
CLARIFICATION = "你希望我处理求职、校园事务，还是学习相关的问题？"


class ContextMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def run_followup(self, history, cached):
        client = MemoryRedis()
        cache = RecentMessageCache(SimpleNamespace(client=client))
        if cached:
            await cache.replace("conversation", cached)
        records = [SimpleNamespace(message_id=uuid4(), **item) for item in history]

        async def add_message(conversation_id, role, content, **kwargs):
            message = SimpleNamespace(message_id=uuid4(), role=role, content=content)
            records.append(message)
            return message

        async def list_messages(conversation_id, limit):
            return records[-limit:]

        conversations = SimpleNamespace(
            add_message=AsyncMock(side_effect=add_message),
            list_messages=AsyncMock(side_effect=list_messages),
        )
        loader = PostgresContextLoader(
            conversations,
            SimpleNamespace(latest_summary=AsyncMock(return_value="此前只讨论过寒暄。")),
            SimpleNamespace(list_owned=AsyncMock(return_value=[])),
            cache,
        )
        intent = SimpleNamespace(
            recognize=AsyncMock(return_value=IntentResult(primary_intent="unclear", confidence=.5))
        )
        trace = SimpleNamespace(event=AsyncMock())
        unused = SimpleNamespace()
        graph = build_global_graph(
            GlobalGraphDependencies(
                loader, intent, AgentRouter(), unused, unused, unused, unused, unused, trace
            )
        )

        @asynccontextmanager
        async def lock(*args):
            yield True

        service = ChatRunService(
            graph,
            conversations,
            SimpleNamespace(
                start=AsyncMock(return_value=SimpleNamespace(trace_id=uuid4())),
                finish=AsyncMock(),
            ),
            trace,
            SimpleNamespace(conversation_lock=lock),
            SimpleNamespace(maintain_after_turn=AsyncMock()),
            cache,
        )
        await service.start("user", "conversation", FOLLOWUP)
        while service.tasks:
            await asyncio.gather(*list(service.tasks))

        self.assertEqual(intent.recognize.await_count, 1)
        inputs = intent.recognize.await_args.kwargs
        self.assertEqual(inputs["query"], FOLLOWUP)
        expected_history = history[-7:]
        self.assertEqual(inputs["recent_messages"], expected_history)
        context_event = next(
            call for call in trace.event.await_args_list if call.args[1] == "context.loaded"
        )
        self.assertEqual(context_event.args[2]["recent_count"], len(expected_history))
        expected_cache = [
            {"role": item.role, "content": item.content} for item in records[-8:]
        ]
        self.assertEqual(await cache.get("conversation"), expected_cache)
        return conversations

    async def test_cold_cache_preserves_previous_topic_in_real_chat_graph(self):
        conversations = await self.run_followup(TOPIC, [])
        self.assertEqual(conversations.list_messages.await_count, 1)

    async def test_existing_partial_cache_is_repaired_before_repeated_followup(self):
        failed_turn = [
            {"role": "user", "content": FOLLOWUP},
            {"role": "assistant", "content": CLARIFICATION},
        ]
        await self.run_followup(TOPIC + failed_turn, failed_turn)

    async def test_full_warm_cache_keeps_database_read_fast_path(self):
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": str(i)}
            for i in range(6)
        ] + TOPIC
        conversations = await self.run_followup(history, history)
        conversations.list_messages.assert_not_awaited()

    async def test_cache_expiring_before_answer_does_not_create_an_orphan_message(self):
        client = MemoryRedis()
        cache = RecentMessageCache(SimpleNamespace(client=client))
        await cache.replace("conversation", TOPIC)
        client.lists.clear()  # 模拟整份缓存到期/Redis 重启。
        await cache.append("conversation", "assistant", "新的回答")
        self.assertEqual(await cache.get("conversation"), [])

    async def test_partial_cache_refill_uses_configured_window(self):
        client = MemoryRedis()
        cache = RecentMessageCache(SimpleNamespace(client=client), keep_count=4)
        await cache.replace("conversation", [{"role": "user", "content": FOLLOWUP}])
        history = TOPIC + TOPIC
        conversations = SimpleNamespace(
            list_messages=AsyncMock(return_value=[SimpleNamespace(**item) for item in history])
        )
        loader = PostgresContextLoader(conversations, None, None, cache)
        self.assertEqual(await loader.load_recent("user", "conversation"), history)
        conversations.list_messages.assert_awaited_once_with("conversation", limit=4)
        self.assertEqual(await cache.get("conversation"), history)


class FollowupContextTests(unittest.TestCase):
    def test_elliptical_comparison_passes_topic_to_semantic_classification(self):
        for query in [FOLLOWUP, "那么研究生呢?", "那专科生呢"]:
            with self.subTest(query=query):
                semantic = IntentRecognitionService._semantic_query(
                    query, TOPIC, "", IntentSignal("pattern", {}, {})
                )
                self.assertIn(query, semantic)
                self.assertIn("本科生转专业", semantic)

    def test_followup_can_use_summary_when_recent_history_is_empty(self):
        summary = "刚才讨论了本科生转专业申请条件。"
        semantic = IntentRecognitionService._semantic_query(
            FOLLOWUP, [], summary, IntentSignal("pattern", {}, {})
        )
        self.assertIn(summary, semantic)

    def test_explicit_new_task_is_not_overridden_by_previous_topic(self):
        query = "那成绩单的GPA呢？"
        semantic = IntentRecognitionService._semantic_query(
            query, TOPIC, "", IntentSignal("pattern", {"learning_gpa": .8}, {})
        )
        self.assertEqual(semantic, query)

    def test_followup_without_any_context_stays_unchanged(self):
        self.assertEqual(
            IntentRecognitionService._semantic_query(
                FOLLOWUP, [], "", IntentSignal("pattern", {}, {})
            ),
            FOLLOWUP,
        )