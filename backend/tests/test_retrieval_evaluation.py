import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from langchain_core.documents import Document
from app.evaluation.retrieval.corpus import FrozenWebService, CorpusChanged, check_corpus, eligible_unit
from app.evaluation.retrieval.scoring import serialize_unit, local_fingerprint


def doc(key="a", text="学校校园网可以使用支付宝充值", **metadata):
    return Document(id=key, page_content=text, metadata={"chunk_id": key, "filename": "手册.pdf", "page_number": 2, **metadata})


def case(**changes):
    return {"id": "Q01", "case_id": str(uuid4()), "corpus_id": str(uuid4()), "query": "校园网如何充值",
            "agent_name": "CampusAgent", "intent": "campus_network", "optional_filters": None,
            "answerable": True, "reference_answer": "使用支付宝充值", "evidence": [{"text": "学校校园网可以使用支付宝充值"}],
            "enabled": True, "relevant_unit_keys": ["local:a"], **changes}


class FrozenCorpusTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_web_never_fetches_and_copies_documents(self):
        web = Document(id="web:1", page_content="固定的网站证据", metadata={"source_key": "web", "source_url": "https://example.com"})
        corpus = {"units": [serialize_unit(web)], "web_sources": {"web": {"source_key": "web", "display_name": "官网", "url": "https://example.com", "adapter": "static_html", "allowed_agents": ["CampusAgent"], "cache_ttl_seconds": 60}}}
        frozen = FrozenWebService(corpus)
        first = await frozen.retrieve_sources(["web"])
        first[0].metadata["changed"] = True
        self.assertNotIn("changed", (await frozen.retrieve_sources(["web"]))[0].metadata)
        self.assertFalse(eligible_unit(case(agent_name="CareerAgent"), corpus["units"][0], corpus))
        with self.assertRaises(ValueError): await frozen.retrieve_sources(["unfixed"])

    async def test_corpus_change_detects_text_metadata_and_membership(self):
        original = doc()
        corpus = {"local_fingerprint": local_fingerprint([serialize_unit(original)])}
        knowledge = SimpleNamespace(load_active_chunks=AsyncMock(return_value=[original]))
        await check_corpus(knowledge, corpus)
        knowledge.load_active_chunks.return_value = [doc(text="内容改变")]
        with self.assertRaises(CorpusChanged): await check_corpus(knowledge, corpus)




if __name__ == "__main__": unittest.main()
