# Phased implementation plan

1. **Foundation/auth** — completed: FastAPI, SQLAlchemy, PostgreSQL deployment configuration, SQLite local mode, Argon2, signed HTTP-only sessions, CSRF token, login throttling, health endpoint.
2. **Webhook intake** — completed: event schema validation, separate secret, payload secret redaction, event logging, idempotency including duplicate race handling, unmatched-event errors.
3. **Market monitor and signal bridge** — completed in code: Pine Script v6 one-minute price monitor, status guard, configurable interval, event payload IDs, and a disabled-by-default long/short signal template. TradingView Pine Editor compile and live delivery still require account-side setup.
4. **Paper engine** — completed: long/short, partial exits, stop/target changes, incoming-price triggers, charge assumptions, gross/net and unrealized P&L, MFE/MAE, manual admin closure audit.
5. **UI** — completed: responsive dark terminal, dashboard, open/closed trade pages, analytics, strategy listing/add, webhook monitor, settings, SSE refresh and polling fallback.
6. **Reports** — initial release: realized metrics grouped by strategy and CSV export. Date filters, equity curve, drawdown, consecutive win/loss and Excel export are outstanding.
7. **Hardening/deployment** — partially completed: tests, Docker Compose, HTTP Nginx, HTTPS example, deployment/security guidance. Hosted deployment, TLS provisioning, migration framework, durable worker queue and multi-worker rate limiting remain outstanding.

The product is paper-only and has no broker integration or real-order path.
