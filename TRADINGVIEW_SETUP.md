# TradingView integration setup

This connects TradingView alerts to Paperdesk. It is paper-only: TradingView sends signals and market prices; Paperdesk records simulated trades and never places broker orders.

## 1. Prepare the webhook secret

- Set a dedicated high-entropy `WEBHOOK_SECRET` in the ignored `.env` file. Do not reuse your login password or `SESSION_SECRET`.
- Start the backend with `python -m uvicorn main:app --reload --app-dir .\backend --port 8000 --env-file .env` for local development.
- Enter the same webhook token in the Pine input. Do not paste it into chat. The token is sent in each alert body because the TradingView alert UI does not provide custom headers; it appears in your TradingView alert configuration/logs. The app strips it before saving event payloads. TradingView warns against putting credentials into webhook bodies, so this token must be dedicated to the webhook, and the production Nginx sample adds a sender-IP allowlist.

## 2. Test locally before TradingView

TradingView cannot reach `localhost`. First test the app contract with a local request from a second PowerShell window:

```powershell
$cfg = Get-Content .env -Raw | ConvertFrom-StringData
$payload = @{ event='PRICE_UPDATE'; event_id="local-test-$([guid]::NewGuid())"; strategy='MARKET_PRICE_MONITOR'; symbol='NASDAQ:AAPL'; price=190.25; timeframe='1'; source='TRADINGVIEW'; secret=$cfg.WEBHOOK_SECRET } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/webhook/tradingview' -Method Post -ContentType 'application/json' -Body $payload
```

The response should report `PROCESSED`; the dashboard's Market Data card should show the symbol. This verifies the local API only, not TradingView delivery.

## 3. Configure the one-minute price monitor

1. Deploy Paperdesk to a public HTTPS URL. Configure `WEBHOOK_SECRET` on the host. Do not enter an HTTP or localhost URL in TradingView.
2. In TradingView Pine Editor, open [paperdesk_price_monitor.pine](pine/paperdesk_price_monitor.pine), paste it into a new script, save, and add it to a chart.
3. Use the 1-minute chart timeframe. In script settings enter the webhook token and `MARKET_PRICE_MONITOR`. The status badge should be green.
4. Create an alert. Select the price monitor script and **Any alert() function call**. Enable **Webhook URL** and set `https://YOUR_HOST/api/webhook/tradingview`.
5. Confirm the alert is active in Alert Manager. Wait for a bar close, then check Alert Log delivery status and Paperdesk's Webhook Monitor/Market Data.
6. Repeat for each required symbol: one Pine alert watches one chart symbol. Edit the interval input if desired, but the chart still needs to be one minute.

The script reads the real chart series `close`, symbol, timeframe and bar close timestamp. It sends at most one event per configured interval and only on confirmed bars. It cannot create bars outside the symbol's session or recover failed deliveries. A stale price is marked stale in Paperdesk; it is never fabricated.

## 4. Connect a signal strategy

The starter [paperdesk_signal_template.pine](pine/paperdesk_signal_template.pine) is an indicator with MA demo signals disabled by default. It demonstrates JSON for `ENTRY_LONG`, `ENTRY_SHORT`, and linked `EXIT_LONG`/`EXIT_SHORT` events. Enable its demo only for a paper test.

To adapt an existing script, copy the template's `f_base`, `f_entry`, `f_exit`, and `f_modify` helpers into it. From the actual strategy conditions call `alert(f_entry("ENTRY_LONG", uniqueTradeId, entryPrice, stopPrice, targetPrice), alert.freq_all)` or the matching event helper. Use a stable unique `trade_id` for the entry and all exits/changes. For pyramiding, generate a distinct `trade_id` per position; the starter template intentionally tracks one position only. A script alert is not active until you create it in TradingView, and edits do not change an existing alert's saved script/input snapshot—recreate the alert after edits.

### ZERO POINT AUTOMATION strategy

The adapted copy [zero_point_automation_paperdesk.pine](pine/zero_point_automation_paperdesk.pine) preserves the supplied indicator and replaces only its final webhook block. It sends Paperdesk's flat JSON schema using strategy ID `ZERO_POINT_AUTOMATION`. Configure the alert using the supplied copy; the original attachment is untouched.

