import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_container
from app.api.routes.database import browser, router
from app.db.repositories.database_browser import TABLES, DatabaseBrowserRepository, safe_value
from app.schemas.enums import RunStatus
from app.services.chat_service import ChatRunService


class DatabaseBrowserTests(unittest.TestCase):
    def setUp(self):
        self.repository = DatabaseBrowserRepository(None)

    def test_private_tables_and_sql_identifiers_are_not_exposed(self):
        self.assertEqual(len(TABLES), 19)
        for schema, table in [
            ("public", "unrelated_table"),
            ("app", "users; DROP TABLE app.users"),
        ]:
            with self.assertRaises(LookupError):
                self.repository.table(schema, table)
        users = self.repository.table("app", "users")
        for filters in ({"password_hash": "secret"}, {"unknown": "value"}):
            with self.assertRaises(ValueError):
                self.repository.conditions(users, filters, "")

    def test_filter_values_are_bound_parameters_and_null_is_explicit(self):
        users = self.repository.table("app", "users")
        value = "x' OR 1=1 --"
        condition = self.repository.conditions(users, {"username": value}, "")[0]
        self.assertNotIn(value, str(condition.compile()))
        self.assertIn(value, condition.compile().params.values())
        condition = self.repository.conditions(users, {"username": "__NULL__"}, "")[0]
        self.assertIn("IS NULL", str(condition.compile()))

    def test_nested_credentials_are_hidden_without_hiding_token_counts(self):
        value = safe_value(
            {"password_hash": "secret", "config": {"API_KEY": "secret", "prompt_tokens": 12}}
        )
        self.assertEqual(value["password_hash"], "[已隐藏]")
        self.assertEqual(value["config"]["API_KEY"], "[已隐藏]")
        self.assertEqual(value["config"]["prompt_tokens"], 12)

    def test_messages_describe_trace_relationships(self):
        description = self.repository.describe(self.repository.table("app", "messages"))
        columns = {column["name"]: column for column in description["columns"]}
        self.assertEqual(columns["trace_id"]["reference"]["table"], "observability.traces")
        self.assertTrue(columns["request_id"]["nullable"])
        self.assertEqual(columns["trace_id"]["foreign_keys"], ["observability.traces.trace_id"])

    def test_observability_catalog_uses_only_current_five_tables(self):
        self.assertEqual({key for key in TABLES if key.startswith("observability.")}, {
            "observability.traces", "observability.trace_events", "observability.eval_cases",
            "observability.eval_runs", "observability.eval_results"})
        case = self.repository.describe(self.repository.table("observability", "eval_cases"))
        self.assertIn("case_number", case["default_columns"])
        self.assertNotIn("enabled", case["default_columns"])
        result = self.repository.describe(self.repository.table("observability", "eval_results"))
        columns = {column["name"]: column for column in result["columns"]}
        self.assertEqual(columns["run_id"]["foreign_keys"], ["observability.eval_runs.run_id"])
        self.assertEqual(columns["case_id"]["foreign_keys"], [])


class DatabaseApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_container] = lambda: SimpleNamespace(
            settings=SimpleNamespace(environment="local"),
            workspace_user_id="00000000-0000-0000-0000-000000000001", database=None,
        )
        self.repository = SimpleNamespace(
            catalog=AsyncMock(return_value={"tables": []}),
            rows=AsyncMock(return_value={"rows": [], "total": 0}),
        )
        app.dependency_overrides[browser] = lambda: self.repository
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    def test_database_api_is_local_without_credentials_and_remains_read_only(self):
        url = "/api/developer/database"
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url, headers={"Origin": "https://example.com"}).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 405)
        self.repository.catalog.assert_awaited_once()

    def test_pagination_is_bounded_and_filters_must_be_an_object(self):
        url = "/api/developer/database/app/messages"
        self.assertEqual(self.client.get(url, params={"page_size": 101}).status_code, 422)
        self.assertEqual(self.client.get(url, params={"filters": "[]"}).status_code, 400)
        self.assertEqual(self.client.get(url, params={"filters": "{"}).status_code, 400)


class MessageTraceTests(unittest.IsolatedAsyncioTestCase):
    def service(self, *, acquired=True, fail=False):
        @asynccontextmanager
        async def lock(*args):
            yield acquired

        final = SimpleNamespace(
            content="回答",
            status=RunStatus.SUCCESS,
            agents=[],
            citations=[],
            partial=False,
            unresolved_points=[],
            metadata={},
        )
        graph = SimpleNamespace(
            ainvoke=AsyncMock(side_effect=RuntimeError("model unavailable"))
            if fail
            else AsyncMock(return_value={"final_response": final})
        )
        conversations = SimpleNamespace(
            add_message=AsyncMock(
                return_value=SimpleNamespace(
                    message_id=UUID("00000000-0000-0000-0000-000000000003")
                )
            )
        )
        traces = SimpleNamespace(
            start=AsyncMock(
                return_value=SimpleNamespace(trace_id=UUID("00000000-0000-0000-0000-000000000004"))
            ),
            finish=AsyncMock(),
        )
        return ChatRunService(
            graph,
            conversations,
            traces,
            SimpleNamespace(event=AsyncMock()),
            SimpleNamespace(conversation_lock=lock),
            SimpleNamespace(maintain_after_turn=AsyncMock()),
            SimpleNamespace(append=AsyncMock()),
        )

    async def wait(self, service):
        while service.tasks:
            import asyncio

            await asyncio.gather(*list(service.tasks))

    async def test_user_and_answer_share_the_request_and_trace(self):
        service = self.service()
        request_id = await service.start("user", "conversation", "问题")
        await self.wait(service)
        calls = service.conversations.add_message.await_args_list
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call.kwargs["request_id"], request_id)
            self.assertEqual(call.kwargs["trace_id"], "00000000-0000-0000-0000-000000000004")
        started = next(
            call
            for call in service.trace_sink.event.await_args_list
            if call.args[1] == "request.started"
        )
        self.assertEqual(started.args[2]["message_id"], "00000000-0000-0000-0000-000000000003")

    async def test_failed_generation_keeps_the_user_message_linked(self):
        service = self.service(fail=True)
        request_id = await service.start("user", "conversation", "问题")
        await self.wait(service)
        self.assertEqual(service.conversations.add_message.await_count, 1)
        self.assertEqual(
            service.conversations.add_message.await_args.kwargs["request_id"], request_id
        )
        self.assertEqual(service.traces.finish.await_args.args[1], "failed")

    async def test_busy_conversation_has_a_trace_without_a_message(self):
        service = self.service(acquired=False)
        await service.start("user", "conversation", "问题")
        await self.wait(service)
        self.assertEqual(service.traces.start.await_count, 1)
        service.conversations.add_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
