# Paperdesk — TradingView Paper Trading & Intelligence

A self-hostable paper-trading terminal. TradingView alerts feed a FastAPI service, a relational database records events and simulated positions, and a responsive React UI shows P&L and event freshness. **Paper trading only:** this repository contains no broker API client and cannot place real orders.

## Implementation status

- Phase 1 — Foundation/auth: FastAPI, SQLAlchemy, PostgreSQL deployment config, SQLite local mode, Argon2 hashes, signed HTTP-only sessions, CSRF token, login throttling.
- Phase 2 — Webhook intake: separate secret, JSON validation, event audit, idempotency and unmatched/ambiguous event review.
- Phase 3 — Market price monitoring: Pine Script v6 file included; one alert per chart symbol is required.
- Phase 4 — Simulated trading: long/short, partial exits, SL/target updates, charges, MFE/MAE, price-triggered exits and audited manual close. The supplied ZERO POINT AUTOMATION script has a Paperdesk-compatible adapter in `pine/zero_point_automation_paperdesk.pine`.
- Phase 5 — UI/live state: overview, open positions, history, analytics, strategies, webhook monitor and settings; SSE refresh with polling fallback.
- Phase 6 — Analytics: stored-trade summary metrics and CSV export.
- Phase 7 — Docker Compose/Nginx and automated tests included. Production review, host account configuration, TLS issuance, and hosted end-to-end checks remain deployment tasks.

## Architecture

`TradingView → HTTPS POST → FastAPI validation/idempotency → SQLAlchemy transaction → paper engine → PostgreSQL → React dashboard (SSE refresh)`.

SQLite is the local default. Docker Compose uses PostgreSQL with a persistent volume. Tables are created on startup; a versioned Alembic migration chain is not included yet. Timestamps are stored in UTC; UI uses browser local time (set to Asia/Kolkata for India display).

## Local development

Requirements: Python 3.12+, Node.js 22+, npm. Create a local `.env` once, keep it private, and change every example secret before the first backend start. Admin bootstrap occurs only when the database has no users.

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
# Replace the example admin, session, webhook and database secrets; keep COOKIE_SECURE=false for local HTTP.
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python -m uvicorn main:app --reload --app-dir .\backend --port 8000 --env-file .env
```

In another terminal, run `cd frontend`, `npm install`, `npm run dev`, and visit `http://localhost:5173`. Sign in as `admin` unless `ADMIN_USERNAME` is set. Keep `papertrade.db` between restarts.

## Docker Compose

Set `ADMIN_PASSWORD`, `SESSION_SECRET`, `WEBHOOK_SECRET`, and `POSTGRES_PASSWORD` in `.env` to distinct random values, then run `docker compose up --build -d`. Inspect with `docker compose logs -f api`. Nginx exposes ports 80/443; the default config serves HTTP for local use. For public use set `COOKIE_SECURE=true`, update CORS origins, configure a real domain and obtain a certificate first. Then replace `nginx/default.conf` with `nginx/https.conf.example`, substituting your domain in all three `YOUR_DOMAIN` locations, and reload Nginx. Restrict firewall ingress to 80/443 and SSH from your IP.

Health check: `GET /health`. Authenticated APIs are under `/api`.

## TradingView setup

Follow the dedicated [TradingView setup and test guide](TRADINGVIEW_SETUP.md).

