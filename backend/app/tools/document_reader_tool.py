"""DocumentReaderService 的 LangChain Tool Adapter。"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_core.tools import StructuredTool
from app.agents.evidence import EvidenceRegistry
from app.tools.agentic_retrieval import render_documents

from .document_reader import DocumentReadRequest, DocumentReaderService


def create_document_reader_tool(service: DocumentReaderService, registry: EvidenceRegistry | None = None) -> StructuredTool:
    registry = registry or EvidenceRegistry("Upload")
    async def run(**kwargs):
        request = DocumentReadRequest(**kwargs)
        result = service.read(request)
        documents = registry.register([
            Document(page_content=segment.content, metadata=segment.metadata)
            for segment in result.segments
        ])
        suffix = "\n内容尚未读取完，可使用continuation_token继续。" if result.truncated else ""
        content = render_documents(documents) + suffix
        if result.continuation_token:
            content += "\ncontinuation_token=%s" % result.continuation_token
        return content, documents

    return StructuredTool.from_function(
        coroutine=run,
        name="document_reader",
        description=(
            "读取当前会话中用户已经上传并明确选择的文件。"
            "支持PDF、DOCX、PPTX、Markdown、TXT和XLSX；不支持图片、扫描PDF和URL。"
        ),
        args_schema=DocumentReadRequest,
        response_format="content_and_artifact",
    )

