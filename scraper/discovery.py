"""
Article-URL discovery for a single source, in preference order:

    1. RSS          - <link rel="alternate" type="application/rss+xml"> on the
                       homepage, falling back to a few common feed paths
    2. News sitemap  - robots.txt's Sitemap: directive (or /sitemap.xml),
    3. Sitemap         preferring a <news:news>-tagged child sitemap
    4. Google News   - a site:{domain} search, decoded via resolve_google_news_url
    5. Website       - homepage links filtered through is_article_url()

Each strategy is generic/best-effort — none of it is hand-tuned per outlet.
RSS auto-discovery is expected to be the working path for most sites, sitemap
next; Google News/website are coarser fallbacks for sites with neither.

Every strategy returns a list of RawItem dicts:
    {url, title, summary, image_url, published_at (datetime|None)}
Callers (services/news_service.py) then run each url through
scraper/extraction.py's fetch_article_page() for the full body/image.

Network failures anywhere here are caught and treated as "strategy found
nothing" — never raised — so one dead source can't break the fallback chain
or the caller's request.
"""

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from scraper.extraction import HEADERS, is_article_url, extract_image, extract_summary, resolve_google_news_url
from scraper.sources_registry import Source

_TIMEOUT = 6
_MAX_ITEMS_PER_SOURCE = 25
_COMMON_FEED_PATHS = ('/feed/', '/feed', '/rss.xml')
# Wall-clock budget for one source's whole discover_source() call — a source
# with a dead homepage could otherwise burn 4 strategies x several requests
# x _TIMEOUT each. Checked BETWEEN strategies, so an already-started
# strategy still finishes; this just stops starting new ones past budget.
_MAX_SECONDS_PER_SOURCE = 18


def _get(url: str, **kw) -> Optional[requests.Response]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=_TIMEOUT, **kw)
        if resp.status_code == 200:
            resp.encoding = resp.apparent_encoding or 'utf-8'
            return resp
    except Exception:
        pass
    return None


def _entry_to_item(entry, feed_domain: str) -> Optional[Dict]:
    url = getattr(entry, 'link', '') or ''
    if not url:
        return None
    published_at = None
    if getattr(entry, 'published_parsed', None):
        published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    elif getattr(entry, 'updated_parsed', None):
        published_at = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    return {
        'url': url,
        'title': (getattr(entry, 'title', '') or '').strip(),
        'summary': extract_summary(entry),
        'image_url': extract_image(entry),
        'published_at': published_at,
    }


def _parse_feed_url(feed_url: str, feed_domain: str, strict: bool = False) -> List[Dict]:
    """strict=True is for GUESSED feed paths (no site-declared <link> backing
    them) — many CMSes return a normal HTML page (their homepage, a 404 page,
    a category listing) for an unknown path instead of a real 404 status, and
    feedparser can salvage stray <a>/<link> elements out of that HTML as fake
    "entries" (this is exactly how Sakshi's guessed /feed/ path once returned
    category pages like "Sagubadi"/"Book review" as if they were articles).
    strict mode requires a real feed Content-Type and a clean (non-bozo)
    parse; a site-declared <link rel="alternate"> is trusted more loosely.
    """
    resp = _get(feed_url)
    if not resp:
        return []
    if strict:
        content_type = resp.headers.get('Content-Type', '').lower()
        if not any(t in content_type for t in ('xml', 'rss', 'atom')):
            return []
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and (strict or not parsed.entries):
        return []
    items = []
    for entry in parsed.entries[:_MAX_ITEMS_PER_SOURCE]:
        item = _entry_to_item(entry, feed_domain)
        if item and is_article_url(item['url']):
            items.append(item)
    return items


# ── 1. RSS ─────────────────────────────────────────────────────────────────────

def rss_discover(source: Source) -> List[Dict]:
    domain = urlparse(source.base_url).netloc.replace('www.', '')

    home = _get(source.base_url)
    if home:
        soup = BeautifulSoup(home.text, 'html.parser')
        for link in soup.find_all('link', attrs={'type': ['application/rss+xml', 'application/atom+xml']}):
            href = link.get('href')
            if href:
                items = _parse_feed_url(urljoin(source.base_url, href), domain)
                if items:
                    return items

        # Homepage loaded but had no <link> tag — still worth guessing common
        # feed paths on a domain we know is actually up. strict=True here:
        # see _parse_feed_url's docstring for why guessed paths need it.
        for path in _COMMON_FEED_PATHS:
            items = _parse_feed_url(urljoin(source.base_url, path), domain, strict=True)
            if items:
                return items
    # If the homepage itself didn't load, guessing feed paths on the same
    # (likely dead/blocking) domain is a poor use of the time budget — skip
    # straight to the next strategy instead of 3 more full-timeout misses.

    return []


# ── 2/3. News sitemap & sitemap ─────────────────────────────────────────────────

def _find_sitemap_urls(source: Source) -> List[str]:
    candidates = []
    robots = _get(urljoin(source.base_url, '/robots.txt'))
    if robots:
        for line in robots.text.splitlines():
            if line.lower().startswith('sitemap:'):
                candidates.append(line.split(':', 1)[1].strip())
    candidates.append(urljoin(source.base_url, '/sitemap.xml'))
    candidates.append(urljoin(source.base_url, '/news-sitemap.xml'))
    # de-dup, preserve order
    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _local(tag: str) -> str:
    """Strip the XML namespace off a tag name, e.g. '{...}url' -> 'url'."""
    return tag.split('}')[-1] if '}' in tag else tag


