# Deployment guide

## Render free demo and webhook smoke test

The root `render.yaml` Blueprint deploys the API and built React dashboard as one HTTPS web service and provisions a free Postgres database. This gives TradingView a stable `https://<service>.onrender.com/api/webhook/tradingview` address while the Render service exists. The Blueprint generates distinct admin, session, and webhook secrets; view them in Render's environment settings and use only `WEBHOOK_SECRET` in the TradingView Pine input. Do not commit `.env` or paste secrets into chat.

1. Put this project in a GitHub, GitLab, or Bitbucket repository that you control. Ensure `.env`, database files, and credentials are excluded; `.gitignore` and `.dockerignore` already exclude them.
2. In Render, choose **New → Blueprint**, connect the repository, and select its `render.yaml`. Review that both services use the Free plan and create them.
3. Wait for the database and web service to finish creating. Open the service's `https://<service>.onrender.com/health` address; expect `status: ok` and `database: ok`.
4. Set the generated `ADMIN_PASSWORD` to a private password you control, and copy the generated `WEBHOOK_SECRET` directly from Render's environment settings. The admin user is created on the first database initialization; later password changes in Render do not reset an existing account.
5. Sign in at the service URL. In TradingView, use the Paperdesk Pine script, set its webhook input to the Render `WEBHOOK_SECRET`, and create/recreate an **Any alert() function call** alert with webhook URL `https://<service>.onrender.com/api/webhook/tradingview`.
6. Send a test alert or wait for a strategy event, then check **Webhook monitor** for the event. The health endpoint does not expose secret values.

Render free web services sleep after 15 minutes without inbound traffic, take about a minute to wake, and use an ephemeral filesystem. Free Postgres expires after 30 days and has no backups. Therefore this option is for testing only: a TradingView webhook sent while the service is asleep may exceed TradingView's three-second delivery window, and the free database is temporary. Do not rely on it for unattended trading or durable records. See Render's [free instance limits](https://render.com/docs/free) and [FastAPI deploy guide](https://render.com/docs/deploy-fastapi).

## Persistent free VM option: Oracle Cloud Always Free

1. Create an Oracle Cloud account and select an eligible Linux VM in the tenancy home region. Review the current Always Free quotas, capacity and estimated charges in the console before creation. The current Always Free page lists up to two E2.1.Micro machines or an Ampere A1 allowance equivalent to 2 OCPUs and 12 GB RAM. Home-region capacity and eligibility vary.
2. Create a public IP, allow TCP 80/443, and restrict SSH ingress to your IP. Point a domain A record to the VM. HTTPS issuance needs a domain that resolves to the VM.
3. Install Docker Engine and Compose, clone this private repository, create `.env` with distinct random `ADMIN_PASSWORD`, `SESSION_SECRET`, `WEBHOOK_SECRET`, and `POSTGRES_PASSWORD`, and set `COOKIE_SECURE=true`.
4. Create `certbot/conf` and `certbot/www`. Start the database, API, web and HTTP proxy with `docker compose up --build -d` (the default proxy config serves HTTP for initial setup).
5. Install Certbot on the VM and issue a webroot certificate: `sudo certbot certonly --webroot -w ./certbot/www -d YOUR_DOMAIN`. Keep TCP 80 open so HTTP challenge renewal can run.
6. Copy `nginx/https.conf.example` over `nginx/default.conf`; replace every `YOUR_DOMAIN` with the actual DNS name. The webhook route allows TradingView published sender IPs; review that official list before use. Reload using `docker compose exec proxy nginx -s reload`. Verify `https://YOUR_DOMAIN/health`; leave `COOKIE_SECURE=true`.
7. Configure a systemd timer/cron task to renew Certbot certificates and reload Nginx after renewal (`certbot renew --deploy-hook "docker compose exec -T proxy nginx -s reload"`). Confirm renewal with Certbot's dry-run mode.
8. Back up Postgres off the VM regularly using `pg_dump` and test restoration. The named volume protects against container replacement, not VM loss. Review OS, Docker and image updates; verify account cost and quota dashboards.

The official [Always Free resource limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) are subject to account policy, regional capacity, and change. No cloud resources or certificates are provisioned by this repository.

## Why not Render Free for 24/7?

Render says free web services spin down after 15 idle minutes and lose local filesystem changes; free PostgreSQL expires after 30 days and does not provide backups. It is suited to a demo rather than durable unattended webhook processing.

## Local Compose and recovery

`docker compose up --build -d` runs Postgres, API, frontend and HTTP Nginx. Configure `.env` first. Local HTTP requires `COOKIE_SECURE=false`; never expose that setting publicly. Back up before code changes. There is no schema migration framework in this first version, so database schema upgrades require an explicit backup and migration plan.
