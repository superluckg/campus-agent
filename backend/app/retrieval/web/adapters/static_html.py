"""静态学校官网正文提取。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from langchain_core.documents import Document

from ..registry import WebSourceConfig


_DATE = re.compile(r"(?:20\d{2})[-年/.](?:0?[1-9]|1[0-2])[-月/.](?:0?[1-9]|[12]\d|3[01])日?")


class StaticHtmlAdapter:
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def load(self, source: WebSourceConfig) -> list[Document]:
        headers = {"User-Agent": "Mozilla/5.0 QUST-Campus-Agent/1.0"}
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(str(source.url))
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        for node in soup.select("script, style, nav, footer, header, noscript"):
            node.decompose()
        title_node = soup.select_one("h1") or soup.select_one("title")
        title = title_node.get_text(" ", strip=True) if title_node else source.display_name
        content_node = (
            soup.select_one(".v_news_content")
            or soup.select_one("#vsb_content")
            or soup.select_one("article")
            or soup.select_one("main")
            or soup.body
        )
        if content_node is None:
            return []
        for anchor in content_node.select("a[href]"):
            href = anchor.get("href")
            if href:
                anchor["href"] = urljoin(str(source.url), href)
        text = "\n".join(
            line.strip()
            for line in content_node.get_text("\n", strip=True).splitlines()
            if line.strip()
        )
        if not text:
            return []
        date_match = _DATE.search(text)
        return [
            Document(
                page_content=text,
                metadata={
                    "source_type": "web",
                    "source_key": source.source_key,
                    "title": title,
                    "source_url": str(source.url),
                    "published_at": date_match.group(0) if date_match else None,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                },
            )
        ]

