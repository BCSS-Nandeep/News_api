"""
One-off scrape of every active source, for smoke-testing scraping.

Not needed in production: the API keeps its own cache warm with a background
refresh (BACKGROUND_REFRESH_SECONDS). Run as a separate process this fills its
own memory, not the API's, so the API gains nothing from it (DEPLOYMENT.md §6).

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
