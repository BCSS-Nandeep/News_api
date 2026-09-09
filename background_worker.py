"""
Optional cache pre-warmer for the News API.

The API works standalone without this — each source is discovered and
scraped lazily on the first request that needs it, then served from cache
until it expires (services/cache.py, CACHE_TTL_SECONDS). Running this
alongside the API just means requests rarely hit a cold cache.

Usage:
    python background_worker.py            # loop forever, refresh every 10 min
    python background_worker.py --once     # single refresh, then exit
"""

import sys
import time
from datetime import datetime

import schedule

from services.news_service import refresh_all_sources


def run():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Refreshing all active sources...")
    total = refresh_all_sources()
    print(f"[DONE] Cached {total} articles across all active sources.")


if __name__ == '__main__':
    if '--once' in sys.argv:
        run()
    else:
        print("[WORKER] News API cache pre-warmer started. Refreshing every 10 minutes.")
        run()
        schedule.every(10).minutes.do(run)
        while True:
            schedule.run_pending()
            time.sleep(30)
