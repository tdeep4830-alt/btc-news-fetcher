# CONTEXT — btc-news-fetcher

> 每份 brief 都必須附上呢份文件。Agent 唔可以假設呢度冇寫嘅嘢；唔清楚就喺 issue 問 Toby。

## 1. 呢個 project 係乜

從多個 RSS 來源抓取 Bitcoin 相關新聞，去重、按時間排序，加上 BTC 現價，整理成文字摘要，經 Telegram 推送。
屬於 Toby 嘅 Hermes Agent 財經助手（Financial Assistant）skill 組合之一。

**呢個 project 嘅角色**：AI Develop Team 嘅第一個試行 project。目的係測試成條開發流程，唔係加新功能。

## 2. 試行範圍

| 喺範圍內 | 唔喺範圍內 |
| --- | --- |
| 重構：拆模組、移走全域狀態、加 type hints | 新功能（新來源、AI 摘要、網頁 dashboard） |
| 補測試、修 `tests/` 入面標咗 xfail 嘅已知問題 | 改輸出格式（Telegram 訊息要同而家一樣） |
| 錯誤處理同 logging 改善 | 改 CLI 參數（`--telegram`、`--brief`、`--limit` 要保留） |

任何超出範圍嘅改動，要先開 issue 等 Toby 批。

## 3. 點樣運行

```bash
python3 src/fetch_btc_news.py               # 完整摘要（新聞 + 價格），印出 stdout
python3 src/fetch_btc_news.py --brief       # 只列標題，唔攞價格
python3 src/fetch_btc_news.py --limit 5     # 最多 5 篇（預設 10）
python3 src/fetch_btc_news.py --telegram    # 同時推送 Telegram
```

- 搵唔到任何新聞時 exit code = 1。
- 由 Hermes cron 定時觸發，時間對齊香港時間。
- 呢個 script 本身唔會叫任何 LLM：抓取、過濾、格式化全部由 Python 做。

## 4. 架構

```
NEWS_SOURCES（8 個 RSS）
  → fetch_feed()：requests 抓 bytes → feedparser 解析 → is_btc_related() 過濾 → 去重
  → fetch_news()：合併 → 再去重 → 按 published 倒序 → 取頭 N 篇
  → fetch_btc_price()：CoinGecko，失敗就 fallback 去 Binance
  → format_full() / format_brief()
  → send_telegram()：每 4000 字一段分開發送
```

**過濾規則**（`is_btc_related`）：
1. `btc_focused: True` 嘅來源（Bitcoin Magazine、Google News 英文/中文）全部收錄。
2. 其他來源：標題有 `BTC_KEYWORDS_TITLE` 就收錄。
3. 否則 summary 或 Atom content 有 `BTC_KEYWORDS_BODY` 都收錄。

## 5. 歷史：已經解決過嘅問題（唔好倒退返去）

| 問題 | 解決方法 | 唔准做 |
| --- | --- | --- |
| `feedparser.parse(url)` 直連被 CDN 封 | 用 `requests` 帶瀏覽器 headers 先抓 bytes，再交 `feedparser.parse(content)` | 改返用 feedparser 直接抓 URL |
| 關鍵字過嚴，漏咗好多文章 | 加 `btc_focused` 來源旗標，標題關鍵字優先 | 只靠 summary 判斷 |
| 偶發失敗 | 每個來源最多 retry 2 次；403 直接放棄，429 會等待 | 冇上限地 retry |

## 6. 已知問題（`tests/` 入面嘅 xfail）

| 問題 | 對應 test |
| --- | --- |
| 關鍵字用 substring 比對：`lightning` 會中天氣新聞；`crypto` 令非 BTC 嘅幣圈新聞都收錄 | `test_lightning_weather_news_rejected` |
| 最後一次 429 之後仲 sleep，嘥 10 秒 | `test_no_sleep_after_final_429` |
| Header 聲明接受 `br`，但冇裝 `brotli`；伺服器回 brotli 時 requests 解唔到 | `test_accept_encoding_matches_installed_decoders` |
| 去重 key 只用標題頭 60 字，前綴相同嘅唔同文章會被當成重複 | `test_different_articles_with_same_prefix_kept` |
| CoinGecko 回空資料時返回價格 0，唔會 fallback 去 Binance | `test_coingecko_empty_payload_falls_back` |

其他已觀察到、但未有 test 嘅問題（修之前要先寫 test）：
- 全域 `cache_title_set`：`fetch_feed()` 同 `fetch_news()` 各自做一次去重，重複；而且狀態跨 call 保留。
- 冇日期嘅文章會用「而家」做時間，所以會排喺最前。
- `format_full()` 用 `datetime.now()`，跟 VPS 時區，唔一定係香港時間。
- 同一篇文章喺唔同來源嘅標題通常唔一樣，按標題去重捉唔到；應考慮用 link 去重。

## 7. 環境同依賴

- Python 3.10 或以上（用咗 `dict | None` 語法）。
- 依賴：`feedparser`、`requests`、`python-dotenv`；測試用 `pytest`（見 `requirements.txt`）。
- Secrets 由 `~/.hermes/config/btc_news.env` 讀：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`。
  - **永遠唔可以**將 secrets 寫入 repo、brief、log 或 issue。`.env.example` 只列變數名。
- 外部服務：8 個 RSS 來源、CoinGecko、Binance 公開 API、Telegram Bot API。全部唔需要 API key（Telegram 除外）。

## 8. 部署

- 經 `deploy_skills.py` 部署去 Hermes skills 目錄。
- **部署 production 一定要 Toby 批准**，Coder 同 QA 唔可以部署。

## 9. Toby 嘅硬約束

- 審批關：PRD、ticket 拆分、production 部署，全部要 Toby 批，Orchestrator 唔可以代批。
- Build 同 audit 一定喺唔同 profile 做。
- 同一張 ticket 失敗 2 次就停低，上報 Toby。
- 唔准用 `/tmp`，唔准 `ps` / `kill`。
- 測試一定要 mock 網絡；`pytest` 唔可以真係連 RSS 或 Telegram。
- 每個 commit message 都要有 ticket ID，例如 `T-003: 移走全域 cache_title_set`。

## 10. 未確認嘅問題（等 Toby 答）

- [ ] Hermes cron 實際幾點觸發？用邊個 profile 行？
- [ ] 部署目標係 host 嘅 skills 目錄定 Docker container？container 入面 `HERMES_HOME=/opt/data`，同 script 寫死嘅 `~/.hermes/config` 唔一致。
- [ ] 對應嘅 `SKILL.md` 喺邊？要唔要一齊搬入呢個 repo？
- [ ] 非 BTC 嘅幣圈新聞（例如 ETH）應唔應該收錄？呢個決定會影響過濾規則點修。