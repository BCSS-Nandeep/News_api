# Blura News API — Deployment Guide

Operational guide for installing, configuring and running the Blura News API.

| | |
|---|---|
| **Service** | Blura News API (`api.main:app`) |
| **Stack** | Python · FastAPI · Uvicorn |
| **Datastore** | None — in-process in-memory cache only |
| **Auth** | None at the application layer |
| **Default port** | `8000` |

---

## 1. What this service is

A read-only HTTP API that discovers, scrapes and normalizes news articles on demand:

```
DISCOVER  ->  SCRAPE  ->  NORMALIZE  ->  FILTER  ->  RETURN
```

There is **no database and no ingestion pipeline to provision**. When a request
arrives, the service selects matching sources from the registry
(`News_URLs.json`), scrapes them live, and caches the results in memory for
`CACHE_TTL_SECONDS`. A restart starts cold; articles are re-discovered lazily.

Two consequences shape the whole deployment, and both are covered in detail below:

1. The service makes **heavy outbound HTTP requests** — it needs egress, not just ingress.
2. The cache is **per-process**, which constrains the worker count (see §6).

---

## 2. Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | Validated on 3.14.6. No version ceiling in `requirements.txt`. |
| `pip` / `venv` | Standard library `venv` is sufficient. |
| Outbound HTTPS (443) | **Mandatory.** The service scrapes ~365 external news sites. Without egress it returns empty results, not errors. |
| Outbound DNS | Required for the same reason. |
| RAM | 512 MB minimum, 1 GB recommended. Scales with cache size, not request volume. |
| Disk | < 100 MB. Nothing is written at runtime. |

Dependencies are **minimum-pinned, not exact-pinned** (`fastapi>=0.115`,
`pydantic>=2.9`, …). This is deliberate: `pydantic`'s compiled Rust core has no
prebuilt wheel the moment a new Python release ships, and an exact old pin then
forces a from-source build requiring a Rust/MSVC toolchain. If your environment
requires reproducible builds, generate a lockfile at deploy time
(`pip freeze > requirements.lock.txt`) rather than tightening `requirements.txt`.

---

## 3. Install

### Linux / macOS

```bash
git clone <repository-url> blura-engine
cd blura-engine

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
git clone <repository-url> blura-engine
cd blura-engine

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Verify the install

```bash
python -m pytest -q
```

Expected: **201 passed**. The suite is fully offline — it uses a fixture
registry and never touches the network, so it is safe to run in CI and on a
locked-down build host.

---

## 4. Configuration

All configuration is via environment variables. There is no config file, and
**no variable is required** — every one has a working default.

| Variable | Default | Read by | Purpose |
|---|---|---|---|
| `PORT` | `8000` | [main.py:14](main.py#L14) | Listen port. **Only honoured when started via `python main.py`** — if you launch `uvicorn` directly, pass `--port`. |
| `CACHE_TTL_SECONDS` | `600` | [services/cache.py:14](services/cache.py#L14) | How long scraped articles stay fresh. Higher = faster responses, staler news. |
| `MAX_SOURCES_PER_REQUEST` | `40` | [services/news_service.py:32](services/news_service.py#L32) | Safety cap on sources scraped per request. Directly bounds worst-case latency. |
| `DISCOVERY_MAX_WORKERS` | `10` | [services/news_service.py:33](services/news_service.py#L33) | Thread-pool size for concurrent source scraping. |

### Tuning guidance

- **`MAX_SOURCES_PER_REQUEST` is your latency control.** An unfiltered request
  scrapes up to this many sources. Lower it to tighten the worst case; raise it
  only if clients always send narrow filters.
- **`DISCOVERY_MAX_WORKERS`** trades latency for outbound connections and CPU.
  Roughly, cold latency is about
  `(MAX_SOURCES_PER_REQUEST / DISCOVERY_MAX_WORKERS) × per-source time`.
- **`CACHE_TTL_SECONDS`** at the default 600 s (10 min) is appropriate for news.
  Raising it to 1800 s materially reduces outbound traffic if freshness permits.

Set them as you would any environment variable, e.g. in a systemd unit (§7) or a
container environment:

```bash
export CACHE_TTL_SECONDS=900
export MAX_SOURCES_PER_REQUEST=25
```

---

## 5. Running the service

### Development

```bash
uvicorn api.main:app --reload
```

Serves on `http://127.0.0.1:8000` with autoreload. Do not use `--reload` in production.

### Production

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Or via the entry point, which reads `PORT`:

```bash
python main.py
```

Both are equivalent; `main.py` simply calls `uvicorn.run('api.main:app', …)` with
`reload=False`.

### Confirm it is up

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## 6. Worker count — read before scaling

> **Run a single application worker.** Do not use `--workers N` or a
> multi-process manager such as Gunicorn without first reading this section.