1. Enable TradingView 2FA; webhook alerts require it.
2. Store a high-entropy `WEBHOOK_SECRET` in `.env`, separate from login/session secrets. For local development, start FastAPI with `python -m uvicorn main:app --reload --app-dir .\backend --port 8000 --env-file .env`; keep the token private. TradingView delivery requires the API on a public HTTPS origin.
3. Alert webhook URL: `https://YOUR_HOST/api/webhook/tradingview`.
4. TradingView sends JSON in the request body and does not expose a custom-header field. This integration accepts a separate `secret` property matching `WEBHOOK_SECRET`; the app removes it before storing payloads. TradingView warns against putting credentials in alert messages, so use only a dedicated webhook token (never your login password), keep alerts private, and enable the webhook sender-IP allowlist in `nginx/https.conf.example` for deployment. The token is visible in TradingView alert messages/logs; rotate it if exposed.
5. For scripts without an inline price feed, add `pine/paperdesk_price_monitor.pine` to a 1-minute chart and enter the webhook token and `MARKET_PRICE_MONITOR` ID. Wait for its green `Paperdesk price feed · 1m` label. Create an alert for “Any alert() function call”, enable Webhook URL, and use the URL above. One alert monitors one symbol. The supplied `zero_point_automation_paperdesk.pine` sends realtime price snapshots every 30 seconds by default through its existing signal alert, and manages TP1–TP5 partial exits with a trailing stop.
6. Add `pine/paperdesk_signal_template.pine` for a safe schema demo; its MA signals are off by default. For your own strategy, copy the webhook input declarations and `f_number`/`f_id`/`f_base`/`f_entry`/`f_exit` helpers (optional `f_modify`) into its existing Pine script, then call them from its real signal conditions. Register the same strategy ID in Paperdesk. This template tracks one position at a time; pyramiding requires unique `trade_id` values per position.
7. For the supplied strategy, use `pine/zero_point_automation_paperdesk.pine` and follow the **ZERO POINT AUTOMATION strategy** subsection in `TRADINGVIEW_SETUP.md`. It uses the fixed paper quantity and sends a trade only after the purple confirmation candle.

Example entry:

```json
{"event":"ENTRY_LONG","event_id":"strategy-event-001","trade_id":"position-001","strategy":"my-strategy","symbol":"NASDAQ:AAPL","entry_price":190.25,"quantity":2,"stop_loss":185,"target":200,"timeframe":"1","timestamp":"2026-11-01T10:00:00Z","secret":"YOUR_WEBHOOK_SECRET"}
```

Example exit and price update:

```json
{"event":"EXIT_LONG","event_id":"strategy-event-002","trade_id":"position-001","strategy":"my-strategy","symbol":"NASDAQ:AAPL","exit_price":198,"exit_quantity":1,"secret":"YOUR_WEBHOOK_SECRET"}
{"event":"PRICE_UPDATE","event_id":"monitor-001","strategy":"MARKET_PRICE_MONITOR","symbol":"NASDAQ:AAPL","price":197.5,"timestamp":"2026-11-01T10:01:00Z","timeframe":"1","source":"TRADINGVIEW","secret":"YOUR_WEBHOOK_SECRET"}
```

Events: `ENTRY_LONG`, `ENTRY_SHORT`, `EXIT_LONG`, `EXIT_SHORT`, `STOP_LOSS`, `TARGET_HIT`, `MODIFY_SL`, `MODIFY_TARGET`, `CLOSE_POSITION`, `PRICE_UPDATE`. Pass `trade_id` on every exit/modification so alerts cannot attach to the wrong paper position. Use `exit_quantity` for partial exits and `price` for stop/target changes. Manual tests can POST to `/api/simulate` after login. For a local webhook test, load the `.env` secret in a second PowerShell window and send a `PRICE_UPDATE`; TradingView itself cannot reach `localhost` and accepts only public HTTP(S) ports 80/443. Example local webhook check (run in another PowerShell window after starting the API with `--env-file .env`):

```powershell
$cfg = Get-Content .env -Raw | ConvertFrom-StringData
$payload = @{ event='PRICE_UPDATE'; event_id="local-test-$([guid]::NewGuid())"; strategy='MARKET_PRICE_MONITOR'; symbol='NASDAQ:AAPL'; price=190.25; timeframe='1'; source='TRADINGVIEW'; secret=$cfg.WEBHOOK_SECRET } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/webhook/tradingview' -Method Post -ContentType 'application/json' -Body $payload
```

This checks the local webhook contract only; TradingView cannot deliver to localhost. Use a public HTTPS deployment to test end-to-end delivery.

### TradingView alert limits and freshness

