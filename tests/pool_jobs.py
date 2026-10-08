"""Jobs for tests/test_scrape_pool.py's process-mode tests. They must live in an
importable module: worker processes are spawned and import the job by name."""

import os
import time


def job(arg: dict):
    action = arg.get('do', 'echo')
    if action == 'sleep':
        time.sleep(arg['seconds'])
    elif action == 'raise':
        raise ValueError('site down')
    elif action == 'exit':
        os._exit(3)  # a worker process dying mid-scrape
    elif action == 'pid':
        time.sleep(0.2)
        return os.getpid()
    return arg['key']
