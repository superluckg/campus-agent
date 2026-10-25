"""根据 Agent 权限矩阵构造本轮工具，并执行共享 Agent Harness。"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from app.agents.factory import AgentFactory
from app.core.response_stream import ResponseObserver
from app.retrieval.external.planned import PlannedKnowledgeRetriever
from app.agents.evidence import EvidenceRegistry
from app.retrieval.web.selector import WebSourceSelector
from app.retrieval.web.service import WebSourceRetrievalService
from app.schemas.agent import AgentAssignment, AgentResult
from app.schemas.enums import AgentName, IntentName
from app.skills.models import SelectedSkillResult
from app.tools.agentic_retrieval import create_agentic_retrieval_tool
from app.tools.document_reader import DocumentReaderService
from app.tools.document_reader_tool import create_document_reader_tool
from app.tools.fixed_web_search import create_fixed_web_search_tool
from app.tools.gpa_calculator import GPACalculatorService
from app.tools.gpa_tool import create_gpa_calculator_tool

from .upload_resolver import ScopedUploadResolver
from app.core.observability import span, observed_tools, current_observation


class LangChainAgentExecutionService:
    def __init__(
        self,
        factory: AgentFactory,
        retriever: PlannedKnowledgeRetriever,
        web_selector: WebSourceSelector,
        web_service: WebSourceRetrievalService,
        upload_root: Path,
        document_output_limit: int = 40000,
        trace_sink=None,
    ) -> None:
        self.factory = factory
        self.retriever = retriever
        self.web_selector = web_selector
        self.web_service = web_service
        self.upload_root = upload_root
        self.document_output_limit = document_output_limit
        self.trace_sink = trace_sink

    async def execute(
        self,
        assignment: AgentAssignment,
        state: dict,
        intent: IntentName,
        skills: SelectedSkillResult,
        observer: ResponseObserver | None = None,
    ) -> AgentResult:
        if self.trace_sink is not None:
            await self.trace_sink.event(
                state["request_id"],
                "agent.started",
                {
                    "agent": assignment.agent_name.value,
                    "focus": assignment.focus,
                    "message": "%s 正在处理。" % assignment.agent_name.value,
                },
            )
        resolver = ScopedUploadResolver(self.upload_root, state.get("uploaded_files", []))
        registry = EvidenceRegistry(assignment.agent_name.value)
        tools = [
            create_agentic_retrieval_tool(
                self.retriever,
                UUID(state["conversation_id"]),
                assignment.agent_name,
                intent,
                registry,
            )
        ]
        if assignment.agent_name == AgentName.CAREER:
            tools.extend(
                [
                    create_fixed_web_search_tool(
                        self.web_selector,
                        self.web_service,
                        assignment.agent_name,
                        registry,
                    ),
                    create_document_reader_tool(
                        DocumentReaderService(resolver, self.document_output_limit), registry,
                    ),
                ]
            )
        elif assignment.agent_name == AgentName.LEARNING:
            tools.extend(
                [
                    create_document_reader_tool(
                        DocumentReaderService(resolver, self.document_output_limit), registry,
                    ),
                    create_gpa_calculator_tool(GPACalculatorService(resolver)),
                ]
            )
        observer_state = current_observation()
        if observer_state is not None:
            await observer_state.emit("context.injected", {
                "agent": assignment.agent_name.value, "focus": assignment.focus,
                "recent_messages": state.get("recent_messages", []),
                "conversation_summary": state.get("conversation_summary", ""),
                "long_term_memories": state.get("recalled_memories", []),
                "skills": skills.selected, "available_files": state.get("uploaded_files", []),
            })
        runner = self.factory.create(
            agent_name=assignment.agent_name,
            tools=observed_tools(tools, assignment.agent_name.value),
            task_focus=assignment.focus,
            conversation_summary=state.get("conversation_summary", ""),
            long_term_memories=state.get("recalled_memories", []),
            selected_skills=skills.selected,
            available_files=state.get("uploaded_files", []),
            evidence_registry=registry,
        )
        messages = [*state.get("recent_messages", [])]
        if not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != state["query"]:
            messages.append({"role": "user", "content": state["query"]})
        async def progress(event_type: str, payload: dict) -> None:
            await self.trace_sink.event(
                state["request_id"], event_type,
                {**payload, "agent": assignment.agent_name.value},
            )

        async with span("agent", assignment.agent_name.value, agent=assignment.agent_name.value,
                        focus=assignment.focus) as details:
            result = await runner.ainvoke(
                messages, observer=observer,
                on_progress=progress if self.trace_sink is not None else None,
            )
            details["output"] = result
            details["status"] = result.status.value
        if self.trace_sink is not None:
            await self.trace_sink.event(
                state["request_id"],
                "agent.completed",
                {
                    "agent": assignment.agent_name.value,
                    "message": "%s 已完成。" % assignment.agent_name.value,
                    "status": result.status.value,
                    "evidence_sufficient": result.evidence_sufficient,
                    "citation_count": len(result.citations),
                    "tools": [
                        record.model_dump(mode="json") for record in result.tool_records
                    ],
                },
            )
            await self.trace_sink.event(
                state["request_id"],
                "agent.progress",
                {
                    "agent": assignment.agent_name.value,
                    "message": "%s 已完成。" % assignment.agent_name.value,
                },
            )
        return result
