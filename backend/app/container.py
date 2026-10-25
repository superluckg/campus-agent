"""应用组合根：集中装配依赖，业务模块不自行创建全局连接。"""

from __future__ import annotations

from pathlib import Path
import asyncio
import logging
from time import perf_counter

from app.agents.composer import ResponseComposer
from app.agents.factory import AgentFactory
from app.agents.prompt_assembler import PromptAssembler
from app.cache.event_bus import RedisEventBus
from app.cache.recent_messages import RecentMessageCache
from app.cache.redis_client import RedisResources
from app.core.config import PROJECT_ROOT, RuntimeSettings, get_settings
from app.core.model_factory import ModelFactory
from app.db.repositories import (
    ConversationRepository,
    IngestionRepository,
    MemoryRepository,
    PostgresKnowledgeRepository,
    TraceRepository,
    UploadRepository,
    UserRepository,
    WebCacheRepository,
)
from app.db.session import Database
from app.graph.builder import GlobalGraphDependencies, build_global_graph
from app.evaluation.retrieval.corpus import FrozenWebService, documents as corpus_documents
from app.services.direct_reply import DirectReplyService
from app.ingestion.service import KnowledgeIngestionService
from app.intent.runtime import build_intent_service
from app.retrieval.bm25 import BM25Index
from app.retrieval.external.planned import PlannedKnowledgeRetriever
from app.retrieval.local.service import LocalRetrievalService
from app.retrieval.web.registry import WebSourceRegistry
from app.retrieval.web.selector import WebSourceSelector
from app.retrieval.web.service import WebSourceRetrievalService
from app.routing.router import AgentRouter
from app.services.agent_execution import LangChainAgentExecutionService
from app.services.chat_service import ChatRunService
from app.services.conversation_activity import ConversationActivity
from app.services.conversation_management import ConversationManagementService
from app.services.conversation_title import ConversationTitleService
from app.services.context_service import PostgresContextLoader
from app.services.conversation_memory_retriever import ConversationMemoryRetriever
from app.services.memory_service import MemoryService
from app.services.trace_service import CompositeTraceSink
from app.services.upload_service import UploadService
from app.skills.embedding_matcher import SkillEmbeddingMatcher
from app.skills.manager import SkillManager
from app.db.repositories.unified_evaluation import UnifiedEvaluationRepository
from app.evaluation.unified_service import UnifiedEvaluationService, UnifiedJudge


logger = logging.getLogger(__name__)