1. In Pine Editor, open the adapted file, save it as a private script, and add it to the chart for the exact instrument you want recorded. The underlying's CE/PE strike hint is informational: use an option-contract chart if the trade should be recorded at option-premium prices.
2. In the script settings, enter the dedicated `WEBHOOK_SECRET` (at least 24 characters). Set the script's paper quantity to the quantity you want recorded. This is a fixed paper size; it does not calculate a risk-based size.
3. Set the chart to **1 minute**. In Paperdesk Webhook settings, **Send frequent market prices** is enabled by default with a 30-second interval. The script sends realtime `PRICE_UPDATE` snapshots through the same **Any alert() function call** alert as entries/exits. The interval is configurable from 20 to 120 seconds. A one-second cadence is not reliable: TradingView stops regular alerts after more than 15 triggers in three minutes. The script can only send while TradingView receives live chart updates.
4. Create an alert for this script with **Any alert() function call**, the same HTTPS webhook URL above, and an appropriate expiry. Recreate both existing NIFTY and global alerts after updating the script or its inputs because TradingView saves an alert snapshot. Apply the updated script to each symbol/chart whose prices you want tracked.
5. An entry is sent at the confirmed purple candle close, after the EMA crossover setup candle. It includes TP1–TP5, calculated from the confirmation entry and the saved signal-candle ATR distance. Paperdesk closes five equal quantity slices as the price feed reaches each target. After TP1, the stop moves to entry; after each later target, the stop moves to the previous target. TP5 closes the remaining quantity. Stop/exit events carry the same `trade_id`.
6. Register strategy ID `ZERO_POINT_AUTOMATION` in Paperdesk's Strategies view if you want it listed there. Confirm `PRICE_UPDATE` events under Webhook Monitor and verify the Market Data panel lists the symbol. The event log keeps only the latest price snapshot per symbol, while the market data table stores the current price.

The strategy can emit at most one tracked paper position at a time. Paperdesk allocates 20% of the initial paper quantity to each of TP1–TP5; the last target closes any remaining quantity. No alert places a broker order. Price updates are sampled from TradingView realtime chart updates, so a fast spike and reversal between snapshots can be missed.

For the supplied ZERO POINT script, its existing alert includes the realtime price feed and staged target/stop management. For other scripts that do not send `PRICE_UPDATE`, keep the standalone price-monitor alert active for the same symbol. Legacy entries with only one `target` continue to close at that target. Alert-driven close events and price monitoring may observe the same terminal close; the backend treats a later close for the same completed trade ID as already handled.

Supported event names: `ENTRY_LONG`, `ENTRY_SHORT`, `EXIT_LONG`, `EXIT_SHORT`, `STOP_LOSS`, `TARGET_HIT`, `MODIFY_SL`, `MODIFY_TARGET`, `CLOSE_POSITION`, `PRICE_UPDATE`. Include JSON `secret`, `event_id`, `strategy`, `symbol`, `timeframe`, and `timestamp`. Include `trade_id` for each exit/modification; use `exit_quantity` for partial exits and `price` for stop/target modifications.

The webhook receiver also normalizes legacy TradingView messages wrapped in a top-level `payload` object. For those messages it maps `ENTRY` plus `side` to `ENTRY_LONG`/`ENTRY_SHORT`, maps `EXIT` plus `order_id`/`fill_comment` stop or target reasons, and accepts `fill_price` and `event_time` aliases. New scripts should use the flat schema above.

## 5. Limits, delivery and security

- TradingView Plus currently lists up to 100 active technical alerts. Price monitor and Pine signal alerts share that technical-alert quota. Check current [TradingView plan limits](https://www.tradingview.com/pricing/) before deciding the number of symbols/strategies.
- A regular alert stops if it fires more than 15 times in three minutes. The one-minute monitor alone stays below that limit; a noisy signal script may not. See the official [Pine alerts FAQ](https://www.tradingview.com/pine-script-docs/faq/alerts/).
- Webhook alerts require 2FA, must use HTTPS on port 443 (TradingView also accepts port 80), and can fail. Requests taking over three seconds are cancelled. Check the official [webhook requirements](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/) and Alert Manager status.
- `nginx/https.conf.example` restricts the production webhook route to sender IPs currently listed by TradingView. Recheck the official list if delivery starts returning 403. If DNS is proxied through another service, configure the allowlist where the actual client IP is visible.
- The one-minute close is not a tick feed. Gaps, within-bar movement, latency and alert expiry mean Paperdesk cannot guarantee stop/target prices or represent exchange execution.
- Never use a broker URL, API key, account password, or real-order alert message. This receiver accepts paper event data only.