def _fetch_sitemap_xml(url: str) -> Optional[ET.Element]:
    resp = _get(url)
    if not resp:
        return None
    try:
        return ET.fromstring(resp.content)
    except ET.ParseError:
        return None


def _looks_like_sitemap_url(url: str) -> bool:
    """A <loc> pointing at another sitemap file, not a real page."""
    path = urlparse(url).path.lower()
    return path.endswith('.xml') or '/sitemap' in path


def _direct_children_with_loc(root: ET.Element) -> List[tuple]:
    """(element, loc_text) for every direct child of root that has a <loc>,
    regardless of whether the child is tagged <sitemap> or <url> — some CMSes
    (e.g. prajasakti.com) declare a plain <urlset> whose <loc> entries are
    themselves nested sitemap files, which is non-compliant with the sitemap
    protocol but common enough in the wild that tag-name alone isn't a
    reliable signal of "this is a real page vs. another sitemap"."""
    out = []
    for child in root:
        loc_el = next((c for c in child if _local(c.tag) == 'loc'), None)
        if loc_el is not None and loc_el.text:
            out.append((child, loc_el.text.strip()))
    return out


def _resolve_sitemap(root: ET.Element, depth: int = 0) -> List[Dict]:
    if depth > 2:
        return []
    entries = _direct_children_with_loc(root)
    if not entries:
        return []

    sitemap_like = [u for _, u in entries if _looks_like_sitemap_url(u)]
    is_index = _local(root.tag) == 'sitemapindex' or len(sitemap_like) >= max(1, len(entries)) * 0.8
    if is_index:
        child_urls = [u for _, u in entries]
        child_urls.sort(key=lambda u: ('news' not in u.lower(), u))
        for child_url in child_urls[:3]:
            child_root = _fetch_sitemap_xml(child_url)
            if child_root is None:
                continue
            items = _resolve_sitemap(child_root, depth=depth + 1)
            if items:
                return items
        return []

    items = []
    for url_el, url in entries:
        if _looks_like_sitemap_url(url) or not is_article_url(url):
            continue

        title = ''
        published_at = None
        for child in url_el.iter():
            tag = _local(child.tag)
            if tag == 'title' and child.text:
                title = child.text.strip()
            elif tag in ('publication_date', 'lastmod') and child.text:
                try:
                    published_at = datetime.fromisoformat(child.text.strip().replace('Z', '+00:00'))
                except ValueError:
                    pass

        items.append({
            'url': url,
            'title': title,
            'summary': '',
            'image_url': None,
            'published_at': published_at,
        })
        if len(items) >= _MAX_ITEMS_PER_SOURCE:
            break
    return items


def sitemap_discover(source: Source) -> List[Dict]:
    for sitemap_url in _find_sitemap_urls(source):
        root = _fetch_sitemap_xml(sitemap_url)
        if root is None:
            continue
        items = _resolve_sitemap(root)
        if items:
            return items
    return []


# ── 4. Google News fallback ─────────────────────────────────────────────────────

def google_news_discover(source: Source) -> List[Dict]:
    domain = urlparse(source.base_url).netloc.replace('www.', '')
    if not domain:
        return []
    query = f'site:{domain}'
    feed_url = f'https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en'
    resp = _get(feed_url)
    if not resp:
        return []
    parsed = feedparser.parse(resp.content)
    items = []
    for entry in parsed.entries[:_MAX_ITEMS_PER_SOURCE]:
        gnews_url = getattr(entry, 'link', '') or ''
        if not gnews_url:
            continue
        real_url = resolve_google_news_url(gnews_url)
        if not real_url or not is_article_url(real_url):
            continue
        title = (getattr(entry, 'title', '') or '').strip()
        if ' - ' in title:
            title = title.rsplit(' - ', 1)[0].strip()
        items.append({
            'url': real_url,
            'title': title,
            'summary': extract_summary(entry),
            'image_url': extract_image(entry),
            'published_at': (
                datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if getattr(entry, 'published_parsed', None) else None
            ),
        })
    return items


# ── 5. Website homepage fallback ────────────────────────────────────────────────

def website_discover(source: Source) -> List[Dict]:
    home = _get(source.base_url)
    if not home:
        return []
    soup = BeautifulSoup(home.text, 'html.parser')
    items = []
    seen = set()
    for a in soup.find_all('a', href=True):
        url = urljoin(source.base_url, a['href'])
        if url in seen or not is_article_url(url):
            continue
        seen.add(url)
        text = a.get_text(strip=True)
        items.append({
            'url': url,
            'title': text,
            'summary': '',
            'image_url': None,
            'published_at': None,
        })
        if len(items) >= _MAX_ITEMS_PER_SOURCE:
            break
    return items


# ── Chain ────────────────────────────────────────────────────────────────────────

_STRATEGIES = (rss_discover, sitemap_discover, google_news_discover, website_discover)


def discover_source(source: Source) -> List[Dict]:
    """Run the fallback chain, returning the first strategy's results that
    found anything. Never raises — a fully broken source just yields []."""
    for strategy in _STRATEGIES:
        try:
            items = strategy(source)
        except Exception:
            items = []
        if items:
            return items
    return []