The article cache ([services/cache.py](services/cache.py)) is a module-level
dict guarded by a `threading.Lock` — it lives in **one process's memory**. With
multiple workers:

- Each worker holds a **separate, independent cache**. Cache hit rate falls and
  outbound scraping multiplies by the worker count.
- `GET /news/articles/{article_id}` becomes **unreliable**. It can only find
  articles in *its own* worker's cache, so the same id returns `200` or `404`
  depending on which worker the load balancer picked.

The service is already concurrent *within* one process: scraping runs on a
`ThreadPoolExecutor` (`DISCOVERY_MAX_WORKERS`) and the workload is
network-bound, not CPU-bound, so a single Uvicorn worker uses the hardware well.

**To scale beyond one process**, the cache must first be moved to a shared store
(Redis or equivalent). That is a code change in `services/cache.py`, not a
deployment flag — treat horizontal scaling as a development task rather than an
ops one.

### The background worker has the same constraint

[background_worker.py](background_worker.py) is an **optional** cache
pre-warmer. Be aware of how it actually behaves:

- Run as its own process (`python background_worker.py`), it warms **its own
  memory, not the API's**. The API process sees no benefit.
- It is therefore only useful if it runs **inside** the API process, or once the
  cache is externalised to a shared store.

The API is fully functional without it — sources are scraped lazily on first
request. **Recommendation: do not deploy it** in its current form; it would
consume outbound bandwidth scraping all 365 active sources every 10 minutes with
no effect on API response times. Use `--once` for manual smoke-testing only:

```bash
python background_worker.py --once
```

---

## 7. Running as a managed service

### systemd (Linux)

`/etc/systemd/system/blura-news-api.service`:

```ini
[Unit]
Description=Blura News API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=blura
Group=blura
WorkingDirectory=/opt/blura-engine
Environment="CACHE_TTL_SECONDS=600"
Environment="MAX_SOURCES_PER_REQUEST=40"
Environment="DISCOVERY_MAX_WORKERS=10"
ExecStart=/opt/blura-engine/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now blura-news-api
sudo systemctl status blura-news-api
sudo journalctl -u blura-news-api -f
```

### Docker

No Dockerfile ships with the repository. This one reflects the constraints above:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000 \
    CACHE_TTL_SECONDS=600 \
    MAX_SOURCES_PER_REQUEST=40 \
    DISCOVERY_MAX_WORKERS=10

EXPOSE 8000

# Single worker — see the worker-count constraint in DEPLOYMENT.md section 6.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t blura-news-api .
docker run -d --name blura-news-api -p 8000:8000 \
  -e CACHE_TTL_SECONDS=900 \
  blura-news-api
