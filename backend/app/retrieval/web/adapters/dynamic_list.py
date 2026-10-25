"""只读取动态列表第一页；不进入详情页。Playwright 浏览器由用户手动准备。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from langchain_core.documents import Document

from ..registry import WebSourceConfig


_DATE = re.compile(r"(?:20\d{2})[-年/.](?:0?[1-9]|1[0-2])[-月/.](?:0?[1-9]|[12]\d|3[01])日?")


class DynamicListAdapter:
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_milliseconds = int(timeout_seconds * 1000)

    async def load(self, source: WebSourceConfig) -> list[Document]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("DynamicListAdapter需要使用者手动安装Playwright及Chromium。") from exc

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(
                    str(source.url),
                    wait_until="networkidle",
                    timeout=self.timeout_milliseconds,
                )
                html = await page.content()
            finally:
                await browser.close()

        soup = BeautifulSoup(html, "lxml")
        results: list[Document] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            title = " ".join(anchor.get_text(" ", strip=True).split())
            href = anchor.get("href") or ""
            if len(title) < 6 or href.startswith(("javascript:", "#")):
                continue
            url = urljoin(str(source.url), href)
            identity = "%s|%s" % (title, url)
            if identity in seen:
                continue
            seen.add(identity)
            parent_text = " ".join(anchor.parent.get_text(" ", strip=True).split()) if anchor.parent else title
            date_match = _DATE.search(parent_text)
            text = parent_text[:1500]
            results.append(
                Document(
                    page_content=text,
                    metadata={
                        "source_type": "web",
                        "source_key": source.source_key,
                        "title": title,
                        "source_url": url,
                        "published_at": date_match.group(0) if date_match else None,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    },
                )
            )
            if len(results) >= 30:
                break
        return results

