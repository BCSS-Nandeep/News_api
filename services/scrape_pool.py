"""
Bounded, prioritised pool that runs one job per key — the News API uses it to
scrape sources, keyed by source id.

Scraping is CPU-heavy (HTML parsing), and one Python process only ever uses one
core, so in production jobs run in `processes` worker processes, each running up
to `threads_per_process` jobs at a time on its own threads (network waits
overlap). With processes=0 jobs run on threads in this process instead — what
the tests use, since a monkeypatched scraper only exists in this process.

Guarantees, in both modes:
  * one job per key at a time: submitting a key that is queued or running
    returns the existing future (no duplicate scrapes, no duplicate articles);
  * at most processes x threads jobs run at once; the rest wait in a priority
    queue, so a user's request (priority 0) overtakes background refreshes (1);
  * a job that raises, exceeds `job_timeout`, or whose worker process dies
    resolves its future with an exception — it never blocks other jobs. A dead
    worker restarts the process pool.

Every result reaches the caller in this process (on_done, then the future), so
all shared state — the article cache — is only ever written here.
"""

import heapq
import itertools
import logging
import multiprocessing
import os
import pickle
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger('blura.news.pool')

_WATCHDOG_SECONDS = 2.0


class JobTimeout(TimeoutError):
    pass


class WorkerDied(RuntimeError):
    pass


@dataclass
class _Task:
    key: str
    arg: Any
    priority: int
    future: Future = field(default_factory=Future)
    run_id: Optional[int] = None      # set once dispatched
    started_at: float = 0.0           # set once a thread actually runs it (0 = waiting)


def _worker_main(tasks, results, threads: int, job: Callable, nice: int) -> None:
    """Worker process: run up to `threads` jobs at once, report each result."""
    if nice and hasattr(os, 'nice'):
        try:
            os.nice(nice)
        except OSError:
            pass
    slots = threading.Semaphore(threads)
    pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix='job')

    def run(run_id: int, arg: Any) -> None:
        try:
            try:
                payload = (True, pickle.dumps(job(arg), protocol=pickle.HIGHEST_PROTOCOL))
            except BaseException as exc:  # report, never kill the worker
                payload = (False, f'{type(exc).__name__}: {exc}')
            results.put(('done', run_id) + payload)
        finally:
            slots.release()

    while True:
        slots.acquire()            # only take a job when a thread is free
        item = tasks.get()
        if item is None:
            break
        run_id, arg = item
        results.put(('start', run_id, os.getpid()))
        pool.submit(run, run_id, arg)
    pool.shutdown(wait=False)


