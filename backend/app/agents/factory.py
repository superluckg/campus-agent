"""共享 create_agent 工厂；三种 Agent 只改变 Prompt、Tools 与 Skills。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Iterable

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph.message import add_messages

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.core.response_stream import ResponseObserver, StructuredTextStream
from app.schemas.agent import AgentAnswer, AgentResult, ToolRecord
from app.schemas.enums import AgentName, RunStatus, ToolName
from app.skills.models import SelectedSkill

from .evidence import EvidenceRegistry, normalize_answer, publish_answer
from .prompt_assembler import PromptAssembler
from app.core.observability import current_observation, span
from app.core.model_usage import model_usage


class BusinessToolCallLimitMiddleware(ToolCallLimitMiddleware):
    """最终结构化答复不是业务工具，不消耗总工具额度。"""

    def after_model(self, state, runtime):
        messages = list(state.get("messages", []))
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, AIMessage):
                messages[index] = message.model_copy(update={
                    "tool_calls": [
                        call for call in message.tool_calls if call["name"] != "AgentAnswer"
                    ]
                })
                break
        return super().after_model({**state, "messages": messages}, runtime)


class ModelTimingMiddleware(AgentMiddleware):
    def __init__(self, agent_name: AgentName):
        self.agent_name = agent_name
        self.call_number = 0

    async def awrap_model_call(self, request, handler):
        self.call_number += 1
        async with span("agent.model", f"业务模型第 {self.call_number} 次调用",
                        agent=self.agent_name, call_number=self.call_number) as details:
            response = await handler(request)
            messages = [m for m in response.result if isinstance(m, AIMessage)]
            details["usage"] = model_usage(messages[-1]) if messages else {}
            details["actions"] = [
                {"action": "answer" if call["name"] == "AgentAnswer" else "tool",
                 "name": call["name"], "arguments": call["args"] if call["name"] != "AgentAnswer" else {
                     "evidence_sufficient": call["args"].get("evidence_sufficient"),
                     "unresolved_points": call["args"].get("unresolved_points", []),
                 }} for message in messages for call in message.tool_calls
            ]
            return response


class AgentRunner:
    def __init__(self, graph: Any, agent_name: AgentName, recursion_limit: int = 50,
                 evidence_registry: EvidenceRegistry | None = None) -> None:
        if recursion_limit < 1:
            raise ValueError("Agent图步数上限必须大于零。")
        self.graph = graph
        self.agent_name = agent_name
        self.recursion_limit = recursion_limit
        self.registry = evidence_registry or EvidenceRegistry(agent_name.value)

    async def ainvoke(
        self, messages: list[dict[str, str]], *,
        observer: ResponseObserver | None = None,
        on_progress: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> AgentResult:
        if observer is None and on_progress is None:
            result = await self.graph.ainvoke(
                {"messages": messages}, config={"recursion_limit": self.recursion_limit},
            )
        else:
            result = {"messages": messages}
            extractor = StructuredTextStream("AgentAnswer", "answer")
            started_calls: set[str] = set()
            completed_calls: set[str] = set()
            business_names = {item.value for item in ToolName}
            async for part in self.graph.astream(
                {"messages": messages}, config={"recursion_limit": self.recursion_limit},
                stream_mode=["messages", "updates"], version="v2",
            ):
                if part["type"] == "messages" and observer is not None:
                    message, metadata = part["data"]
                    if metadata.get("langgraph_node") == "model":
                        text = extractor.feed(message)
                        if text is not None:
                            await publish_answer(observer, normalize_answer(text, self.registry.citations, preview=True))
                elif part["type"] == "updates":
                    for update in part["data"].values():
                        if not isinstance(update, dict):
                            continue
                        if "structured_response" in update:
                            result["structured_response"] = update["structured_response"]
                        new_messages = update.get("messages", [])
                        if new_messages:
                            result["messages"] = add_messages(result["messages"], new_messages)
                        if on_progress is None:
                            continue
                        for message in new_messages:
                            if isinstance(message, AIMessage):
                                for call in message.tool_calls:
                                    if call["name"] in business_names and call["id"] not in started_calls:
                                        started_calls.add(call["id"])
                                        await on_progress("tool.started", {
                                            "tool": call["name"], "call_id": call["id"],
                                            "message": "正在使用工具：" + call["name"],
                                        })
                            elif (
                                isinstance(message, ToolMessage)
                                and message.name in business_names
                                and message.tool_call_id not in completed_calls
                            ):
                                completed_calls.add(message.tool_call_id)
                                await on_progress("tool.completed", {
                                    "tool": message.name, "call_id": message.tool_call_id,
                                    "status": message.status,
                                    "message": "工具处理结束：" + message.name,
                                })
        structured = result.get("structured_response")
        if isinstance(structured, dict):
            structured = AgentAnswer.model_validate(structured)
        if isinstance(structured, AgentAnswer):
            structured = AgentResult(agent_name=self.agent_name, **structured.model_dump())
        if not isinstance(structured, AgentResult):
            # 未配置/未形成最终结构时，模型普通文字可能是内部规划。
            answer = "Agent达到执行上限，但未能形成结构化回答。"
            structured = AgentResult(
                agent_name=self.agent_name,
                answer=answer,
                evidence_sufficient=False,
                unresolved_points=["未生成完整结构化结果。"],
                status=RunStatus.PARTIAL,
            )
        if structured.agent_name != self.agent_name:
            structured.agent_name = self.agent_name
        messages = list(result.get("messages") or [])
        records = []
        observation = current_observation()
        measured_calls = list(observation.tool_calls) if observation else []
        for message in messages:
            raw_name = getattr(message, "name", None)
            if raw_name not in {item.value for item in ToolName}:
                continue
            status = str(getattr(message, "status", "success"))
            measurement = next((call for call in measured_calls if call["agent"] == self.agent_name.value and call["tool"] == raw_name), None)
            if measurement:
                measured_calls.remove(measurement)
            records.append(
                ToolRecord(
                    tool_name=ToolName(raw_name),
                    call_number=len(records) + 1,
                    success=status != "error",
                    duration_ms=measurement["duration_ms"] if measurement and measurement["duration_ms"] is not None else 0.0,
                    summary=str(getattr(message, "content", ""))[:300],
                )
            )
        structured.tool_records = records
        normalized = normalize_answer(structured.answer, self.registry.citations)
        structured.answer = normalized.content
        structured.citations = normalized.citations
        structured.metadata = normalized.metadata
        structured.evidence_answer = normalized.evidence_answer
        structured.evidence = dict(self.registry.citations)
        if not structured.evidence_sufficient and structured.status == RunStatus.SUCCESS:
            structured.status = RunStatus.PARTIAL
        if normalized.metadata.get("citation_warning"):
            structured.status = RunStatus.PARTIAL
            structured.unresolved_points.append(normalized.metadata["citation_warning"])
            if observation:
                await observation.emit("citation.invalid", normalized.metadata)
        if observer is not None:
            if hasattr(observer, "set_citations"):
                await observer.set_citations(structured.metadata, structured.citations)
            await observer.update(structured.answer)
        return structured


class AgentFactory:
    def __init__(
        self,
        model: BaseChatModel,
        prompt_assembler: PromptAssembler,
        max_tool_calls: int = 4,
        max_same_tool_calls: int = 2,
        recursion_limit: int = 50,
    ) -> None:
        self.model = model
        self.prompt_assembler = prompt_assembler
        self.max_tool_calls = max_tool_calls
        self.max_same_tool_calls = max_same_tool_calls
        self.recursion_limit = recursion_limit

    def create(
        self,
        agent_name: AgentName,
        tools: Iterable[BaseTool],
        task_focus: str,
        conversation_summary: str,
        long_term_memories: Iterable[str],
        selected_skills: Iterable[SelectedSkill],
        available_files: Iterable[dict[str, str]],
        evidence_registry: EvidenceRegistry | None = None,
    ) -> AgentRunner:
        system_prompt = self.prompt_assembler.assemble(
            agent_name=agent_name,
            task_focus=task_focus,
            conversation_summary=conversation_summary,
            long_term_memories=long_term_memories,
            selected_skills=selected_skills,
            available_files=available_files,
        )
        tool_list = list(tools)
        middleware = [
            ModelTimingMiddleware(agent_name),
            BusinessToolCallLimitMiddleware(
                run_limit=self.max_tool_calls,
                exit_behavior="continue",
            ),
            *[
                ToolCallLimitMiddleware(
                    tool_name=tool.name,
                    run_limit=self.max_tool_calls if tool.name == "agentic_retrieval" else self.max_same_tool_calls,
                    exit_behavior="continue",
                )
                for tool in tool_list
            ],
        ]
        graph = create_agent(
            model=self.model,
            tools=tool_list,
            system_prompt=system_prompt,
            name=agent_name.value,
            middleware=middleware,
            response_format=ToolStrategy(AgentAnswer),
        )
        return AgentRunner(graph=graph, agent_name=agent_name, recursion_limit=self.recursion_limit,
                           evidence_registry=evidence_registry)
