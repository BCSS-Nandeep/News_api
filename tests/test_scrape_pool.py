"""
services/scrape_pool.py: one job per key, bounded concurrency, requests before
background refreshes, and a failing / slow / crashing job never blocks the rest.
Thread mode is what the other tests run on; process mode is what production
runs (spawned worker processes) and is covered at the end.
"""

import os
import threading
import time

import pytest

from services import scrape_pool
from services.scrape_pool import JobTimeout, ScrapePool, WorkerDied
from tests import pool_jobs


class Recorder:
    """A thread-mode job that records runs and can be held until released."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started = []
        self.running = 0
        self.max_running = 0
        self.gate = threading.Event()

    def __call__(self, arg):
        with self.lock:
            self.started.append(arg)
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            if arg.startswith('block'):
                self.gate.wait(timeout=5)
            if arg.startswith('fail'):
                raise RuntimeError(f'{arg} is down')
            if arg.startswith('slow'):
                time.sleep(1.5)
            return f'result:{arg}'
        finally:
            with self.lock:
                self.running -= 1


@pytest.fixture
def recorder():
    rec = Recorder()
    yield rec
    rec.gate.set()


@pytest.fixture
def fast_watchdog(monkeypatch):
    monkeypatch.setattr(scrape_pool, '_WATCHDOG_SECONDS', 0.05)


def thread_pool(job, threads=2, **kw):
    return ScrapePool(job, processes=0, threads_per_process=threads, job_timeout=kw.pop('job_timeout', 30), **kw)


class TestThreadMode:
    def test_one_job_per_key_while_queued_or_running(self, recorder):
        pool = thread_pool(recorder)
        first = pool.submit('src', 'block-a')
        second = pool.submit('src', 'block-a')
        assert first is second
        recorder.gate.set()
        assert first.result(timeout=5) == 'result:block-a'
        assert recorder.started == ['block-a']

    def test_a_finished_key_can_run_again(self, recorder):
        pool = thread_pool(recorder)
        assert pool.submit('src', 'a').result(timeout=5) == 'result:a'
        assert pool.submit('src', 'a').result(timeout=5) == 'result:a'
        assert recorder.started == ['a', 'a']

    def test_never_runs_more_than_capacity(self, recorder):
        pool = thread_pool(recorder, threads=2)
        futures = [pool.submit(f'k{i}', f'block-{i}') for i in range(6)]
        time.sleep(0.2)
        assert recorder.running == 2
        assert pool.busy() == {'running': 2, 'queued': 4}
        recorder.gate.set()
        assert [f.result(timeout=5) for f in futures] == [f'result:block-{i}' for i in range(6)]
        assert recorder.max_running == 2

    def test_requests_overtake_queued_background_refreshes(self, recorder):
        pool = thread_pool(recorder, threads=1)
        pool.submit('busy', 'block-busy')
        time.sleep(0.1)
        refresh_a = pool.submit('a', 'a', priority=1)
        refresh_b = pool.submit('b', 'b', priority=1)
        request_c = pool.submit('c', 'c', priority=0)
        bumped_b = pool.submit('b', 'b', priority=0)  # a request for a queued refresh
        assert bumped_b is refresh_b
        recorder.gate.set()
        for f in (refresh_a, refresh_b, request_c):
            f.result(timeout=5)
        assert recorder.started == ['block-busy', 'c', 'b', 'a']

    def test_failing_job_resolves_with_its_error_and_others_continue(self, recorder):
        pool = thread_pool(recorder)
        bad = pool.submit('bad', 'fail-x')
        good = pool.submit('good', 'ok')
        with pytest.raises(RuntimeError, match='fail-x is down'):
            bad.result(timeout=5)
        assert good.result(timeout=5) == 'result:ok'

    def test_slow_job_times_out_and_frees_its_slot(self, recorder, fast_watchdog):
        pool = thread_pool(recorder, threads=1, job_timeout=0.3)
        slow = pool.submit('slow', 'slow-x')
        after = pool.submit('next', 'next')
        with pytest.raises(JobTimeout):
            slow.result(timeout=5)
        assert after.result(timeout=5) == 'result:next'

    def test_on_done_runs_before_the_key_is_released(self, recorder):
        seen = []
        pool = thread_pool(recorder, on_done=lambda key, value: seen.append((key, value, pool.busy())))
        pool.submit('src', 'a').result(timeout=5)
        assert seen == [('src', 'result:a', {'running': 1, 'queued': 0})]

    def test_on_done_is_not_called_for_failures(self, recorder):
        seen = []
        pool = thread_pool(recorder, on_done=lambda key, value: seen.append(key))
        with pytest.raises(RuntimeError):
            pool.submit('bad', 'fail-y').result(timeout=5)
        assert seen == []


class TestProcessMode:
    """Real spawned worker processes, as in production."""

    @pytest.fixture
    def pool(self, fast_watchdog):
        pool = ScrapePool(pool_jobs.job, processes=2, threads_per_process=2, job_timeout=30)
        yield pool
        pool.shutdown()

    def test_jobs_run_in_worker_processes_with_threads_each(self, pool):
        pids = [pool.submit(f'k{i}', {'key': f'k{i}', 'do': 'pid'}) for i in range(8)]
        pids = {f.result(timeout=60) for f in pids}
        assert os.getpid() not in pids
        assert 1 <= len(pids) <= 2
        assert pool.capacity == 4

    def test_results_and_errors_come_back(self, pool):
        assert pool.submit('a', {'key': 'a'}).result(timeout=60) == 'a'
        with pytest.raises(RuntimeError, match='site down'):
            pool.submit('b', {'key': 'b', 'do': 'raise'}).result(timeout=60)

    def test_a_crashed_worker_is_replaced_and_other_jobs_still_run(self, pool):
        assert pool.submit('warm', {'key': 'warm'}).result(timeout=60) == 'warm'
        with pytest.raises(WorkerDied):
            pool.submit('crash', {'key': 'crash', 'do': 'exit'}).result(timeout=60)
        assert pool.submit('after', {'key': 'after'}).result(timeout=60) == 'after'