class ScrapePool:
    def __init__(
        self,
        job: Callable[[Any], Any],
        processes: int,
        threads_per_process: int,
        job_timeout: float,
        on_done: Optional[Callable[[str, Future], None]] = None,
        nice: int = 0,
    ):
        self._job = job
        self._processes = max(0, processes)
        self._threads = max(1, threads_per_process)
        self.capacity = max(1, self._processes) * self._threads
        self._job_timeout = job_timeout
        self._on_done = on_done
        self._nice = nice

        self._lock = threading.Lock()
        self._tasks: Dict[str, _Task] = {}        # queued or running, by key
        self._runs: Dict[int, _Task] = {}         # running, by run id
        self._heap: List[tuple] = []              # (priority, seq, key)
        self._seq = itertools.count()
        self._started = False
        self._stopping = False

        self._local: Optional[ThreadPoolExecutor] = None
        self._ctx = None
        self._task_q = None
        self._result_q = None
        self._workers: List[Any] = []

    # ── public ────────────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return f'{self._processes} processes x {self._threads} threads' if self._processes \
            else f'{self._threads} threads in-process'

    def submit(self, key: str, arg: Any, priority: int = 0) -> Future:
        """Future for `key`'s job — the existing one if it is queued or running.
        A lower `priority` number moves a queued job ahead."""
        with self._lock:
            self._start_locked()
            task = self._tasks.get(key)
            if task is None:
                task = _Task(key, arg, priority)
                self._tasks[key] = task
                heapq.heappush(self._heap, (priority, next(self._seq), key))
            elif task.run_id is None and priority < task.priority:
                task.priority = priority
                heapq.heappush(self._heap, (priority, next(self._seq), key))
            self._pump_locked()
            return task.future

    def busy(self) -> Dict[str, int]:
        with self._lock:
            return {'running': len(self._runs), 'queued': len(self._tasks) - len(self._runs)}

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            workers, task_q = list(self._workers), self._task_q
        for _ in workers:
            try:
                task_q.put_nowait(None)
            except Exception:
                pass
        for p in workers:
            p.join(timeout=2)
            if p.is_alive():
                p.terminate()
        if self._local is not None:
            self._local.shutdown(wait=False)

    # ── dispatch ──────────────────────────────────────────────────────────────

    def _start_locked(self) -> None:
        if self._started:
            return
        self._started = True
        if self._processes:
            self._ctx = multiprocessing.get_context('spawn')
            self._spawn_workers_locked()
            threading.Thread(target=self._read_results, name='pool-results', daemon=True).start()
        else:
            self._local = ThreadPoolExecutor(max_workers=self._threads, thread_name_prefix='scrape')
        threading.Thread(target=self._watchdog, name='pool-watchdog', daemon=True).start()
        log.info('scrape pool started: %s (up to %d sources at once)', self.mode, self.capacity)

    def _spawn_workers_locked(self) -> None:
        self._task_q = self._ctx.Queue()
        self._result_q = self._ctx.Queue()
        self._workers = []
        for i in range(self._processes):
            p = self._ctx.Process(
                target=_worker_main,
                args=(self._task_q, self._result_q, self._threads, self._job, self._nice),
                name=f'scrape-worker-{i}', daemon=True,
            )
            p.start()
            self._workers.append(p)

    def _pump_locked(self) -> None:
        while len(self._runs) < self.capacity and self._heap:
            priority, _, key = heapq.heappop(self._heap)
            task = self._tasks.get(key)
            # Stale heap entries: already dispatched, finished, or re-queued higher.
            if task is None or task.run_id is not None or task.priority != priority:
                continue
            task.run_id = next(self._seq)
            self._runs[task.run_id] = task
            if self._processes:
                self._task_q.put((task.run_id, task.arg))
            else:
                self._local.submit(self._run_local, task.run_id, task.arg)

    def _run_local(self, run_id: int, arg: Any) -> None:
        with self._lock:
            task = self._runs.get(run_id)
            if task is None:
                return  # timed out or failed while waiting for a thread
            task.started_at = time.monotonic()
        try:
            result = self._job(arg)
        except BaseException as exc:
            self._finish(run_id, False, exc)
        else:
            self._finish(run_id, True, result)

    def _finish(self, run_id: int, ok: bool, value: Any) -> None:
        with self._lock:
            task = self._runs.get(run_id)
        if task is None:
            return  # already timed out or failed by a restart
        # on_done runs while the key is still registered, so the caller's cache
        # is updated before anyone can submit the key again.
        if ok and self._on_done is not None:
            try:
                self._on_done(task.key, value)
            except Exception:
                log.exception('on_done failed for %s', task.key)
        with self._lock:
            if self._runs.pop(run_id, None) is None:
                return
            self._tasks.pop(task.key, None)
            self._pump_locked()
        if ok:
            task.future.set_result(value)
        else:
            task.future.set_exception(value if isinstance(value, BaseException) else RuntimeError(value))

    # ── process mode ──────────────────────────────────────────────────────────

    def _read_results(self) -> None:
        while not self._stopping:
            result_q = self._result_q
            try:
                msg = result_q.get(timeout=1)
            except queue.Empty:
                continue
            except (EOFError, OSError, ValueError):
                time.sleep(0.2)  # queue replaced during a restart
                continue
            if msg[0] == 'start':
                with self._lock:
                    task = self._runs.get(msg[1])
                    if task is not None:
                        # The timeout counts from when a worker picks the job
                        # up, not from when it was queued behind others.
                        task.started_at = time.monotonic()
                continue
            _, run_id, ok, payload = msg
            if ok:
                try:
                    value = pickle.loads(payload)
                except Exception as exc:
                    ok, value = False, exc
            else:
                value = RuntimeError(payload)
            self._finish(run_id, ok, value)

    def _restart_workers(self, reason: str) -> None:
        with self._lock:
            if self._stopping:
                return
            failed = list(self._runs.values())
            for task in failed:
                self._runs.pop(task.run_id, None)
                self._tasks.pop(task.key, None)
            old = self._workers
            self._spawn_workers_locked()
            self._pump_locked()
        for p in old:
            if p.is_alive():
                p.terminate()
        log.error('scrape worker restart (%s): %d running sources failed and will be retried',
                  reason, len(failed))
        for task in failed:
            task.future.set_exception(WorkerDied(reason))

    # ── watchdog ──────────────────────────────────────────────────────────────

    def _watchdog(self) -> None:
        while not self._stopping:
            time.sleep(_WATCHDOG_SECONDS)
            now = time.monotonic()
            with self._lock:
                # Only jobs actually running: one waiting for a thread (behind a
                # job that timed out but is still finishing) is not overdue.
                overdue = [t for t in self._runs.values()
                           if t.started_at and now - t.started_at > self._job_timeout]
                dead = [p for p in self._workers if not p.is_alive()] if self._processes else []
            for task in overdue:
                log.warning('source %s timed out after %.0f s', task.key, self._job_timeout)
                self._finish(task.run_id, False, JobTimeout(f'{task.key}: over {self._job_timeout:.0f} s'))
            if dead and not self._stopping:
                self._restart_workers(f'{len(dead)} worker process(es) exited '
                                      f'(exit code {dead[0].exitcode})')
