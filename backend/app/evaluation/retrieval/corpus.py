"""固定当次网站缓存；本地资料指纹变化时停止评测。"""

from copy import deepcopy
from langchain_core.documents import Document
from app.retrieval.web.registry import WebSourceConfig, WebSourceRegistry
from .scoring import local_fingerprint, serialize_unit


class CorpusChanged(ValueError):
    pass


def documents(units):
    return [Document(id=unit["id"], page_content=unit["content"], metadata=deepcopy(unit["metadata"])) for unit in units]


def eligible_unit(case, unit, corpus):
    if unit["source_kind"] == "web":
        return case["agent_name"] in corpus["web_sources"].get(unit["metadata"].get("source_key"), {}).get("allowed_agents", [])
    return True


class FrozenWebService:
    def __init__(self, corpus):
        self.registry = WebSourceRegistry({key: WebSourceConfig.model_validate(value)
                                          for key, value in corpus["web_sources"].items()})
        self.units = [unit for unit in corpus["units"] if unit["source_kind"] == "web"]

    async def retrieve_sources(self, source_keys):
        unknown = set(source_keys) - set(self.registry.keys)
        if unknown:
            raise ValueError("本次运行没有成功缓存的官网来源：" + ",".join(sorted(unknown)))
        result = documents([unit for unit in self.units if unit["metadata"].get("source_key") in source_keys])
        for document in result:
            document.metadata["cache"] = "evaluation_pinned"
        return result


async def check_corpus(knowledge, corpus):
    loader = knowledge.load_evaluation_chunks if corpus.get("automatic") and hasattr(knowledge, "load_evaluation_chunks") else knowledge.load_active_chunks
    active = [serialize_unit(doc) for doc in await loader()]
    if local_fingerprint(active) != corpus["local_fingerprint"]:
        raise CorpusChanged("本地有效资料在运行期间发生变化，请重新运行。")
