"""
Pure scraping mechanics: fetch a page, pull out its body/image/title, decode
a Google News link, decide whether a URL is an article at all.

Deliberately free of any domain knowledge or storage concerns so both the
discovery layer (scraper/discovery.py) and the article normalizer
(services/news_service.py) can share it.
"""

import re
import socket
from urllib.parse import urlparse
from typing import Optional

import json
import requests
from bs4 import BeautifulSoup

# Safety net: without this, any blocking call that lacks its own explicit
# timeout (e.g. a stalled connection during DNS/SSL) can hang forever.
# Set once at import time so every caller of this module is covered.
socket.setdefaulttimeout(20)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9,te;q=0.8,hi;q=0.7',
}

_SKIP_PATTERNS = re.compile(
    r'/(tag|tags|category|categories|section|topic|author|page|feed|rss|'
    r'search|trending|videos|gallery|live|breaking|sitemap|epaper|e-paper)(/|$)',
    re.IGNORECASE,
)

SKIP_DOMAINS = {'indianexpress.com', 'news.google.com'}
DEFAULT_IMAGE_URL = 'https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/Flag_of_India.svg/320px-Flag_of_India.svg.png'


# ── Language detection ─────────────────────────────────────────────────────────

def detect_language(text: str) -> str:
    """Heuristic: count Telugu vs Devanagari vs Latin chars."""
    if not text:
        return 'en'
    telugu     = len(re.findall(r'[ఀ-౿]', text))
    devanagari = len(re.findall(r'[ऀ-ॿ]', text))
    latin      = len(re.findall(r'[a-zA-Z]', text))
    total = telugu + devanagari + latin or 1
    if telugu / total > 0.1:
        return 'te'
    if devanagari / total > 0.1:
        return 'hi'
    return 'en'


# ── Image extraction ──────────────────────────────────────────────────────────

def extract_image(entry) -> Optional[str]:
    """Try every known RSS/Atom image field in priority order."""
    def _valid(url):
        return bool(url and isinstance(url, str) and url.startswith('http')
                    and not url.endswith('.gif'))

    for thumb in getattr(entry, 'media_thumbnail', []):
        if _valid(thumb.get('url', '')):
            return thumb['url']

    for mc in getattr(entry, 'media_content', []):
        url    = mc.get('url', '')
        medium = mc.get('medium', '')
        mtype  = mc.get('type', '')
        if _valid(url) and ('image' in medium or 'image' in mtype or medium == ''):
            return url

    for enc in getattr(entry, 'enclosures', []):
        if enc.get('type', '').startswith('image/') and _valid(enc.get('href', '')):
            return enc['href']

    for link in getattr(entry, 'links', []):
        lt = link.get('type', '')
        if lt.startswith('image/') and _valid(link.get('href', '')):
            return link['href']

    for html_src in [
        (entry.content[0].get('value', '') if getattr(entry, 'content', None) else ''),
        getattr(entry, 'summary_detail', {}).get('value', ''),
        getattr(entry, 'summary', '') or '',
    ]:
        if not html_src:
            continue
        m = re.search(r'<img[^>]+src=["\']?([^"\'>\s]+)["\']?', html_src, re.IGNORECASE)
        if m and _valid(m.group(1)):
            return m.group(1)

    return None


# ── Summary extraction ────────────────────────────────────────────────────────

