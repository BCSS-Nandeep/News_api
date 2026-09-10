# Blura News API — Integration Guide

How to consume the Blura News API from another application. Every endpoint,
parameter, response structure and status code below was verified against the
running service.

**Contents**

1. [Quick start](#1-quick-start)
2. [Conventions](#2-conventions)
3. [Endpoint summary](#3-endpoint-summary)
4. [`GET /health`](#4-get-health)
5. [`GET /news/sources`](#5-get-newssources)
6. [`GET /news/articles`](#6-get-newsarticles)
7. [`GET /news/articles/{article_id}`](#7-get-newsarticlesarticle_id)
8. [Filter semantics](#8-filter-semantics)
9. [Error responses](#9-error-responses)
10. [CORS — read before integrating a browser app](#10-cors--read-before-integrating-a-browser-app)
11. [Performance and timeouts](#11-performance-and-timeouts)
12. [Integration notes and gotchas](#12-integration-notes-and-gotchas)
13. [Client examples](#13-client-examples)
14. [Reference: available filter values](#14-reference-available-filter-values)
15. [Related documentation](#15-related-documentation)

---

## 1. Quick start

```bash
# Is it up?
curl "http://localhost:8000/health"

# What sources exist for a country + language?
curl "http://localhost:8000/news/sources?country=India&language=Telugu"

# Fetch articles
curl "http://localhost:8000/news/articles?country=India&language=English&limit=10"
```

A typical client does three things:

1. Calls `GET /news/sources` **once at startup** to populate filter controls
   (country / language / state / source lists). Never hardcode these values —
   they are derived from the server's registry.
2. Calls `GET /news/articles` with the user's selected filters plus `limit` /
   `offset`.
3. Renders `articles[]`, using `count` to drive pagination.

---

## 2. Conventions

| | |
|---|---|
| **Base URL** | `http://<host>:<port>` — e.g. `http://localhost:8000` |
| **Protocol** | HTTP/1.1. TLS is terminated by a reverse proxy, if configured. |
| **Methods** | `GET` only. The API is entirely read-only. |
| **Authentication** | **None.** No API key, token or header is required or accepted. |
| **Request body** | Never used. All input is query parameters. |
| **Response type** | `application/json` |
| **Character encoding** | UTF-8. Non-Latin scripts (Telugu, Hindi, Tamil, …) are returned as-is. |
| **Timestamps** | ISO 8601, UTC, `Z` suffix — e.g. `2026-09-10T09:04:42Z` |
| **Rate limits** | None enforced by the application. |
| **Versioning** | Unversioned paths. App version `1.0.0` is reported in `/openapi.json`. |

**Interactive reference:** `/docs` (Swagger UI), `/redoc` (ReDoc), and
`/openapi.json` (machine-readable schema) are all live on a running instance.
`/openapi.json` is the best input for generating a typed client.

---

## 3. Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/news/sources` | List active news sources (drives filter UIs) |
| `GET` | `/news/articles` | **Primary endpoint** — filtered, paginated articles |
| `GET` | `/news/articles/{article_id}` | Single article by id (cache-only) |
| `GET` | `/` | Built-in HTML dashboard (not part of the JSON API) |

There are deliberately **no** `/search`, `/location`, `/state`, `/language` or
`/country` sub-routes. Every filter is an optional query parameter on
`/news/articles`.

---

## 4. `GET /health`

Liveness probe. Performs no scraping and always responds immediately — this is
the endpoint to use for load-balancer and uptime checks.

**Parameters:** none.

**Response `200`**

```json
{
  "status": "ok"
}
```

| Field | Type | Description |
|---|---|---|
| `status` | `string` | Always `"ok"` when the process is serving. |

---

## 5. `GET /news/sources`

Returns the active source registry. Use it to build filter controls — country,
language, state and website lists should all be derived from this response
rather than hardcoded in your client.

### Query parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `country` | `string` | — | Comma-separated. e.g. `India,United States` |
| `state` | `string` | — | Comma-separated. e.g. `Telangana,Tamil Nadu` |
| `language` | `string` | — | Comma-separated. e.g. `English,Telugu` |
| `source` | `string` | — | Registry ids or name substrings. e.g. `cnn,bbc_news` |

All are optional. Values within one parameter are ORed; separate parameters are
ANDed. See [§8](#8-filter-semantics).

### Response `200` — `SourceOut[]`

Note this endpoint returns a **bare JSON array**, not a wrapped object.

```json
[
  {
    "id": "eenadu",
    "name": "Eenadu",
    "region": "South India",
    "country": "India",
    "state": "Andhra Pradesh & Telangana",
    "language": "Telugu",
    "type": "newspaper",
    "base_url": "https://www.eenadu.net/",
    "active": true
  },
  {
    "id": "sakshi",
    "name": "Sakshi",
    "region": "South India",
    "country": "India",
    "state": "Andhra Pradesh & Telangana",
    "language": "Telugu",
    "type": "newspaper",
    "base_url": "https://www.sakshi.com/",
    "active": true
  }
]
```

### Field reference

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Stable registry identifier. **Use this for the `source` filter** — names can change. |
| `name` | `string` | Human-readable outlet name, e.g. `"The Hindu"`. |
| `region` | `string` | Broad region, e.g. `"South India"`, `"Europe"`. 16 distinct values. |
| `country` | `string` | Curated country, e.g. `"India"`. Also `"International"` for global agencies. |
| `state` | `string` | Indian state coverage. **Empty string for non-Indian sources.** May be compound, e.g. `"Andhra Pradesh & Telangana"`, `"Pan-India"`. |
| `language` | `string` | Publication language, e.g. `"Telugu"`. |
| `type` | `string` | One of `newspaper`, `tv_news`, `digital_news`, `news_agency`. |
| `base_url` | `string` | Outlet homepage. |
| `active` | `boolean` | Always `true` in this response — inactive sources are filtered out. |

This endpoint reads from the registry file only and **never scrapes**, so it
responds in milliseconds regardless of cache state.

---

## 6. `GET /news/articles`

The primary endpoint. Selects matching sources, scrapes them (or serves them
from cache), filters, sorts newest-first, and paginates.

### Query parameters

| Parameter | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `keyword` | `string` | — | — | Free-text match against title, summary and body. e.g. `drugs`, `corruption`, `accident`. **Single value — not comma-split.** |
| `country` | `string` | — | — | Comma-separated. e.g. `India,United States` |
| `language` | `string` | — | — | Comma-separated. e.g. `English,Telugu` |
| `state` | `string` | — | — | Comma-separated. e.g. `Telangana,Andhra Pradesh` |
| `district` | `string` | — | — | Comma-separated. e.g. `Hyderabad,Karimnagar` |
| `location` | `string` | — | — | Comma-separated. Loose match against the article's detected location, district **and** state. |
| `source` | `string` | — | — | Comma-separated registry ids (preferred) or name substrings. e.g. `cnn,bbc_news` |
| `limit` | `integer` | `20` | `1`–`100` | Page size. Outside the range → `422`. |
| `offset` | `integer` | `0` | `≥ 0` | Records to skip. Negative → `422`. |

All filters are optional; an omitted filter never restricts results.

> **`country`, `language`, `state` and `source` also narrow which sources get
> scraped** before any network activity happens. `keyword`, `district` and
> `location` are applied afterwards, to already-scraped articles. Sending at
> least one registry-backed filter therefore makes a request dramatically
> faster — see [§11](#11-performance-and-timeouts).

### Example request

```
GET /news/articles?country=India&language=English&state=Telangana&limit=10&offset=0
```

### Response `200` — `ArticleListResponse`

```json
{
  "count": 24,
  "limit": 2,
  "offset": 0,
  "articles": [
    {
      "id": "6d1fd198f66f2eb9",
      "title": "SMU and LSE say universities and global academic culture should prioritise purpose and people",
      "content": "SMU-LSE Global Forum examines what it will take for universities to fulfil their responsibility toward societal impact in a fractured world …",
      "summary": "SMU and LSE say universities and global academic culture should prioritise purpose and people",
      "source": "The Hindu",
      "source_id": "the_hindu",
      "source_url": "https://www.thehindu.com/brandhub/pr-release/smu-and-lse-say-universities-and-global-academic-culture-should-prioritise-purpose-and-people/article71450923.ece",
      "language": "English",
      "country": "India",
      "state": "Pan-India",
      "district": "",
      "location": "",
      "published_at": "2026-09-10T09:04:42Z",
      "image_url": "https://th-i.thgim.com/public/incoming/6a050d/article71450922.ece/alternates/LANDSCAPE_1200/Singapore-Management-University-Logo.jpg"
    }
  ]
}
```

### Envelope fields

| Field | Type | Description |
|---|---|---|
| `count` | `integer` | **Total articles matching the filters**, before pagination. Use this for page counts — not `articles.length`. |
| `limit` | `integer` | The `limit` that was applied (echoed back). |
| `offset` | `integer` | The `offset` that was applied (echoed back). |
| `articles` | `ArticleOut[]` | This page of results, sorted by `published_at` descending. |

### `ArticleOut` field reference

Every field is always present. String fields may be empty (`""`) but are
**never `null`**, so a client can render without null checks.

| Field | Type | Description |
|---|---|---|
| `id` | `string` | 16-character hex id derived from the article URL. Stable for the same URL across requests. Used by `/news/articles/{article_id}`. |
| `title` | `string` | Headline. Never empty — articles without a title are dropped server-side. |
| `content` | `string` | **Full extracted body text**, plain text with no HTML. Can be long — 15,000+ characters is normal. Never empty; bodyless pages are dropped server-side. See the payload-size note in [§12](#12-integration-notes-and-gotchas). |
| `summary` | `string` | Short description from the feed or the page's meta description. **Falls back to the title** when neither exists, so it is sometimes identical to `title`. |
| `source` | `string` | Human-readable outlet name, e.g. `"The Hindu"`. Matches `SourceOut.name`. |
| `source_id` | `string` | Registry id, e.g. `"the_hindu"`. Matches `SourceOut.id` — use this to join back to the sources list. |
| `source_url` | `string` | Canonical URL of the original article. Link to this for "read more". |
| `language` | `string` | Taken from the source registry, not detected from text. |
| `country` | `string` | Taken from the source registry — curated per source, never guessed from article text. |
| `state` | `string` | Indian state. Derived from the source's registry value and the article text. **Empty for non-Indian sources.** |
| `district` | `string` | Detected district. **Often empty** — see the detection-coverage note below. |
| `location` | `string` | Detected place name. **Often empty** — see below. |
| `published_at` | `string` | ISO 8601 UTC, e.g. `"2026-09-10T09:04:42Z"`. Always populated; falls back to fetch time when the source publishes no date. |
| `image_url` | `string` | Lead image URL, always `https://` — see the note below. **Empty string when no image was found**; there is no placeholder image, so your UI must handle a card with no image. |

> **`district` and `location` coverage is narrow.** Location detection is
> Telangana-focused by design, so these fields are empty for most national and
> all international articles. Do not build a required UI element or a mandatory
> filter around them. `state` and `country` are registry-backed and far more
> reliable.

> **`published_at` has a fallback.** When a source exposes no usable date, the
> service stamps the fetch time rather than dropping the article. Treat the
> timestamp as "best available", not as a guaranteed publication time.

> **`image_url` is always `https://`.** Many feeds still publish `http://` image
> links, and a browser on an https page blocks those outright as mixed content.
> The API upgrades the scheme so the URL is usable directly in an `<img>` tag on
> a TLS-served page. The image is still hosted by the news outlet and is not
> proxied by this API, so an occasional link will 404 or block hotlinking —
> attach an `onerror` handler and hide the element rather than showing a broken
> image.

---

## 7. `GET /news/articles/{article_id}`

Looks up a single article by the `id` from a previous `/news/articles` response.

| Parameter | In | Type | Description |
|---|---|---|---|
| `article_id` | path | `string` | The `id` field from an `ArticleOut`. |

**Response `200`** — a single `ArticleOut` object (same shape as §6, unwrapped).

**Response `404`**

```json
{
  "detail": "Article not found (not in cache — it may have expired or never been scraped)"
}
```

> **This endpoint only searches the warm in-memory cache.** No database backs
> it. An id resolves only while the source that produced it is still cached
> (`CACHE_TTL_SECONDS`, default 10 minutes) and the API process has not
> restarted.
>
> **Practical guidance: do not rely on this endpoint for deep links or
> permalinks.** If a user bookmarks a detail page and returns an hour later, the
> lookup will `404`. Either keep the full article object client-side after
> listing it — every field you need is already in the list response, so a second
> call is usually unnecessary — or persist articles in your own datastore.

---

## 8. Filter semantics

Understanding this is the key to using the API correctly.

### OR within a filter, AND across filters

Values inside one parameter are alternatives; separate parameters must all hold.

```
?country=India,United States&language=English,Telugu&state=Telangana
```

resolves to:

```
(country = India OR United States)
  AND (language = English OR Telugu)
  AND (state = Telangana)
```

### Matching is case-insensitive substring, not exact equality

This is deliberate, because registry values are free text. `state=Telangana`
matches both `"Telangana"` and `"Andhra Pradesh & Telangana"`; `country=United`
matches both `"United States"` and `"United Kingdom"`.

**Implication:** pass full values from `/news/sources` rather than abbreviations,
or you will get broader results than intended.

### Comma is the separator

Send `?country=India,United States`. Whitespace around values is trimmed, and
empty selections are discarded — `?country=`, `?country=India,,` and
`?country=India, ` all behave sensibly. Remember to URL-encode: a space becomes
`%20`, and the comma may be sent literally or as `%2C`.

> **Do not use repeated parameters.** `?country=Japan&country=India` does **not**
> mean "Japan OR India" — the API keeps only the **last** value and silently
> discards the rest. Verified: that request returns 170 sources (India alone),
> not the 174 that `?country=India,Japan` returns.
>
> There is no error and no warning, so this fails quietly and is easy to miss in
> review. Always join multi-select values with commas into a **single**
> parameter. If you build query strings with a helper that appends one entry per
> selected checkbox, it will produce the repeated form by default — collapse the
> values first (see the `add()` helper in [§13](#13-client-examples)).

### An omitted filter does not restrict

Sending no filters returns articles from across the registry, capped by the
server's `MAX_SOURCES_PER_REQUEST` (default 40 sources). This is the slowest
possible request — see [§11](#11-performance-and-timeouts).

### `source` accepts ids or names

`source=the_hindu` matches by exact registry id. `source=hindu` matches by name
substring. **Prefer ids** — they are stable and unambiguous.

---

## 9. Error responses

All errors return JSON with a `detail` key. FastAPI's conventions apply.

| Status | When | Body |
|---|---|---|
| `200` | Success — **including zero matches** | Normal payload, `count: 0` |
| `404` | Unknown article id | `{"detail": "Article not found (not in cache — it may have expired or never been scraped)"}` |
| `404` | Unknown path | `{"detail": "Not Found"}` |
| `422` | Parameter failed validation | Structured validation array — see below |
| `500` | Unhandled server error | `{"detail": "<ExceptionType>: <message>"}` |

### `422` — validation error

Returned when `limit` or `offset` is out of range. Example, for `limit=500`:

```json
{
  "detail": [
    {
      "type": "less_than_equal",
      "loc": ["query", "limit"],
      "msg": "Input should be less than or equal to 100",
      "input": "500",
      "ctx": { "le": 100 }
    }
  ]
}
```

Note that `detail` is an **array of objects** here, while for `404`/`500` it is a
**string**. Handle both shapes if you surface `detail` to users.

### Important: no-match is `200`, never `404`

An unknown source, an impossible filter combination, or simply no recent news
all return `200` with an empty list:

```json
{ "count": 0, "limit": 20, "offset": 0, "articles": [] }
```

**Your client must treat an empty `articles` array as a normal outcome**, not an
error. This is also what you get when the server has no outbound internet
access, so an empty result is not by itself proof that no news exists.

### Per-source failures are invisible

If a news site is down, blocks the scraper, or times out, it silently
contributes nothing. One dead source never fails the request and is not reported
in the response. A lower-than-expected `count` may reflect a partial scrape.

---

## 10. CORS — read before integrating a browser app

> **The API does not currently send CORS headers.** No `CORSMiddleware` is
> registered in [api/main.py](api/main.py). The bundled dashboard works only
> because it is served from the API's own origin at `GET /`.

**Any browser application on a different origin will be blocked by the browser**
— a React app on `http://localhost:3000` calling `http://localhost:8000` will
fail, even though the API itself responds `200`.

This is **not** affected by anything you do client-side. `fetch` options, axios
configuration and proxy headers cannot fix it. One of the following is required:

**Option A — enable CORS on the API (recommended).** A four-line change in
[api/main.py](api/main.py):

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=['https://soceye.example.com'],  # list real origins, not ['*']
    allow_methods=['GET'],
    allow_headers=['*'],
)
```

Prefer an explicit origin list over `['*']`, and drive it from an environment
variable so each environment can differ.

**Option B — same-origin reverse proxy.** Serve the API under a path on the same
origin as the frontend (e.g. nginx routing `/api/` to the API), so the browser
never makes a cross-origin request.

**Option C — call from your backend.** Server-to-server requests are unaffected
by CORS. This is the right choice if you also want to add caching, auth or
response trimming between the API and your users.

Server-side consumers (Python, Node, Java, mobile native) are **not affected at
all** — CORS is a browser mechanism only.

---

## 11. Performance and timeouts

The API scrapes live websites, so response times are **bimodal** — and the
difference is large. Measured against this codebase:

| Scenario | Measured |
|---|---|
| Single source, cold cache | **~15 s** |
| Single source, warm cache | **~0.01 s** |
| `/health`, `/news/sources` | Milliseconds — never scrape |

Scraped articles are cached per source for `CACHE_TTL_SECONDS` (default 600 s),
so the first request for a given source pays the cost and subsequent requests
are effectively instant until the entry expires.

An unfiltered request fans out to up to 40 sources across 10 threads, which puts
the cold worst case in the **60–90 s** range.

### What this means for your client

1. **Set a generous HTTP timeout — 120 s.** Default client timeouts are far too
   short: `axios` has none but browsers cap around 300 s, Python `requests` has
   no default, and many HTTP clients and API gateways default to 30 s. A 30 s
   timeout will fail on cold requests that would otherwise have succeeded.
2. **Always send at least one of `country`, `language`, `state` or `source`.**
   These narrow the source set *before* scraping. Unfiltered requests are the
   slow path.
3. **Never block a page render on a cold request.** Show a loading state and
   tell the user this may take up to a minute. Consider a background fetch at
   app startup to warm the cache for your common filter combinations.
4. **Cache on your side too** if you serve many users. The server's cache is
   per-process and in-memory; it is cleared by any restart.
5. **Expect slow-then-fast.** Do not interpret a fast second request as a
   different code path — it is the cache.

### Pagination caveat

`offset` paginates a list that is **rebuilt on every request**. If a cache entry
expires between your call for page 1 and page 2, the underlying set can change
and an article may be duplicated or skipped across page boundaries.

For stable pagination, request a larger `limit` once (up to 100) and paginate
client-side, rather than walking `offset` across many calls.

---

## 12. Integration notes and gotchas

A consolidated list of everything that commonly surprises a first integration.

| # | Note |
|---|---|
| 1 | **No authentication exists.** Do not send an `Authorization` header — it is ignored. If your platform requires auth, add it at a gateway. |
| 2 | **No CORS headers.** Browser apps on another origin are blocked. See [§10](#10-cors--read-before-integrating-a-browser-app). |
| 3 | **`/news/sources` returns a bare array**; `/news/articles` returns a wrapped object. Different shapes — don't write one parser for both. |
| 4 | **`count` is the pre-pagination total**, not `articles.length`. Use it for page counts. |
| 5 | **Empty results are `200`, not `404`.** Handle `count: 0` as normal. |
| 6 | **`422` `detail` is an array; `404`/`500` `detail` is a string.** |
| 7 | **`content` is large.** Full article bodies routinely exceed 15 KB, so `limit=100` can produce a multi-megabyte response. Request smaller pages for list views, and strip `content` in your own API layer if you proxy this one. |
| 8 | **`image_url` can be empty**, and is always `https://` when present. There is no placeholder. Design cards to work without an image, and handle `onerror` — images are hosted by the outlet, not proxied. |
| 9 | **`summary` may duplicate `title`.** It falls back to the title when no description exists — de-duplicate before rendering both. |
| 10 | **`district`, `location` and `state` are often empty**, especially outside India. Never make them required. |
| 11 | **Filter matching is substring-based.** `country=United` matches both the US and the UK. Send full values from `/news/sources`. |
| 12 | **`keyword` is not comma-split.** Unlike the other filters, it is treated as a single phrase. |
| 13 | **Repeated query parameters silently lose values.** `?country=A&country=B` keeps only `B`. Always join multi-select values with commas into one parameter. See [§8](#8-filter-semantics). |
| 14 | **Article ids are URL-derived and cache-scoped.** Stable for the same URL, but resolvable via `/news/articles/{id}` only while cached. Don't build permalinks on them. |
| 15 | **Cold requests take up to ~90 s.** Use a 120 s timeout. |
| 16 | **Drive filter UIs from `/news/sources`.** Hardcoded country/language lists will drift from the registry. |
| 17 | **Silent per-source failures.** A low `count` may mean partial scrape success, not an absence of news. |
| 18 | **Generate your client from `/openapi.json`** for compile-time-accurate types. |

---

## 13. Client examples

### JavaScript — `fetch`

```javascript
const API_BASE = 'http://localhost:8000';

async function getArticles({
  country, language, state, district, location, source, keyword,
  limit = 20, offset = 0,
} = {}) {
  const params = new URLSearchParams();

  // Arrays are joined with commas; empty selections are omitted entirely.
  const add = (key, value) => {
    if (!value || (Array.isArray(value) && value.length === 0)) return;
    params.set(key, Array.isArray(value) ? value.join(',') : value);
  };

  add('country', country);
  add('language', language);
  add('state', state);
  add('district', district);
  add('location', location);
  add('source', source);
  add('keyword', keyword);
  params.set('limit', String(limit));
  params.set('offset', String(offset));

  // Cold scrapes can take ~90s — give the request real headroom.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120_000);

  try {
    const res = await fetch(`${API_BASE}/news/articles?${params}`, {
      signal: controller.signal,
    });

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      // 422 sends an array of validation objects; 404/500 send a string.
      const detail = Array.isArray(body.detail)
        ? body.detail.map(d => d.msg).join('; ')
        : body.detail ?? res.statusText;
      throw new Error(`${res.status}: ${detail}`);
    }

    return await res.json();   // { count, limit, offset, articles }
  } finally {
    clearTimeout(timer);
  }
}

// Usage — note count drives pagination, not articles.length.
const { count, articles } = await getArticles({
  country: ['India', 'United States'],
  language: ['English'],
  limit: 10,
});
console.log(`${articles.length} of ${count} total`);
```

### React — filters from the registry

```jsx
import { useEffect, useState } from 'react';

const API_BASE = 'http://localhost:8000';

export function NewsFeed() {
  const [countries, setCountries] = useState([]);
  const [selected, setSelected] = useState(['India']);
  const [articles, setArticles] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  // Build filter options from the registry — never hardcode them.
  useEffect(() => {
    fetch(`${API_BASE}/news/sources`)
      .then(r => r.json())
      .then(sources => {
        setCountries([...new Set(sources.map(s => s.country))].sort());
      })
      .catch(console.error);
  }, []);

  useEffect(() => {
    if (selected.length === 0) return;
    setLoading(true);

    const params = new URLSearchParams({
      country: selected.join(','),
      limit: '20',
    });

    fetch(`${API_BASE}/news/articles?${params}`)
      .then(r => r.json())
      .then(data => {
        setArticles(data.articles);
        setTotal(data.count);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [selected]);

  return (
    <div>
      {loading && <p>Fetching live news — this can take up to a minute…</p>}

      {!loading && articles.length === 0 && (
        <p>No articles matched these filters.</p>
      )}

      <p>{articles.length} of {total}</p>

      {articles.map(a => (
        <article key={a.id}>
          {/* image_url can be an empty string — guard it. The outlet hosts the
              image, so hide it on error rather than showing a broken icon. */}
          {a.image_url && (
            <img
              src={a.image_url}
              alt=""
              loading="lazy"
              onError={e => { e.currentTarget.style.display = 'none'; }}
            />
          )}
          <h3><a href={a.source_url} target="_blank" rel="noreferrer">{a.title}</a></h3>
          <p>
            {a.source} · {a.language}
            {a.district && ` · ${a.district}`}
            {' · '}{new Date(a.published_at).toLocaleString()}
          </p>
          {/* summary falls back to title — avoid printing it twice. */}
          {a.summary !== a.title && <p>{a.summary}</p>}
        </article>
      ))}
    </div>
  );
}
```

### Python — `requests`

```python
from typing import Iterable, Optional, Union

import requests

API_BASE = 'http://localhost:8000'
TIMEOUT = 120  # cold scrapes can take ~90s

Filter = Optional[Union[str, Iterable[str]]]


def _csv(value: Filter) -> Optional[str]:
    if not value:
        return None
    return value if isinstance(value, str) else ','.join(value)


def get_articles(
    keyword: Optional[str] = None,
    country: Filter = None,
    language: Filter = None,
    state: Filter = None,
    district: Filter = None,
    location: Filter = None,
    source: Filter = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Returns {'count': int, 'limit': int, 'offset': int, 'articles': [...]}."""
    params = {
        'keyword': keyword,
        'country': _csv(country),
        'language': _csv(language),
        'state': _csv(state),
        'district': _csv(district),
        'location': _csv(location),
        'source': _csv(source),
        'limit': limit,
        'offset': offset,
    }
    # requests drops None values for us, so unset filters are simply not sent.
    resp = requests.get(f'{API_BASE}/news/articles', params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_sources(country: Filter = None, language: Filter = None) -> list:
    resp = requests.get(
        f'{API_BASE}/news/sources',
        params={'country': _csv(country), 'language': _csv(language)},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == '__main__':
    data = get_articles(country=['India'], language=['Telugu'], limit=5)
    print(f"{len(data['articles'])} of {data['count']} total")
    for a in data['articles']:
        print(f"[{a['published_at']}] {a['source']}: {a['title']}")
```

### cURL

```bash
BASE=http://localhost:8000

# Health
curl "$BASE/health"

# Sources for a country
curl "$BASE/news/sources?country=India"

# Multi-select: (India OR US) AND English
curl --get "$BASE/news/articles" \
  --data-urlencode "country=India,United States" \
  --data-urlencode "language=English" \
  --data-urlencode "limit=10" \
  --max-time 120

# Keyword search within one state
curl --get "$BASE/news/articles" \
  --data-urlencode "keyword=corruption" \
  --data-urlencode "state=Telangana" \
  --max-time 120

# Specific outlets by registry id
curl --get "$BASE/news/articles" \
  --data-urlencode "source=the_hindu,cnn" \
  --max-time 120
```

---

## 14. Reference: available filter values

Current registry contents — **377 sources, 365 active**. Treat this as a
snapshot; query `/news/sources` for live values.

### `country` — 53 values

```
Argentina, Australia, Bangladesh, Belgium, Brazil, Canada, Chile, China,
Colombia, Denmark, Egypt, Finland, France, Germany, Ghana, Hong Kong, India,
Indonesia, International, Ireland, Israel, Italy, Jamaica, Japan, Kenya,
Malaysia, Mexico, Netherlands, New Zealand, Nigeria, Norway, Pakistan, Panama,
Philippines, Poland, Qatar, Russia, Saudi Arabia, Singapore, South Africa,
South Korea, Spain, Sri Lanka, Sweden, Switzerland, Taiwan, Thailand,
Trinidad and Tobago, Turkiye, Ukraine, United Arab Emirates, United Kingdom,
United States
```

India dominates with 170 active sources, followed by the United States (12),
Australia (9), the United Kingdom (8) and Canada (8). `International` (6) covers
global agencies such as wire services.

### `language` — 13 values

| Language | Active sources |
|---|---|
| English | 216 |
| Hindi | 38 |
| Telugu | 17 |
| Malayalam | 14 |
| Tamil | 14 |
| Kannada | 12 |
| Marathi | 12 |
| Bengali | 12 |
| Gujarati | 10 |
| Odia | 10 |
| Assamese | 5 |
| Punjabi | 4 |
| Urdu | 1 |

### `state` — 24 values (Indian sources only)

Non-Indian sources carry an empty `state`. Several values are compound, which is
exactly why matching is substring-based — `state=Telangana` matches both
`"Telangana"` and `"Andhra Pradesh & Telangana"`.

```
Andhra Pradesh, Andhra Pradesh & Telangana, Assam, Bihar, Chhattisgarh,
Gujarat, Haryana, Himachal Pradesh, Karnataka, Kerala, Madhya Pradesh,
Madhya Pradesh & Chhattisgarh, Maharashtra, Northeast India (multi-state),
Odisha, Pan-India, Pan-India (Hindi Belt), Punjab, Punjab & Haryana, Rajasthan,
Tamil Nadu, Telangana, Uttar Pradesh, West Bengal
```

### `region` — 16 values

```
Africa, Asia, Caribbean, Central India, East India, Europe, Global,
Latin America, Middle East, National, North America, North India,
Northeast India, Oceania, South India, West India
```

`region` is returned on `SourceOut` but is **not** a query parameter on either
endpoint.

### `type` — 4 values

`newspaper` (154) · `tv_news` (123) · `digital_news` (74) · `news_agency` (14)

Like `region`, `type` is returned but not filterable. Filter client-side on the
`/news/sources` response if you need it.

---

## 15. Related documentation

- **[DEPLOYMENT.md](DEPLOYMENT.md)** — installing, configuring and running the service.
- **Interactive API reference** — `/docs`, `/redoc`, `/openapi.json` on any running instance.