```

A restart empties the cache, so prefer restarting during low-traffic windows.
The container needs unrestricted outbound HTTPS.

---

## 8. Reverse proxy

Two things matter here: **generous timeouts** and **buffering turned off**.

Cold requests are slow by nature — the service is scraping live websites.
Measured on this codebase: **~15 s for a single cold source**, **~0.01 s once
cached**. An unfiltered cold request fans out to `MAX_SOURCES_PER_REQUEST`
sources across `DISCOVERY_MAX_WORKERS` threads, so expect the worst case to
reach **60–90 s**. A default 60 s proxy timeout will cut those requests off.

### nginx

```nginx
server {
    listen 80;
    server_name news-api.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Cold scrapes legitimately take a long time. See section 8.
        proxy_connect_timeout 15s;
        proxy_send_timeout   120s;
        proxy_read_timeout   120s;

        proxy_buffering off;
    }
}
```

Terminate TLS at the proxy (certbot or your platform's certificate manager). The
application speaks plain HTTP only.

### Load balancer health check

Point it at **`GET /health`** — it returns `{"status":"ok"}` immediately and
performs no scraping. Never health-check `/news/articles`; a cold check would
trigger a full scrape and time out.

---

## 9. Security posture

These are properties of the service as it stands today, not defects introduced
by deployment. Raise them with whoever owns the hosting environment.

| Concern | Current state | Recommended action |
|---|---|---|
| **Authentication** | None. Every endpoint is open. | Do not expose publicly. Restrict to internal networks, or put an API gateway / proxy-level auth in front. |
| **Rate limiting** | None. | Apply at the proxy. Each uncached request can trigger dozens of outbound scrapes, so this doubles as protection for *your* egress. |
| **CORS** | **No CORS middleware is configured.** | Browser clients on a different origin will be blocked. See [INTEGRATION.md](INTEGRATION.md) §10 — it needs a small code change. |
| **Inbound surface** | Read-only `GET` endpoints; no writes, no user input reaching a datastore. | — |
| **Interactive docs** | `/docs` and `/redoc` are enabled. | Consider blocking at the proxy if the API is internet-facing. |
| **Outbound requests** | Fetches URLs listed in `News_URLs.json`. | Treat the registry as trusted configuration and review changes to it. |

---

## 10. Monitoring

| Signal | How |
|---|---|
| Liveness | `GET /health` |
| Logs | Uvicorn's stdout/stderr — `journalctl -u blura-news-api` or `docker logs`. |
| Latency | Expect a bimodal distribution: sub-100 ms cache hits and multi-second cold scrapes. Alert on the cold-path p99, not the mean. |
| Unhandled errors | Returned as `500` with `{"detail": "<ExceptionType>: <message>"}` by the global handler in [api/main.py:37](api/main.py#L37). Any occurrence is worth investigating — per-source scrape failures are already swallowed upstream and never reach it. |
| Memory | Should plateau. The cache is bounded by active sources × articles each, with entries overwritten on refresh. Steady growth indicates a leak worth reporting. |

There are no metrics endpoints, structured logs, or tracing hooks. Adding them
is a code change.

---

## 11. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| All requests return `{"count":0,…}` | No outbound internet access, or DNS blocked. | From the host: `curl -I https://www.thehindu.com`. Check egress rules and proxy environment variables. |
| First request very slow, later ones instant | Normal — cold cache, then cache hits. | None. Lower `MAX_SOURCES_PER_REQUEST` if the cold path must be faster. |
| `504` from the proxy | Proxy read timeout shorter than a cold scrape. | Raise `proxy_read_timeout` to 120 s (§8). |
| `/news/articles/{id}` returns `404` for an id just received | Cache entry expired, process restarted, or multiple workers are running. | Re-request `/news/articles` to re-warm, then look the id up. Confirm single-worker (§6). |
| Browser client gets a CORS error | No CORS middleware (§9). | See [INTEGRATION.md](INTEGRATION.md) §10. |
| Results are stale | `CACHE_TTL_SECONDS` too high. | Lower it; restart to clear the cache immediately. |
| `pip install` tries to compile `pydantic-core` | Python release newer than the available wheels. | Use Python 3.12 for the most stable wheel coverage, or install a Rust toolchain. |
| A specific source never yields articles | Site changed layout, blocks scrapers, or renders body content in JavaScript. | Expected and tolerated by design — one dead source never fails a request. Set `"active": false` in `News_URLs.json` to stop scraping it. |

---

## 12. Pre-deployment checklist

- [ ] Python 3.11+ present; virtualenv created and `requirements.txt` installed
- [ ] `python -m pytest -q` → **201 passed**
- [ ] Outbound HTTPS and DNS verified **from the deployment host**
- [ ] Environment variables set, or defaults accepted deliberately
- [ ] Started with a **single** worker (§6)
- [ ] `background_worker.py` **not** deployed as a separate process (§6)
- [ ] `GET /health` responds through the proxy
- [ ] Proxy read timeout ≥ 120 s; buffering off (§8)
- [ ] Load-balancer health check points at `/health`, not `/news/articles`
- [ ] Access restricted — no authentication exists in the application (§9)
- [ ] CORS decision made if any browser client is on a different origin (§9)
- [ ] Process supervision in place (`Restart=always` or a container restart policy)

---

## 13. Repository layout

```
Blura-Engine/
├── main.py                   # Entry point — reads PORT, runs uvicorn
├── api/
│   ├── main.py               # FastAPI app, router wiring, global error handler
│   ├── schemas.py            # Pydantic response models
│   └── routes/
│       ├── health.py         # GET /health
│       ├── sources.py        # GET /news/sources
│       └── articles.py       # GET /news/articles, /news/articles/{id}
├── services/
│   ├── news_service.py       # Orchestrates discover -> scrape -> filter -> paginate
│   └── cache.py              # In-memory TTL cache (per-process — see section 6)
├── scraper/
│   ├── sources_registry.py   # Loads News_URLs.json; filter semantics
│   ├── discovery.py          # RSS -> sitemap -> Google News -> homepage fallbacks
│   ├── extraction.py         # Article body/image/metadata extraction
│   └── normalize.py          # Article ids, dedupe
├── processing/
│   ├── keywords.py           # Keyword matching
│   ├── location.py           # District/location resolution
│   └── location_data.py      # Location reference data
├── static/index.html         # Built-in dashboard, served at GET /
├── tests/                    # 201 offline tests
├── News_URLs.json            # Source registry — 377 entries, 365 active
├── background_worker.py      # Optional pre-warmer (see section 6 before using)
└── requirements.txt
```

---

## 14. Related documentation

- **[INTEGRATION.md](INTEGRATION.md)** — endpoints, query parameters, response
  structures and client examples for consuming this API.
- **Interactive API reference** — `http://<host>:<port>/docs` (Swagger UI),
  `/redoc` (ReDoc), `/openapi.json` (raw OpenAPI schema).
