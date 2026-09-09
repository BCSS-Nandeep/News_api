"""
State/district tagging for the News API.

Only Telangana sources get real district-level detection — it uses the
keyword gazetteer in location_data.py, which only covers Telangana. Every
other state in News_URLs.json gets its `state` from the source registry
directly (already accurate, since each source's state is curated) and an
empty district — building a 30-state district gazetteer is future work,
not attempted here.
"""

from processing.location_data import LOCATION_KEYWORDS, TELANGANA_LAT, TELANGANA_LNG


def detect_district(text: str) -> dict:
    """Tag the district whose alias appears EARLIEST in the article text — a
    multi-district article (e.g. an event in Vijayawada, opposition reaction
    quoted from Guntur) should map to whichever district the story is
    actually about, not whichever district happens to be declared first in
    LOCATION_KEYWORDS."""
    lower = (text or '').lower()
    best_district = None
    best_pos = None
    for district, aliases in LOCATION_KEYWORDS.items():
        for alias in aliases:
            pos = lower.find(alias.lower())
            if pos != -1 and (best_pos is None or pos < best_pos):
                best_pos = pos
                best_district = district
    if best_district:
        return {
            'location_found': True,
            'district': best_district,
            'city': best_district,
            'state': 'Telangana',
            'lat': TELANGANA_LAT,
            'lng': TELANGANA_LNG,
        }
    return {'location_found': False, 'district': '', 'city': '', 'state': 'India', 'lat': None, 'lng': None}


def resolve_location(source_state: str, text: str) -> dict:
    """Resolve {state, district, location} for a normalized article.

    `source_state` is the registry's `state` field for the article's source
    (e.g. "Telangana", "Andhra Pradesh & Telangana", "Tamil Nadu"). District
    detection only runs for sources whose state covers Telangana; every other
    state is tagged from the registry alone.
    """
    state = source_state or ''
    if 'telangana' in state.lower():
        found = detect_district(text)
        if found.get('location_found'):
            district = found['district']
            return {'state': state, 'district': district, 'location': district}
    return {'state': state, 'district': '', 'location': ''}