def extract_summary(entry) -> str:
    """Prefer content:encoded (full HTML) over description (snippet)."""
    content_encoded = ''
    if hasattr(entry, 'content') and entry.content:
        content_encoded = entry.content[0].get('value', '')

    description = getattr(entry, 'summary', '') or ''
    raw_html = content_encoded if len(content_encoded) > len(description) else description

    if raw_html:
        soup = BeautifulSoup(raw_html, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        return re.sub(r'\s+', ' ', text).strip()

    return ''


# ── Article page fetcher ──────────────────────────────────────────────────────

_CONTENT_SELECTORS = [
    'div[itemprop="articleBody"] p',
    # entry-content is WordPress's default body class and a large share of
    # Indian news sites run WordPress, so it's worth trying early.
    'div[class*="entry-content"] p',
    'article p',
    'div[class*="article-body"] p',
    'div[class*="articleBody"] p',
    'div[class*="story-content"] p',
    'div[class*="storyContent"] p',
    'div[class*="story_content"] p',
    'div[class*="content-area"] p',
    'div[class*="article-content"] p',
    'div[class*="article_content"] p',
    'div[class*="post-content"] p',
    'div.artText p',
    'div.storytxt p',
    'div.storyDetails p',
    'section[class*="article"] p',
    '.story-body p',
    '.article p',
    'main article p',
    'main p',
]

_NOISE_KEYWORDS = [
    'subscribe', 'follow us', 'advertisement', 'also read',
    'read more', 'click here', 'download app', 'all rights reserved',
    'copyright', 'share this', 'whatsapp', 'facebook', 'twitter',
]


# Chrome to strip before looking for body text. Kept deliberately narrow:
# a bare [class*="ad"] also matches header/read/load/shadow/breadcrumb, and
# WordPress puts long class lists on <body> itself — matching there wipes the
# whole page. Hence whole-token / hyphen-delimited "ad" patterns only.
_NOISE_SELECTOR = (
    'script, style, nav, footer, aside, form, iframe, '
    '[class*="related"], [class*="social"], [class*="share"], '
    '[class*="advert"], [class~="ad"], [class*="ad-"], [class*="-ad"], '
    '[class*="newsletter"], [class*="subscribe"], [class*="comment"]'
)


def _holds_article_body(tag) -> bool:
    """True if this element contains several real paragraphs — i.e. removing it
    would take the article with it."""
    longs = 0
    for p in tag.find_all('p'):
        if len(p.get_text(strip=True)) >= 60:
            longs += 1
            if longs >= 3:
                return True
    return False


def _strip_noise(soup) -> None:
    for tag in soup.select(_NOISE_SELECTOR):
        if tag.name in ('html', 'body'):
            continue
        if tag.name not in ('script', 'style') and _holds_article_body(tag):
            continue
        tag.decompose()


# WordPress stamps what kind of page it rendered onto <body>. Archive/category
# /tag pages are listings, not articles — they're how "Crime in Hyderabad
# Archives" style entries end up looking like stories. A huge share of Indian
# news sites run WordPress, so this is a cheap, reliable discriminator.
_LISTING_BODY_CLASSES = {'archive', 'category', 'tag', 'author', 'search', 'blog', 'paged'}


def _is_listing_page(soup) -> bool:
    body = soup.find('body')
    if not body:
        return False
    return bool(_LISTING_BODY_CLASSES.intersection(body.get('class') or []))


def _parse_published(soup) -> Optional[str]:
    """Real publication timestamp, when the page advertises one. Only genuine
    articles carry article:published_time — listings don't — and without it
    the caller would otherwise stamp every article with 'now'."""
    for prop in ('article:published_time', 'og:article:published_time', 'article:modified_time'):
        tag = soup.find('meta', property=prop)
        if tag and tag.get('content'):
            return tag['content'].strip()
    time_tag = soup.find('time', attrs={'datetime': True})
    if time_tag:
        return time_tag['datetime'].strip()
    return None


def _clean_paragraph(tag) -> str:
    text = re.sub(r'\s+', ' ', tag.get_text(separator=' ', strip=True)).strip()
    if len(text) < 40:
        return ''
    if any(kw in text.lower() for kw in _NOISE_KEYWORDS):
        return ''
    return text


def _densest_text_block(soup) -> str:
    """Fallback when no known selector matched: return the text of whichever
    element holds the most paragraph content.

    _CONTENT_SELECTORS only covers markup we've seen before, and the registry
    spans 170+ independently-built sites — hand-adding a selector per site
    doesn't scale. Real articles put their body in one dense block of <p>
    tags, so picking the densest block generalises to markup we've never seen.
    Listing/category pages have no such block, so they correctly yield ''.
    """
    buckets: dict = {}
    holders: dict = {}
    for p in soup.find_all('p'):
        text = _clean_paragraph(p)
        if not text:
            continue
        parent = p.parent
        if parent is None:
            continue
        key = id(parent)
        holders[key] = parent
        buckets.setdefault(key, []).append(text)

    if not buckets:
        return ''
    best = max(buckets.values(), key=lambda texts: sum(len(t) for t in texts))
    content = '\n\n'.join(best)
    return content if len(content) >= 200 else ''


def fetch_article_page(url: str) -> dict:
    """Fetch article page and return image, og:description, and full body content."""
    if not url or 'news.google.com' in url:
        return {'image': None, 'description': None, 'content': ''}

    headers = {
        **HEADERS,
        'Referer': 'https://www.google.com/',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-IN,en;q=0.9,te;q=0.8,hi;q=0.7',
    }
    try:
        resp = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
        if resp.status_code != 200:
            return {'image': None, 'description': None, 'content': ''}

        resp.encoding = resp.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')

        _strip_noise(soup)

        def meta(prop, name=None):
            tag = soup.find('meta', property=prop) or (
                soup.find('meta', attrs={'name': name}) if name else None
            )
            return tag['content'].strip() if tag and tag.get('content') else None

        image = (
            meta('og:image') or meta('og:image:secure_url')
            or meta('twitter:image', 'twitter:image')
            or meta('twitter:image:src', 'twitter:image:src')
        )
        if not image:
            for img in soup.find_all('img', src=True):
                src = img['src']
                if src.startswith('http') and not src.endswith('.gif'):
                    try:
                        if int(str(img.get('width', '0')).replace('px', '') or 0) >= 200:
                            image = src
                            break
                    except ValueError:
                        pass

        desc = meta('og:description') or meta('description', 'description')

        title = meta('og:title') or meta('twitter:title', 'twitter:title')
        if not title and soup.title and soup.title.string:
            title = soup.title.string.strip()

        content = ''
        for selector in _CONTENT_SELECTORS:
            tags = soup.select(selector)
            if not tags:
                continue
            paragraphs = []
            for t in tags:
                text = t.get_text(separator=' ', strip=True)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) < 40:
                    continue
                if any(kw in text.lower() for kw in _NOISE_KEYWORDS):
                    continue
                paragraphs.append(text)
            if paragraphs:
                content = '\n\n'.join(paragraphs)
                if len(content) > 200:
                    break

        if len(content) < 200:
            content = _densest_text_block(soup) or content

        return {
            'image': image if image and image.startswith('http') else None,
            'description': desc if desc else None,
            'content': content,
            'title': title or None,
            'published': _parse_published(soup),
            'is_listing': _is_listing_page(soup),
        }
    except Exception:
        return {'image': None, 'description': None, 'content': '', 'title': None,
                'published': None, 'is_listing': False}


