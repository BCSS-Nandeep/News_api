"""
Canonical URL + deterministic article ID.

The service is stateless, so ids can't come from a database — they're derived
from the URL itself. The same article URL must always produce the same id,
with or without tracking params, so a duplicate never gets returned twice
within one response and SocEye can re-request an article by id.
"""

import hashlib
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

_TRACKING_PARAM_PREFIXES = ('utm_',)
_TRACKING_PARAMS = {
    'fbclid', 'gclid', 'gclsrc', 'msclkid', 'mc_cid', 'mc_eid',
    'ref', 'ref_src', 'ref_url', 'refsrc', 'cmpid', 'ito', 'igshid', 'spm',
}


def canonical_url(url: str) -> str:
    """Lowercase scheme+host, strip a leading www., drop known tracking
    query params (keeping any others — some sites need ?id=... to resolve
    the article), drop the fragment, and strip a trailing slash on the path.
    """
    if not url:
        return ''
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or 'https').lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]

    kept_params = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
        and not k.lower().startswith(_TRACKING_PARAM_PREFIXES)
    ]
    query = urlencode(kept_params)

    path = parsed.path.rstrip('/') or ''

    return urlunparse((scheme, netloc, path, parsed.params, query, ''))


def make_article_id(url: str) -> str:
    """Stable id derived from the canonical URL. Same URL -> same id, always."""
    return hashlib.sha256(canonical_url(url).encode('utf-8')).hexdigest()[:16]


def dedupe_by_id(articles: list) -> list:
    """Keep the first occurrence of each article id (dict items with an 'id' key)."""
    seen = set()
    out = []
    for a in articles:
        aid = a.get('id') if isinstance(a, dict) else getattr(a, 'id', None)
        if aid in seen:
            continue
        seen.add(aid)
        out.append(a)
    return out
