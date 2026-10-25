"""集中读取环境变量和 YAML 配置；导入时不连接外部服务。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote_plus

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError("缺少必要环境变量：%s" % name)
    return value


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError("配置文件不存在：%s" % path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("配置文件顶层必须是对象：%s" % path)
    return payload


@dataclass(frozen=True)
class RuntimeSettings:
    environment: str
    config_dir: Path
    skills_dir: Path
    upload_dir: Path
    database_url: str
    redis_url: str
    ollama_base_url: str
    deepseek_base_url: str
    deepseek_api_key: str

    def load_yaml(self, filename: str) -> Dict[str, Any]:
        return _read_yaml(self.config_dir / filename)


@lru_cache(maxsize=1)
def get_settings(require_secrets: bool = True) -> RuntimeSettings:
    config_dir = Path(os.getenv("APP_CONFIG_DIR", str(PROJECT_ROOT / "config")))
    postgres_user = os.getenv("POSTGRES_USER", "postgres")
    postgres_password = os.getenv("POSTGRES_PASSWORD") or "qust_local_dev"
    postgres_host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    postgres_port = os.getenv("POSTGRES_PORT", "15433")
    postgres_db = os.getenv("POSTGRES_DB", "qust_campus_agent")
    database_url = (
        "postgresql+asyncpg://%s:%s@%s:%s/%s"
        % (
            quote_plus(postgres_user),
            quote_plus(postgres_password),
            postgres_host,
            postgres_port,
            quote_plus(postgres_db),
        )
    )
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if require_secrets:
        api_key = _required_env("DEEPSEEK_API_KEY")
    return RuntimeSettings(
        environment=os.getenv("APP_ENV", "local"),
        config_dir=config_dir,
        skills_dir=Path(os.getenv("APP_SKILLS_DIR", str(PROJECT_ROOT / "skills"))),
        upload_dir=Path(os.getenv("APP_UPLOAD_DIR", str(PROJECT_ROOT / "runtime/uploads"))),
        database_url=database_url,
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6380/0"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435"),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_api_key=api_key,
    )