# ── Google News URL resolver ──────────────────────────────────────────────────

def resolve_google_news_url(url: str) -> str:
    """Decode a Google News RSS link to the real article URL.

    Modern Google News links (/rss/articles/CBMi...) are not plain HTTP
    redirects — the real URL is obtained via Google's batchexecute endpoint,
    using a signature + timestamp embedded in the article page. Returns '' if
    it can't be decoded (caller then keeps the Google News link, headline-only).
    """
    try:
        # Cheap path first: an old-style HTTP redirect, if any.
        resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        # requests falls back to ISO-8859-1 for text/* responses that omit a
        # charset (RFC 2616), which turns UTF-8 pages into mojibake. Match what
        # fetch_article_page already does.
        resp.encoding = resp.apparent_encoding or 'utf-8'
        if 'news.google.com' not in resp.url:
            return resp.url

        gn_id = urlparse(url).path.rstrip('/').split('/')[-1].split('?')[0]
        if not gn_id:
            return ''

        soup = BeautifulSoup(resp.text, 'html.parser')
        div = soup.select_one('c-wiz > div')
        if not div:
            return ''
        sig = div.get('data-n-a-sg')
        ts = div.get('data-n-a-ts')
        if not (sig and ts):
            return ''

        inner = json.dumps([
            'garturlreq',
            [['X', 'X', ['X', 'X'], None, None, 1, 1, 'US:en', None, 1, None, None, None, None, None, 0, 1],
             'X', 'X', 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            gn_id, ts, sig,
        ])
        payload = [['Fbv4je', inner]]
        r2 = requests.post(
            'https://news.google.com/_/DotsSplashUi/data/batchexecute',
            headers={'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
            data={'f.req': json.dumps([payload])},
            timeout=12,
        )
        for m in re.finditer(r'(https?://[^\s"\\]+)', r2.text):
            u = m.group(1)
            if 'google.com' not in u and 'gstatic.com' not in u:
                return u
    except Exception:
        pass
    return ''


# ── URL validation ────────────────────────────────────────────────────────────

def is_article_url(url: str) -> bool:
    if not url:
        return False
    if 'news.google.com' in url:
        return False
    parsed = urlparse(url)
    if parsed.netloc.replace('www.', '') in SKIP_DOMAINS:
        return False
    path = parsed.path.rstrip('/')
    if _SKIP_PATTERNS.search(path):
        return False
    segments = [s for s in path.split('/') if s]
    if not segments:
        return False

    last = segments[-1]
    if len(last) < 8 and not re.search(r'\d', last):
        return False

    # Flat-permalink sites put the whole headline in one segment
    # (siasat.com/jamia-nizamia-files-complaint-...-3539270). Requiring two
    # segments rejected every real article on those sites while happily
    # accepting their two-segment category pages (/news/telangana). So judge a
    # single-segment URL on whether the slug reads like a headline — several
    # hyphens, or a trailing numeric id — which /crime/ and /about-us/ fail.
    if len(segments) == 1 and last.count('-') < 2 and not re.search(r'\d', last):
        return False

    return True
