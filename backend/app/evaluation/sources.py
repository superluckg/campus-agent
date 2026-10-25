"""评测标注资料预览。只读取有效索引与注册官网缓存，不读取任意路径或抓取网址。"""

from collections import defaultdict
from app.core.source_pages import page_fragments


def preview_sections(documents):
    groups = defaultdict(list)
    for position, doc in enumerate(documents):
        meta = doc.metadata
        # 将跨页chunk投影回物理页，预览不把第二页正文误标为第一页。
        for page, content, offset in page_fragments(doc.page_content, meta):
            key = (page, meta.get("section_path", ""), meta.get("source_url", ""))
            groups[key].append((meta.get("chunk_index", position), content, offset))
    sections = []
    for (page, section, url), rows in groups.items():
        text, previous, source_end = "", None, 0
        for index, content, source_start in sorted(rows):
            if source_start is not None:
                if text and source_start > source_end:
                    text += "\n\n"
                overlap = max(0, source_end - source_start)
                text += content[overlap:]
                source_end = max(source_end, source_start + len(content))
                previous = index
                continue
            adjacent = previous is not None and index == previous + 1
            overlap = (
                next(
                    (
                        size
                        for size in range(min(len(text), len(content)), 3, -1)
                        if text.endswith(content[:size])
                    ),
                    0,
                )
                if adjacent
                else 0
            )
            text += ("\n\n" if text and not adjacent else "") + content[overlap:]
            previous = index
        sections.append(
            {
                "section_id": str(len(sections)),
                "page_number": page,
                "section_path": section,
                "source_url": url,
                "content": text,
            }
        )
    return sections


class EvaluationSources:
    def __init__(self, knowledge, web_cache, registry):
        self.knowledge, self.web_cache, self.registry = knowledge, web_cache, registry

    async def catalog(self):
        local = {}
        for doc in await self.knowledge.load_evaluation_chunks():
            meta = doc.metadata
            identifier = meta.get("document_id")
            if identifier:
                local[identifier] = {
                    "id": identifier,
                    "source_kind": "knowledge_file",
                    "filename": meta.get("filename", ""),
                    "label": meta.get("filename") or meta.get("title", "未命名文档"),
                }
        web = []
        for key in self.registry.keys:
            if await self.web_cache.latest_success(key):
                definition = self.registry.get(key)
                web.append(
                    {
                        "id": key,
                        "source_kind": "official_web",
                        "source_key": key,
                        "label": definition.display_name,
                    }
                )
        return {"local": sorted(local.values(), key=lambda item: item["label"]), "web": web}

    async def local(self, identifier):
        docs = [
            doc
            for doc in await self.knowledge.load_evaluation_chunks()
            if doc.metadata.get("document_id") == identifier
        ]
        if not docs:
            raise LookupError("该文档没有有效的已入库原文，请先完成入库。")
        return {
            "source_kind": "knowledge_file",
            "filename": docs[0].metadata.get("filename", ""),
            "source_key": "",
            "sections": preview_sections(docs),
        }

    async def web(self, key):
        if key not in self.registry.keys:
            raise LookupError("该官网来源未注册。")
        docs = await self.web_cache.latest_success(key)
        if not docs:
            raise LookupError("该官网来源尚无成功缓存，请先通过正常链路建立缓存。")
        return {
            "source_kind": "official_web",
            "filename": "",
            "source_key": key,
            "sections": preview_sections(docs),
        }
