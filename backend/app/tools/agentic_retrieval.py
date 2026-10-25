"""ExternalKnowledgeRetriever 的 LangChain Tool Adapter。"""

from __future__ import annotations

from uuid import UUID

from langchain_core.documents import Document
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.core.source_pages import page_label
from app.agents.evidence import EvidenceRegistry
from app.retrieval.external.planned import InvalidQueryPlan, PlannedKnowledgeRetriever, RetrievalSession
from app.schemas.enums import AgentName, IntentName
from app.schemas.retrieval import QueryPlan, RetrievalRequest


class AgenticRetrievalToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    plan: QueryPlan


def render_documents(documents: list[Document]) -> str:
    if not documents:
        return "未检索到能够支持回答的外部资料。"
    blocks = []
    for index, document in enumerate(documents, start=1):
        metadata = document.metadata
        title = metadata.get("title") or metadata.get("filename") or "未命名资料"
        source = metadata.get("source_url") or metadata.get("filename") or "本地知识库"
        pages = page_label(metadata)
        page_text = "，" + pages if pages else ""
        identifier = metadata.get("evidence_id")
        marker = f"[[e:{identifier}]]" if identifier else f"[资料{index}]"
        blocks.append(f"{marker} {title}{page_text}\n{document.page_content}\n来源：{source}")
    return "\n\n".join(blocks)


def create_agentic_retrieval_tool(
    retriever: PlannedKnowledgeRetriever,
    conversation_id: UUID,
    agent_name: AgentName,
    intent: IntentName,
    registry: EvidenceRegistry | None = None,
) -> StructuredTool:
    registry = registry or EvidenceRegistry(agent_name.value)
    session = RetrievalSession()

    async def run(query: str, plan: QueryPlan | dict):
        plan = QueryPlan.model_validate(plan)
        try:
            execution = await retriever.retrieve_with_diagnostics(
                RetrievalRequest(query=query, conversation_id=conversation_id,
                                 agent_name=agent_name, intent=intent),
                plan=plan, session=session,
            )
        except InvalidQueryPlan as exc:
            return f"检索计划无效，未执行召回：{exc}。请修正计划或依据已有证据作答。", {
                "documents": [], "diagnostics": {
                    "protocol": "agentic", "rounds": 0, "total_rounds": session.rounds,
                    "evidence_sufficient": None, "stop_reason": "invalid_plan",
                    "errors": [str(exc)], "query_plan": plan.model_dump(mode="json"),
                },
            }
        documents = registry.register(execution.documents)
        example = (
            f"正确引用示例：事实说明[[e:{documents[0].metadata['evidence_id']}]]。"
            "不要使用历史数字编号如[1]。\n" if documents else ""
        )
        content = (
            f"证据可回答性由你判断；检索轮数={execution.diagnostics.rounds}；"
            f"累计批次={execution.diagnostics.total_rounds}/3。\n"
            f"结束原因：{execution.diagnostics.finished_reason}\n"
            f"引用时完整复制证据标识，不自行编号或列参考来源。\n{example}\n{render_documents(documents)}"
        )
        return content, {
            "documents": documents,
            "diagnostics": execution.diagnostics.model_dump(mode="json"),
        }

    available_web = "；".join(
        f"{source.source_key}：{source.display_name}"
        for source in retriever.web.registry.allowed_for(agent_name)
    ) if hasattr(retriever.web, "registry") else "无"
    return StructuredTool.from_function(
        coroutine=run,
        name="agentic_retrieval",
        description=(
            "检索回答青岛科技大学相关事实所需的权威外部证据。"
            "由你提供查询计划（original/rewrite/hyde/multi_query）与来源，每次执行一批。"
            "整次Agent最多三批实际检索，成功查询缓存跨调用复用。"
            "该工具只返回证据，不生成最终用户回答。"
            f"当前Agent授权的官网source_key：{available_web or '无'}。"
        ),
        args_schema=AgenticRetrievalToolInput,
        response_format="content_and_artifact",
    )
