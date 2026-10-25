from unittest import TestCase

from langchain_core.documents import Document

from app.evaluation.retrieval.matching import OriginalEvidenceMatcher
from app.evaluation.retrieval.scoring import local_fingerprint, serialize_unit


def document(key="a", content="学校校园网可以使用支付宝充值", index=0, **metadata):
    return Document(id=key, page_content=content, metadata={"chunk_id": key, "document_id": "source",
        "filename": "手册.pdf", "page_number": 2, "chunk_index": index, **metadata})


def payload(**changes):
    return {"query": "校园网如何充值", "agent_name": "CampusAgent", "intent": "campus_network",
            "reference_answer": "使用支付宝充值", "evidence": [{"text": "学校校园网可以使用支付宝充值"}],
            **changes}


def corpus(documents):
    units = [serialize_unit(item) for item in documents]
    return {"units": units, "web_sources": {}, "automatic": True, "local_fingerprint": local_fingerprint(units), "fingerprint": "same"}


class MatchingTests(TestCase):
    def test_overlap_cross_chunks_and_all_matching_occurrences(self):
        source = corpus([document("a", "学校校园网充值使用支付宝", 0), document("b", "充值使用支付宝后立即到账", 1)])
        result = OriginalEvidenceMatcher(source).match(payload(evidence=[{"text": "使用支付宝后立即到账", "filename": "手册.pdf", "page_number": 2}]))
        self.assertTrue(result["complete"])
        self.assertEqual(result["relevant_unit_keys"], ["local:a", "local:b"])
        repeats = OriginalEvidenceMatcher(corpus([document("a"), document("b", index=1)])).match(payload())
        self.assertEqual(repeats["relevant_unit_keys"], ["local:a", "local:b"])

    def test_source_anchors_and_no_join_across_filtered_gap_or_document(self):
        matcher = OriginalEvidenceMatcher(corpus([document()]))
        for anchor in ({"filename": "其他.pdf"}, {"page_number": 3}, {"source_url": "https://wrong"}, {"source_key": "wrong"}):
            result = matcher.match(payload(evidence=[{"text": "学校校园网可以使用支付宝充值", **anchor}]))
            self.assertFalse(result["complete"])
        for second in (document("b", "使用支付宝充值", 2), document("b", "使用支付宝充值", 1, document_id="another")):
            result = OriginalEvidenceMatcher(corpus([document("a", "学校校园网可以", 0), second])).match(payload())
            self.assertFalse(result["complete"])
