"""共用 fixtures：將 src/ 加入 import path，並隔離所有網絡同時間依賴。"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import fetch_btc_news as m  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每個 test 前清空全域去重 cache，並令 time.sleep 唔會真係等。"""
    m.cache_title_set.clear()
    sleeps = []
    monkeypatch.setattr(m.time, "sleep", lambda s: sleeps.append(s))
    yield sleeps
    m.cache_title_set.clear()


@pytest.fixture
def sleeps(_isolate):
    """記錄咗嘅 sleep 秒數，用嚟檢查 retry 行為。"""
    return _isolate


def rss(items):
    """由 [(title, summary, pubdate or None), ...] 砌一個 RSS 2.0 XML（bytes）。"""
    parts = []
    for title, summary, pub in items:
        pub_xml = f"<pubDate>{pub}</pubDate>" if pub else ""
        parts.append(
            f"<item><title>{title}</title><description>{summary}</description>"
            f"<link>https://example.com/{abs(hash(title))}</link>{pub_xml}</item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<title>Test Feed</title><link>https://example.com</link>"
        "<description>test feed for unit tests, padded to exceed 200 bytes</description>"
        + "".join(parts)
        + "</channel></rss>"
    )
    return xml.encode()


def response(status=200, content=b"", json_data=None):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.content = content
    r.text = content.decode(errors="ignore")
    r.json.return_value = json_data
    return r


SOURCE_GENERAL = {"name": "General", "url": "https://g.example/rss", "emoji": "📰",
                  "btc_focused": False, "content_keywords": []}
SOURCE_FOCUSED = {"name": "Focused", "url": "https://f.example/rss", "emoji": "₿",
                  "btc_focused": True, "content_keywords": []}


def article(title, hours_ago=0, source="General"):
    from datetime import timedelta
    return {"title": title, "summary": "", "link": "", "source": source, "emoji": "📰",
            "published": datetime.now(timezone.utc) - timedelta(hours=hours_ago)}