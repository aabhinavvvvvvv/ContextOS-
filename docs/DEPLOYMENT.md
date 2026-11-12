# Deployment

ContextOS runs as five containers from `docker-compose.prod.yml`:

```
Internet ──► web (Caddy) :80/:443 ──► static React app (/srv)
                  │  /api/*  (prefix stripped)
                  ▼
                 api (FastAPI, runs migrations on start) ──► postgres (pgvector)   [no public port]
                 worker (SAQ: ingestion, memory, connector sync) ──► redis          [no public port]
                 volumes: postgres_data, redis_data, app_data (/data: uploads, model cache), caddy_data (certificates)
```

## Run it anywhere Docker runs

```bash
cp .env.example .env          # set POSTGRES_PASSWORD, GEMINI_API_KEY, API_TOKEN (and GITHUB_TOKEN if wanted)
docker compose -f docker-compose.prod.yml up -d --build
open http://localhost:8080     # WEB_PORT, default 8080
```

First start: the API applies migrations, and the worker downloads the embedding model (~65 MB) into the `app_data` volume once.

## On a server with a domain (HTTPS)

1. Create a small Linux VM, install Docker, open ports 80 and 443 in its firewall, nothing else.
2. Point a DNS `A` record (e.g. `contextos.example.com`) at the server's IP.
3. In `.env`: `SITE_ADDRESS=contextos.example.com`, `WEB_PORT=80`, `WEB_TLS_PORT=443`, a strong `API_TOKEN` (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
4. `docker compose -f docker-compose.prod.yml up -d --build`. Caddy obtains a Let's Encrypt certificate and renews it by itself.
5. Open the site, then **Settings**, and paste the API token.

## Hosting options and cost

| Option | Cost | Notes |
|---|---|---|
| One small VPS running this compose file (e.g. Hetzner CX22, Oracle Cloud Always Free ARM) | ~€4/month, or free (Oracle, subject to availability) | Simplest; everything on one machine; you handle backups |
| Containers on a PaaS (Render, Fly.io, Railway) + managed Postgres with pgvector (Neon, Supabase) + managed Redis (Upstash) | free tiers exist, with sleep/limits; ~$10–25/month for always-on | Less ops work; three providers to configure; free tiers may sleep or cap usage |
| LLM | Gemini free tier: $0 within its rate limits | `GET /metrics` estimates what paid usage would cost |

The embedding model runs on CPU in the worker and API (~200 MB RAM), so 1–2 GB RAM is enough for personal use.

## Configuration

All configuration is environment variables (`.env`, never committed; see `.env.example`). Production-relevant ones:

| Variable | Why |
|---|---|
| `POSTGRES_PASSWORD` | database password (long, random) |
| `API_TOKEN` | **required when exposed**: every route except `/health` needs `Authorization: Bearer <token>` |
| `GEMINI_API_KEY` | answers and memory extraction |
| `GITHUB_TOKEN` | optional: higher GitHub rate limit, needed to open issues |
| `SITE_ADDRESS`, `WEB_PORT`, `WEB_TLS_PORT` | domain and ports for Caddy |
| `ALLOWED_ACTIONS` | which action kinds may be proposed and approved |
| `LOG_LEVEL` | `INFO` by default |

## Operations

- **Health:** `/api/health` (process up) and `/api/health/ready` (database reachable; used by the container health check).
- **Metrics:** the Metrics page or `GET /api/metrics` (LLM calls, errors, p50/p95 latency, cost estimate, recent errors).
- **Logs:** `docker compose -f docker-compose.prod.yml logs -f api worker`.
- **Backups:** the database is the source of truth (documents' files are in `app_data`). Nightly, e.g. from cron:
  `docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U contextos contextos | gzip > backup-$(date +%F).sql.gz`, plus a copy of the `app_data` volume. Test a restore once.
- **Updates:** `git pull && docker compose -f docker-compose.prod.yml up -d --build` (migrations run on API start).

## CI/CD

- **CI** (`.github/workflows/ci.yml`): on every push, the backend tests run against real Postgres + Redis service containers, and the frontend is linted, type-checked and built. Live LLM tests are excluded, so CI needs no API key.
- **CD** (not automated, on purpose for a personal project): deploy with the update command above. To automate later: a workflow that builds images, pushes them to GitHub Container Registry, and SSHes to the server to pull and restart.

## Security checklist

- `API_TOKEN` set; only ports 80/443 open; Postgres and Redis have no published ports.
- Secrets only in `.env` on the server (or the platform's secret store), never in git; the repository's pre-push scan checks history for `.env` values.
- Containers run as a non-root user (API/worker).
- Consequential actions need approval in the app; `ALLOWED_ACTIONS` can disable kinds entirely.
- The folder connector can only read inside `FOLDER_ROOT` (`/data/sources` in the container).
- Production notes not implemented (single-user scope): multiple users and roles, rate limiting per client, secret rotation, centralised log shipping and alerting.