class AppContainer:
    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        app_config = settings.load_yaml("app.yaml")
        model_config = settings.load_yaml("models.yaml")
        model_factory = ModelFactory(settings, model_config)
        embeddings = model_factory.embeddings()

        self.database = Database(settings.database_url)
        self.redis = RedisResources(settings.redis_url)
        self.users = UserRepository(self.database)
        self.workspace_user_id: str | None = None
        self.conversations = ConversationRepository(self.database)
        self.conversation_activity = ConversationActivity()
        self.conversation_management = ConversationManagementService(
            self.database, self.conversation_activity, settings.upload_dir, self.redis,
        )
        self.conversation_titles = ConversationTitleService(
            self.conversations,
            model_factory.chat_model("memory_model", max_retries=0, max_output_tokens=128),
        )
        self.uploads = UploadRepository(self.database)
        self.knowledge = PostgresKnowledgeRepository(self.database, embeddings)
        self.memories = MemoryRepository(self.database, embeddings)
        self.traces = TraceRepository(self.database)
        self.web_cache = WebCacheRepository(self.database)
        self.ingestion_repository = IngestionRepository(self.database)

        trace_config = app_config["trace"]
        self.event_bus = RedisEventBus(
            self.redis,
            ttl_seconds=int(trace_config["sse_event_ttl_seconds"]),
        )
        recent_cache = RecentMessageCache(
            self.redis,
            keep_count=int(app_config["memory"]["recent_message_count"]),
            ttl_seconds=int(app_config["memory"]["redis_ttl_seconds"]),
        )
        trace_sink = CompositeTraceSink(self.traces, self.event_bus)

        intent_config = app_config["intent"]
        intent_service = build_intent_service(settings, model_factory, embeddings, intent_config)
        self.intent_service = intent_service
        self.intent_embedding_warmup = {"status": "pending"}
        self.intent_router = AgentRouter()

        skill_config = app_config["skills"]
        self.skill_manager = SkillManager(
            settings.skills_dir,
            optional_threshold=float(skill_config["optional_score_threshold"]),
            max_selected=int(skill_config["max_selected"]),
            token_budget=int(skill_config["approximate_token_budget"]),
            character_limit=int(skill_config["hard_character_limit"]),
        )
        skill_matcher = SkillEmbeddingMatcher(self.skill_manager, embeddings)

        retrieval_config = app_config["retrieval"]
        self.retrieval_config = dict(retrieval_config)
        self.bm25 = BM25Index()
        local_retrieval = LocalRetrievalService(
            repository=self.knowledge,
            bm25_index=self.bm25,
            route_top_k=int(retrieval_config["route_top_k"]),
            rrf_k=int(retrieval_config["rrf_k"]),
            rrf_candidate_k=int(retrieval_config["rrf_candidate_k"]),
        )
        web_registry = WebSourceRegistry.from_yaml(settings.config_dir / "web_sources.yaml")
        web_selector = WebSourceSelector(web_registry)
        self.web_registry = web_registry
        web_service = WebSourceRetrievalService(web_registry, self.web_cache)
        prompt_dir = Path(__file__).parent
        external_retriever = PlannedKnowledgeRetriever(
            local_service=local_retrieval, web_service=web_service, web_selector=web_selector,
            memory_retriever=ConversationMemoryRetriever(self.conversations, self.memories),
            max_rounds=int(retrieval_config["max_rounds"]),
            source_timeout_seconds=int(retrieval_config["source_timeout_seconds"]),
        )
        self.external_retriever = external_retriever

        # 评测与聊天复用同一检索实现，官网与记忆使用隔离输入。
        def evaluation_retriever(corpus, memory_retriever=None):
            index = BM25Index()
            index.rebuild(corpus_documents([unit for unit in corpus["units"] if unit["source_kind"] == "local"]))
            frozen_web = FrozenWebService(corpus)
            local = LocalRetrievalService(self.knowledge, index,
                route_top_k=int(retrieval_config["route_top_k"]), rrf_k=int(retrieval_config["rrf_k"]),
                rrf_candidate_k=int(retrieval_config["rrf_candidate_k"]))
            return PlannedKnowledgeRetriever(
                local_service=local, web_service=frozen_web,
                web_selector=WebSourceSelector(frozen_web.registry),
                memory_retriever=memory_retriever,
                max_rounds=int(retrieval_config["max_rounds"]),
                source_timeout_seconds=int(retrieval_config["source_timeout_seconds"]),
            )

        memory_config = app_config["memory"]
        memory_service = MemoryService(
            repository=self.memories,
            conversations=self.conversations,
            model=model_factory.chat_model("memory_model"),
            prompt_dir=prompt_dir / "memory" / "prompts",
            summarize_after_messages=int(memory_config["summarize_after_messages"]),
            keep_recent=int(memory_config["recent_message_count"]),
        )
        agent_model = model_factory.chat_model("agent_model")
        self.agent_recursion_limit = int(app_config["agents"].get("recursion_limit", 50))
        agent_factory = AgentFactory(
            agent_model,
            PromptAssembler(prompt_dir / "agents" / "prompts"),
            max_tool_calls=int(app_config["agents"]["max_tool_calls_per_agent"]),
            max_same_tool_calls=int(app_config["agents"]["max_same_tool_calls"]),
            recursion_limit=self.agent_recursion_limit,
        )
        agent_service = LangChainAgentExecutionService(
            factory=agent_factory,
            retriever=external_retriever,
            web_selector=web_selector,
            web_service=web_service,
            upload_root=settings.upload_dir,
            document_output_limit=int(app_config["uploads"]["max_tool_output_characters"]),
            trace_sink=trace_sink,
        )
        composer = ResponseComposer(
            agent_model,
            prompt_dir / "agents" / "prompts" / "response_composer.md",
        )
        context_loader = PostgresContextLoader(
            self.conversations, self.memories, self.uploads, recent_cache
        )
        direct_reply = DirectReplyService(model_factory.chat_model("chat_model"),
            prompt_dir / "services" / "prompts", model_config["components"]["chat_model"]["model"])
        global_graph = build_global_graph(
            GlobalGraphDependencies(
                context_loader=context_loader,
                intent_service=intent_service,
                router=self.intent_router,
                memory_service=memory_service,
                skill_manager=self.skill_manager,
                skill_score_service=skill_matcher,
                agent_service=agent_service,
                composer=composer,
                trace=trace_sink,
                direct_reply=direct_reply,
                single_agent_timeout_seconds=int(
                    app_config["agents"]["single_agent_timeout_seconds"]
                ),
                multi_agent_timeout_seconds=int(
                    app_config["agents"]["multi_agent_timeout_seconds"]
                ),
            )
        )
        concurrency = app_config["concurrency"]
        self.unified_evaluations = UnifiedEvaluationRepository(self.database)
        def evaluation_graph(fixture, corpus, sink):
            frozen_web = FrozenWebService(corpus)
            service = LangChainAgentExecutionService(agent_factory,
                evaluation_retriever(corpus, fixture), WebSourceSelector(frozen_web.registry),
                frozen_web, settings.upload_dir,
                int(app_config["uploads"]["max_tool_output_characters"]), sink)
            return build_global_graph(GlobalGraphDependencies(
                context_loader=fixture, intent_service=intent_service, router=self.intent_router,
                memory_service=fixture, skill_manager=self.skill_manager, skill_score_service=skill_matcher,
                agent_service=service, composer=composer, trace=sink, direct_reply=direct_reply,
                single_agent_timeout_seconds=int(app_config["agents"]["single_agent_timeout_seconds"]),
                multi_agent_timeout_seconds=int(app_config["agents"]["multi_agent_timeout_seconds"])))
        self.unified_evaluation = UnifiedEvaluationService(self.unified_evaluations, self.traces,
            self.knowledge, self.web_cache, web_registry, evaluation_graph,
            UnifiedJudge(model_factory.chat_model("judge_model")),
            {"app_config": app_config, "models": model_config, "contract": "unified-evaluation"})
        self.chat = ChatRunService(
            graph=global_graph,
            conversations=self.conversations,
            traces=self.traces,
            trace_sink=trace_sink,
            redis=self.redis,
            memory=memory_service,
            recent_cache=recent_cache,
            max_active_requests=int(concurrency["max_active_requests"]),
            lock_ttl_seconds=int(concurrency["conversation_lock_ttl_seconds"]),
            activity=self.conversation_activity,
            title_service=self.conversation_titles,
        )
        upload_config = app_config["uploads"]
        self.upload_service = UploadService(
            upload_root=settings.upload_dir,
            repository=self.uploads,
            allowed_extensions=upload_config["allowed_extensions"],
            max_files=int(upload_config["max_files_per_conversation"]),
            max_file_size_mb=int(upload_config["max_file_size_mb"]),
            max_output_characters=int(upload_config["max_text_characters"]),
            max_pdf_pages=int(upload_config["max_pdf_pages"]),
            max_pptx_slides=int(upload_config["max_pptx_slides"]),
            max_excel_sheets=int(upload_config["max_excel_sheets"]),
            max_excel_rows_per_sheet=int(upload_config["max_excel_rows_per_sheet"]),
        )
        self.ingestion = KnowledgeIngestionService(
            raw_root=PROJECT_ROOT / "data" / "knowledge_base" / "raw",
            repository=self.ingestion_repository,
            knowledge=self.knowledge,
            embeddings=embeddings,
            chunk_size=int(retrieval_config["chunk_size"]),
            chunk_overlap=int(retrieval_config["chunk_overlap"]),
        )

    async def initialize(self) -> None:
        self.workspace_user_id = await self.users.ensure_workspace_user()
        await self.unified_evaluations.recover_interrupted()
        await self.web_cache.sync_sources(self.web_registry)
        documents = await self.knowledge.load_active_chunks()
        self.bm25.rebuild(documents)
        started = perf_counter()
        try:
            profile = await self.intent_service.warmup_embeddings()
        except Exception as exc:
            self.intent_embedding_warmup = {
                "status": "degraded",
                "error_type": type(exc).__name__,
                "duration_ms": (perf_counter() - started) * 1000,
            }
            logger.warning("启动时意图向量预热失败，本进程直接升级Jev：%s", type(exc).__name__)
        else:
            self.intent_embedding_warmup = {
                "status": "ready",
                "duration_ms": (perf_counter() - started) * 1000,
                **profile,
            }
            logger.info("意图向量启动预热完成：%s", self.intent_embedding_warmup)

    async def close(self) -> None:
        unified = getattr(self, "unified_evaluation", None)
        tasks = list(set(self.chat.tasks) | set(unified.tasks.values() if unified else ()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self.intent_service.jev_client.aclose()
        finally:
            try:
                await self.redis.close()
            finally:
                await self.database.close()


def build_container() -> AppContainer:
    return AppContainer(get_settings(require_secrets=True))
