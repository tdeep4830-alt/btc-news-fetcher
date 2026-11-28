"""
fetch_btc_news.py 嘅 unit tests。

全部 test 都唔會連網：requests.get / requests.post 用 monkeypatch 換走。

兩類 test：
  - 普通 test：鎖定「而家嘅行為」，重構時唔可以改變（characterization tests）。
  - xfail(strict=True)：已知問題。修好之後佢會變 XPASS → pytest 報錯，
    提醒你移走 xfail 標記，將佢變返普通 test。
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests

import fetch_btc_news as m
from conftest import SOURCE_FOCUSED, SOURCE_GENERAL, article, response, rss

NOW = datetime.now(timezone.utc)


def entry(**kw):
    """feedparser entry 支援 .get() 同屬性存取，用 FeedParserDict 模擬。"""
    import feedparser
    return feedparser.FeedParserDict(kw)


# ── is_btc_related ──────────────────────────────────────────────────────────

class TestIsBtcRelated:
    def test_focused_source_accepts_everything(self):
        assert m.is_btc_related(entry(title="Weather today"), SOURCE_FOCUSED)

    def test_title_keyword_case_insensitive(self):
        assert m.is_btc_related(entry(title="BITCOIN hits new high"), SOURCE_GENERAL)

    def test_chinese_title_keyword(self):
        assert m.is_btc_related(entry(title="比特幣突破十萬"), SOURCE_GENERAL)

    def test_summary_keyword_with_html_stripped(self):
        e = entry(title="Markets wrap", summary="<p>Investors piled into <b>blockchain</b> names</p>")
        assert m.is_btc_related(e, SOURCE_GENERAL)

    def test_atom_content_fallback_when_no_summary(self):
        e = entry(title="Markets wrap", content=[{"value": "<div>satoshi era</div>"}])
        assert m.is_btc_related(e, SOURCE_GENERAL)

    def test_unrelated_rejected(self):
        e = entry(title="Oil prices fall", summary="Brent crude dropped 2%")
        assert not m.is_btc_related(e, SOURCE_GENERAL)

    def test_missing_title_and_summary_rejected(self):
        assert not m.is_btc_related(entry(), SOURCE_GENERAL)

    @pytest.mark.xfail(strict=True, reason="已知問題：關鍵字用 substring 比對，'lightning' 會中天氣新聞")
    def test_lightning_weather_news_rejected(self):
        assert not m.is_btc_related(entry(title="Lightning storm hits Texas"), SOURCE_GENERAL)


# ── parse_published_time ────────────────────────────────────────────────────

class TestParsePublishedTime:
    def test_uses_published_parsed(self):
        e = entry(published_parsed=(2026, 11, 20, 8, 30, 0, 0, 0, 0))
        assert m.parse_published_time(e) == datetime(2026, 11, 20, 8, 30, tzinfo=timezone.utc)

    def test_missing_date_falls_back_to_now(self):
        result = m.parse_published_time(entry())
        assert abs((result - datetime.now(timezone.utc)).total_seconds()) < 5


# ── time_ago ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("delta,expected", [
    (timedelta(seconds=-30), "剛剛"),
    (timedelta(seconds=30), "30 秒前"),
    (timedelta(minutes=5), "5 分鐘前"),
    (timedelta(hours=3), "3 小時前"),
    (timedelta(days=2), "2 天前"),
])
def test_time_ago(delta, expected):
    assert m.time_ago(datetime.now(timezone.utc) - delta) == expected


# ── _clean_summary ──────────────────────────────────────────────────────────

class TestCleanSummary:
    def test_strips_tags_and_collapses_whitespace(self):
        assert m._clean_summary("<p>Hello\n\n   <b>world</b></p>") == "Hello world"

    def test_exactly_150_chars_not_truncated(self):
        assert m._clean_summary("a" * 150) == "a" * 150

    def test_over_150_truncated_with_ellipsis(self):
        out = m._clean_summary("a" * 200)
        assert out == "a" * 150 + "…"

    def test_empty(self):
        assert m._clean_summary("") == ""


# ── fetch_feed ──────────────────────────────────────────────────────────────

class TestFetchFeed:
    def _patch_get(self, monkeypatch, *responses_or_exc):
        calls = []
        seq = list(responses_or_exc)

        def fake_get(url, **kw):
            calls.append((url, kw))
            item = seq.pop(0) if len(seq) > 1 else seq[0]
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(m.requests, "get", fake_get)
        return calls

    def test_parses_and_filters_articles(self, monkeypatch):
        body = rss([
            ("Bitcoin ETF inflows surge", "big day", "Mon, 23 Nov 2026 10:00:00 GMT"),
            ("Oil prices fall", "crude down", "Mon, 23 Nov 2026 09:00:00 GMT"),
        ])
        calls = self._patch_get(monkeypatch, response(200, body))
        arts = m.fetch_feed(SOURCE_GENERAL)
        assert [a["title"] for a in arts] == ["Bitcoin ETF inflows surge"]
        a = arts[0]
        assert a["source"] == "General" and a["emoji"] == "📰"
        assert a["published"] == datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
        assert calls[0][1]["headers"] is m.HEADERS and calls[0][1]["timeout"] == 15

    def test_dedups_within_feed(self, monkeypatch):
        body = rss([("Bitcoin rallies", "", None), ("bitcoin RALLIES", "", None)])
        self._patch_get(monkeypatch, response(200, body))
        assert len(m.fetch_feed(SOURCE_GENERAL)) == 1

    def test_403_returns_empty_without_retry(self, monkeypatch):
        calls = self._patch_get(monkeypatch, response(403))
        assert m.fetch_feed(SOURCE_GENERAL) == []
        assert len(calls) == 1

    def test_other_http_error_returns_empty(self, monkeypatch):
        calls = self._patch_get(monkeypatch, response(500))
        assert m.fetch_feed(SOURCE_GENERAL) == []
        assert len(calls) == 1

    def test_too_short_content_returns_empty(self, monkeypatch):
        self._patch_get(monkeypatch, response(200, b"<rss/>"))
        assert m.fetch_feed(SOURCE_GENERAL) == []

    def test_429_backs_off_then_gives_up(self, monkeypatch, sleeps):
        calls = self._patch_get(monkeypatch, response(429))
        assert m.fetch_feed(SOURCE_GENERAL) == []
        assert len(calls) == 2
        assert sleeps[0] == 5

    def test_429_then_success(self, monkeypatch):
        body = rss([("Bitcoin news", "", None)])
        self._patch_get(monkeypatch, response(429), response(200, body))
        assert len(m.fetch_feed(SOURCE_GENERAL)) == 1

    def test_timeout_retries_then_empty(self, monkeypatch, sleeps):
        calls = self._patch_get(monkeypatch, requests.exceptions.Timeout())
        assert m.fetch_feed(SOURCE_GENERAL) == []
        assert len(calls) == 2
        assert sleeps == [2]

    def test_generic_exception_does_not_crash(self, monkeypatch):
        self._patch_get(monkeypatch, ConnectionError("boom"))
        assert m.fetch_feed(SOURCE_GENERAL) == []

    @pytest.mark.xfail(strict=True, reason="已知問題：最後一次 429 之後仲 sleep 10 秒，嘥時間")
    def test_no_sleep_after_final_429(self, monkeypatch, sleeps):
        self._patch_get(monkeypatch, response(429))
        m.fetch_feed(SOURCE_GENERAL)
        assert sum(sleeps) == 5


# ── HEADERS ─────────────────────────────────────────────────────────────────

_HAS_BROTLI = bool(importlib.util.find_spec("brotli") or importlib.util.find_spec("brotlicffi"))


@pytest.mark.xfail(not _HAS_BROTLI, strict=True,
                   reason="已知問題：冇裝 brotli 但 header 聲明接受 br，伺服器回 br 時 requests 解唔到")
def test_accept_encoding_matches_installed_decoders():
    assert "br" not in m.HEADERS["Accept-Encoding"] or _HAS_BROTLI


# ── fetch_news ──────────────────────────────────────────────────────────────

class TestFetchNews:
    def _patch_sources(self, monkeypatch, per_source):
        sources = [{"name": f"S{i}"} for i in range(len(per_source))]
        monkeypatch.setattr(m, "NEWS_SOURCES", sources)
        mapping = {s["name"]: arts for s, arts in zip(sources, per_source)}
        monkeypatch.setattr(m, "fetch_feed", lambda s: mapping[s["name"]])

    def test_merges_sorts_newest_first_and_limits(self, monkeypatch):
        self._patch_sources(monkeypatch, [
            [article("old", 10), article("newest", 0)],
            [article("middle", 5)],
        ])
        assert [a["title"] for a in m.fetch_news(limit=2)] == ["newest", "middle"]

    def test_dedups_across_sources_by_first_60_chars(self, monkeypatch):
        self._patch_sources(monkeypatch, [[article("Bitcoin hits 100k")], [article("BITCOIN HITS 100K")]])
        assert len(m.fetch_news()) == 1

    def test_empty_when_all_sources_fail(self, monkeypatch):
        self._patch_sources(monkeypatch, [[], []])
        assert m.fetch_news() == []

    @pytest.mark.xfail(strict=True, reason="已知問題：去重 key 只用標題頭 60 字，兩篇唔同文章會被當成重複")
    def test_different_articles_with_same_prefix_kept(self, monkeypatch):
        prefix = "Bitcoin price analysis: what traders need to know this week ab"  # 61 chars
        self._patch_sources(monkeypatch, [[article(prefix + "out ETFs"), article(prefix + "out miners")]])
        assert len(m.fetch_news()) == 2


# ── fetch_btc_price ─────────────────────────────────────────────────────────

class TestFetchBtcPrice:
    def _patch(self, monkeypatch, handler):
        monkeypatch.setattr(m.requests, "get", lambda url, **kw: handler(url))

    def test_coingecko_success(self, monkeypatch):
        self._patch(monkeypatch, lambda url: response(200, b"{}", {"bitcoin": {"usd": 95000, "usd_24h_change": 1.5}}))
        assert m.fetch_btc_price() == {"price": 95000, "change_24h": 1.5, "source": "CoinGecko"}

    def test_falls_back_to_binance(self, monkeypatch):
        def handler(url):
            if "coingecko" in url:
                return response(500)
            return response(200, b"{}", {"lastPrice": "94000.5", "priceChangePercent": "-2.1"})
        self._patch(monkeypatch, handler)
        assert m.fetch_btc_price() == {"price": 94000.5, "change_24h": -2.1, "source": "Binance"}

    def test_falls_back_on_exception(self, monkeypatch):
        def handler(url):
            if "coingecko" in url:
                raise requests.exceptions.Timeout()
            return response(200, b"{}", {"lastPrice": "1", "priceChangePercent": "0"})
        self._patch(monkeypatch, handler)
        assert m.fetch_btc_price()["source"] == "Binance"

    def test_both_fail_returns_none(self, monkeypatch):
        self._patch(monkeypatch, lambda url: response(503))
        assert m.fetch_btc_price() is None

    @pytest.mark.xfail(strict=True, reason="已知問題：CoinGecko 回空資料時返回價格 0，冇 fallback 去 Binance")
    def test_coingecko_empty_payload_falls_back(self, monkeypatch):
        def handler(url):
            if "coingecko" in url:
                return response(200, b"{}", {})
            return response(200, b"{}", {"lastPrice": "94000", "priceChangePercent": "0"})
        self._patch(monkeypatch, handler)
        assert m.fetch_btc_price()["source"] == "Binance"


# ── format_full / format_brief ──────────────────────────────────────────────

class TestFormatting:
    def test_full_with_positive_price(self):
        out = m.format_full([article("Bitcoin up")], {"price": 95123.4, "change_24h": 2.345, "source": "CoinGecko"})
        assert "1. Bitcoin up" in out
        assert "$95,123 USD" in out
        assert "📈" in out and "+2.35%" in out

    def test_full_with_negative_price(self):
        out = m.format_full([article("Bitcoin down")], {"price": 90000, "change_24h": -1.2, "source": "Binance"})
        assert "📉" in out and "-1.20%" in out and "+" not in out.split("24h:")[1].split("\n")[0]

    def test_full_without_price_has_no_price_section(self):
        assert "BTC 價格快照" not in m.format_full([article("x bitcoin")], None)

    def test_full_includes_summary_and_link_when_present(self):
        a = article("Bitcoin up")
        a.update(summary="short summary", link="https://example.com/x")
        out = m.format_full([a], None)
        assert "📝 short summary" in out and "🔗 https://example.com/x" in out

    def test_brief_lists_source_and_title(self):
        out = m.format_brief([article("Bitcoin up", source="CoinDesk")])
        assert "1. [CoinDesk] Bitcoin up" in out


# ── send_telegram ───────────────────────────────────────────────────────────

class TestSendTelegram:
    def test_missing_env_returns_false(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        assert m.send_telegram("hi") is False

    def test_splits_into_4000_char_chunks(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        post = MagicMock(return_value=response(200))
        monkeypatch.setattr(m.requests, "post", post)
        assert m.send_telegram("x" * 8001) is True
        assert post.call_count == 3
        assert [len(c.kwargs["json"]["text"]) for c in post.call_args_list] == [4000, 4000, 1]
        assert post.call_args_list[0].kwargs["json"]["chat_id"] == "123"

    def test_api_failure_returns_false(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setattr(m.requests, "post", MagicMock(return_value=response(400, b"bad")))
        assert m.send_telegram("hi") is False


# ── main (CLI) ──────────────────────────────────────────────────────────────

class TestMain:
    def _run(self, monkeypatch, argv):
        monkeypatch.setattr(m.sys, "argv", ["fetch_btc_news.py", *argv])
        m.main()

    def test_no_articles_exits_1(self, monkeypatch):
        monkeypatch.setattr(m, "fetch_news", lambda limit: [])
        with pytest.raises(SystemExit) as exc:
            self._run(monkeypatch, [])
        assert exc.value.code == 1

    def test_brief_skips_price_and_telegram(self, monkeypatch):
        monkeypatch.setattr(m, "fetch_news", lambda limit: [article("Bitcoin up")])
        price, tg = MagicMock(), MagicMock()
        monkeypatch.setattr(m, "fetch_btc_price", price)
        monkeypatch.setattr(m, "send_telegram", tg)
        self._run(monkeypatch, ["--brief"])
        price.assert_not_called()
        tg.assert_not_called()

    def test_limit_and_telegram_flags(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(m, "fetch_news", lambda limit: seen.setdefault("limit", limit) and [article("Bitcoin")])
        monkeypatch.setattr(m, "fetch_btc_price", lambda: None)
        tg = MagicMock(return_value=True)
        monkeypatch.setattr(m, "send_telegram", tg)
        self._run(monkeypatch, ["--limit", "3", "--telegram"])
        assert seen["limit"] == 3
        tg.assert_called_once()