TradingView currently lists Plus with up to 100 technical alerts; Pine price-monitor and signal-script alerts share that quota. Plus alerts expire after two months. A regular alert stops after more than 15 triggers within three minutes. The monitor sends at most one per minute per chart, but high-frequency signal scripts can hit that cap. Created alerts store a snapshot of the script, its inputs and chart; delete and recreate alerts after edits. Limits can change, so verify the [current plan details](https://www.tradingview.com/pricing/).

The price monitor will not emit unless the chart interval is one minute and a 24+ character webhook token is configured. It sends confirmed bar-close prices while bars are available. Closed markets, missing bars, expired alerts and webhook delivery failures create gaps. TradingView requires 2FA for webhooks, accepts ports 80/443, may fail delivery, and cancels requests taking over three seconds. Its [webhook requirements](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/) list sender IPs allowlisted in the sample HTTPS Nginx config; update them if TradingView changes its list. Pine alert behavior and limits are in its [official FAQ](https://www.tradingview.com/pine-script-docs/faq/alerts/). Prices older than three minutes display stale. One-minute data cannot guarantee intrabar stop/target execution or exchange fills.

## Free deployment

For always-on compute with persistent storage, Oracle Cloud Infrastructure Always Free is the closest fit. Install Docker on an eligible Linux VM, clone this repository, configure `.env`, issue certificates, and start Compose. Current official [Always Free resource limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) include up to two E2.1.Micro VMs or an Ampere A1 monthly allowance equivalent to 2 OCPUs/12 GB RAM. Home-region capacity and account eligibility vary; verify the tenancy cost estimate and quota before provisioning.

Allow inbound 80/443 and restricted SSH, configure DNS if using a domain, install Docker Compose and Certbot, configure the real certificate path in Nginx, and run `docker compose up -d --build`. Back up PostgreSQL off-host regularly. This repository cannot provision cloud resources or a TLS certificate for you.

[Render's free services](https://render.com/docs/free) sleep after 15 minutes idle, have ephemeral app storage, and free Postgres expires after 30 days without backups; that works for demos, but not 24/7 service with durable history. GitHub hosts source code, not the running backend. Push to a private repository and never commit `.env`, database files or webhook secrets.

## Security checklist

- Use distinct, high-entropy admin, session, DB and webhook secrets; never commit `.env`.
- Set `COOKIE_SECURE=true` behind HTTPS; retain HTTP-only/SameSite session cookie and CSRF header.
- Rotate an exposed webhook secret; keep scripts/alerts private.
- Restrict firewall and SSH; update OS/images; monitor service and webhook failures.
- Back up PostgreSQL off-host and test restore; a compose volume is not a backup.
- Restrict dashboard access and review failed/unmatched events and audit records.
- Login rate limiting is in-memory and resets on restart; add a shared proxy limiter before multi-worker public use.
- Event processing is currently inline in a DB transaction; monitor webhook latency against TradingView's timeout. No durable background queue yet.
- No broker credentials or real-order adapters exist.

## Troubleshooting

- Webhook `401`: match body `secret` to `WEBHOOK_SECRET` (or use `X-Webhook-Secret` for manual clients).
- Webhook `422`: check supported event, positive prices/quantities and `trade_id` on ambiguous exits.
- No prices: check chart interval, “Any alert() function call”, webhook URL, alert status/quota and symbol trading hours.
- Admin password changed in env but login fails: bootstrap only runs on empty DB; use old password or intentionally reset DB (which deletes records).
- Compose errors: `docker compose logs api db`; verify required environment variables.

## Tests

```powershell
python -m pip install -r backend\requirements.txt
$env:PYTHONPATH='backend'
pytest backend\test_main.py -q
cd frontend; npm install; npm run build
```

Tests cover auth, webhook auth/idempotency, long/short calculations, price-triggered target closure, partial exit, SL modification and reports. Hosted HTTPS, live TradingView delivery, browser UX, and PostgreSQL restart recovery still need environment-backed verification.

