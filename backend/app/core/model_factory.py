"""为不同组件创建可独立配置的 Chat Model 和 Embedding Model。"""

from __future__ import annotations

from typing import Any

from langchain_ollama import OllamaEmbeddings
from langchain_openai import ChatOpenAI

from .config import RuntimeSettings


class ModelFactory:
    def __init__(self, settings: RuntimeSettings, models_config: dict[str, Any]) -> None:
        self.settings = settings
        self.config = models_config

    def chat_model(
        self, component: str, *, max_retries: int = 2, max_output_tokens: int | None = None
    ) -> ChatOpenAI:
        definition = self.config["components"][component]
        if definition.get("provider") != "deepseek":
            raise ValueError("当前Chat Model只配置了DeepSeek兼容接口。")
        extra_body = {"thinking": {"type": "disabled"}}
        if max_output_tokens is not None:
            if max_output_tokens < 1:
                raise ValueError("输出token限制必须大于零。")
            # DeepSeek的正式参数为max_tokens；避免兼容SDK重命名为max_completion_tokens。
            extra_body["max_tokens"] = max_output_tokens
        return ChatOpenAI(
            model=definition["model"],
            temperature=float(definition.get("temperature", 0.0)),
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
            max_retries=max_retries,
            timeout=60,
            extra_body=extra_body,
        )

    def embeddings(self) -> OllamaEmbeddings:
        definition = self.config["components"]["embedding_model"]
        return OllamaEmbeddings(
            model=definition["model"],
            base_url=self.settings.ollama_base_url,
            keep_alive=int(definition.get("keep_alive_seconds", 1800)),
            client_kwargs={"trust_env": False},
        )
