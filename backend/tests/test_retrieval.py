import unittest

from langchain_core.documents import Document

from app.retrieval.rrf import reciprocal_rank_fusion

try:
    from app.retrieval.bm25 import BM25Index, tokenize_for_bm25
except ModuleNotFoundError:
    BM25Index = None
    tokenize_for_bm25 = None


class RetrievalTests(unittest.TestCase):
    @unittest.skipIf(BM25Index is None, "当前指定环境尚未由用户手动准备BM25依赖")
    def test_chinese_tokenizer_keeps_words_and_bigrams(self):
        tokens = tokenize_for_bm25("校园网充值 2026-09-20")
        self.assertIn("校园", tokens)
        self.assertIn("2026-09-20", tokens)

    @unittest.skipIf(BM25Index is None, "当前指定环境尚未由用户手动准备BM25依赖")
    def test_bm25_rebuild_and_search(self):
        index = BM25Index()
        index.rebuild(
            [
                Document(id="network", page_content="校园网充值说明"),
                Document(id="career", page_content="秋季招聘会通知"),
            ]
        )
        hits = index.search("校园网怎么充值", 2)
        self.assertEqual(hits[0].document.id, "network")
        self.assertEqual(index.search("食堂开放时间", 2), [])

    def test_rrf_rewards_cross_route_document(self):
        common = Document(id="common", page_content="共同证据")
        dense_only = Document(id="dense", page_content="向量证据")
        lexical_only = Document(id="lexical", page_content="词法证据")
        fused = reciprocal_rank_fusion(
            [("dense", [dense_only, common]), ("bm25", [lexical_only, common])]
        )
        self.assertEqual(fused[0].document.id, "common")
        self.assertEqual(set(fused[0].route_ranks), {"dense", "bm25"})


if __name__ == "__main__":
    unittest.main()
