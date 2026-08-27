"""High-performance HTTP session pooling, AIMD concurrency scaling, and tail-latency hedging."""

import collections
import logging
import queue
import socket
import threading
import time
from dataclasses import dataclass
from typing import Dict, Generator, List, Optional, Tuple, Union

import requests
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.connection import HTTPConnection
from urllib3.util.retry import Retry

logger = logging.getLogger("crunchyroll.session_pool")


@dataclass
class ConcurrencyConfig:
    """Configuration for concurrency, scaling, connection pool, and hedging."""
    min_workers: int = 8
    max_workers: int = 48
    initial_workers: int = 16
    aimd_enabled: bool = True
    hedging_enabled: bool = True
    hedge_factor: float = 2.0  # multiplier of median latency to trigger hedge
    hedge_min_delay: float = 1.5  # minimum delay in seconds before hedging
    max_retries: int = 5
    backoff_factor: float = 1.5
    pool_size: int = 64
    timeout: int = 20
    chunk_size: int = 262144  # 256 KB read buffer


class TCPKeepAliveAdapter(HTTPAdapter):
    """Custom HTTPAdapter configuring TCP Keep-Alive and TCP_NODELAY."""

    def init_poolmanager(self, *args, **kwargs):
        socket_options = list(HTTPConnection.default_socket_options)
        if hasattr(socket, "SO_KEEPALIVE"):
            socket_options.append((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1))
        if hasattr(socket, "IPPROTO_TCP") and hasattr(socket, "TCP_NODELAY"):
            socket_options.append((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1))
        kwargs["socket_options"] = socket_options
        super().init_poolmanager(*args, **kwargs)


class AIMDConcurrencyScaler:
    """Additive Increase / Multiplicative Decrease (AIMD) concurrency controller.
    
    Dynamically tunes active worker count between min_workers and max_workers
    based on real-time segment download throughput, error rates, and latency.
    """

    def __init__(
        self,
        min_workers: int = 8,
        max_workers: int = 48,
        initial_workers: int = 16,
        window_size: int = 10,
    ):
        self.min_workers = min_workers
        self.max_workers = max_workers
        self._current_workers = max(min_workers, min(max_workers, initial_workers))
        self.window_size = window_size

        self._lock = threading.RLock()
        self._recent_latencies: collections.deque = collections.deque(maxlen=50)
        self._recent_sizes: collections.deque = collections.deque(maxlen=50)
        self._window_durations: List[float] = []
        self._window_bytes: List[int] = []
        self._window_errors: int = 0
        self._prev_throughput_mb_s: float = 0.0

        self._total_success: int = 0
        self._total_failures: int = 0

    @property
    def current_workers(self) -> int:
        with self._lock:
            return self._current_workers

    def get_current_workers(self) -> int:
        return self.current_workers

    def record_success(self, duration: float, size_bytes: int) -> int:
        """Record a successful segment download and run AIMD adjustment if window is full."""
        with self._lock:
            self._total_success += 1
            self._recent_latencies.append(duration)
            self._recent_sizes.append(size_bytes)
            self._window_durations.append(duration)
            self._window_bytes.append(size_bytes)

            if len(self._window_durations) >= self.window_size:
                total_time = max(sum(self._window_durations), 0.001)
                total_mb = sum(self._window_bytes) / (1024 * 1024)
                avg_throughput = total_mb / (total_time / len(self._window_durations))

                # Additive Increase: If zero errors and throughput improved or stayed high
                if self._window_errors == 0:
                    if avg_throughput >= self._prev_throughput_mb_s * 0.95:
                        self._current_workers = min(self.max_workers, self._current_workers + 2)
                    elif avg_throughput < self._prev_throughput_mb_s * 0.70 and self._current_workers > self.min_workers:
                        # Slight decay if throughput degraded significantly despite no errors
                        self._current_workers = max(self.min_workers, self._current_workers - 1)
                else:
                    # Multiplicative Decrease: errors were encountered during the window
                    self._current_workers = max(self.min_workers, int(self._current_workers * 0.75))

                self._prev_throughput_mb_s = avg_throughput
                self._window_durations.clear()
                self._window_bytes.clear()
                self._window_errors = 0

            return self._current_workers

    def record_failure(self, status_code: int = 0) -> int:
        """Record a segment download failure and immediately backoff concurrency."""
        with self._lock:
            self._total_failures += 1
            self._window_errors += 1
            # Multiplicative Decrease immediately on error / rate-limiting
            self._current_workers = max(self.min_workers, int(self._current_workers * 0.75))
            return self._current_workers

    def get_median_latency(self) -> float:
        """Return median latency in seconds of recent segment downloads (for hedging)."""
        with self._lock:
            if not self._recent_latencies:
                return 1.0
            sorted_lats = sorted(self._recent_latencies)
            mid = len(sorted_lats) // 2
            if len(sorted_lats) % 2 == 1:
                return sorted_lats[mid]
            return (sorted_lats[mid - 1] + sorted_lats[mid]) / 2.0

    def get_stats(self) -> Dict[str, Union[int, float]]:
        with self._lock:
            med_lat = self.get_median_latency()
            return {
                "current_workers": self._current_workers,
                "total_success": self._total_success,
                "total_failures": self._total_failures,
                "median_latency_s": round(med_lat, 3),
                "last_throughput_mb_s": round(self._prev_throughput_mb_s, 2),
            }


class SessionPool:
    """Thread-safe persistent HTTP session pool with Keep-Alive, retries, and metrics."""

    DEFAULT_HEADERS = {
        "Origin": "https://static.crunchyroll.com",
        "Referer": "https://static.crunchyroll.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
    }

    def __init__(
        self,
        max_pool_size: int = 64,
        max_retries: int = 5,
        backoff_factor: float = 1.5,
        timeout: int = 20,
        config: Optional[ConcurrencyConfig] = None,
    ):
        self.config = config or ConcurrencyConfig(
            pool_size=max_pool_size,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            timeout=timeout,
        )
        self.max_pool_size = self.config.pool_size
        self.max_retries = self.config.max_retries
        self.backoff_factor = self.config.backoff_factor
        self.timeout = self.config.timeout

        self.scaler = AIMDConcurrencyScaler(
            min_workers=self.config.min_workers,
            max_workers=self.config.max_workers,
            initial_workers=self.config.initial_workers,
        )

        self._session = requests.Session()
        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=self.backoff_factor,
            status_forcelist=[420, 429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = TCPKeepAliveAdapter(
            pool_connections=self.max_pool_size,
            pool_maxsize=self.max_pool_size,
            max_retries=retry_strategy,
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._session.headers.update(self.DEFAULT_HEADERS)

        self._closed = False
        self._lock = threading.Lock()

    def get_session(self) -> requests.Session:
        """Return the underlying requests.Session."""
        return self._session

    def get_recommended_workers(self) -> int:
        """Query current optimal worker concurrency from AIMD scaler."""
        if self.config.aimd_enabled:
            return self.scaler.get_current_workers()
        return self.config.initial_workers

    def download_segment(
        self,
        url: str,
        timeout: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> bytes:
        """Download a single media segment into bytes with retries and metrics tracking."""
        t_out = timeout or self.timeout
        req_headers = dict(self.DEFAULT_HEADERS)
        if headers:
            req_headers.update(headers)

        attempt = 0
        last_exception: Optional[Exception] = None
        while attempt < self.max_retries:
            start_t = time.time()
            try:
                resp = self._session.get(url, headers=req_headers, timeout=t_out)
                duration = time.time() - start_t

                if resp.status_code == 200:
                    data = resp.content
                    self.scaler.record_success(duration, len(data))
                    return data
                elif resp.status_code in (420, 429):
                    self.scaler.record_failure(resp.status_code)
                    wait_time = (self.backoff_factor ** attempt) * 2.0
                    time.sleep(wait_time)
                elif 400 <= resp.status_code < 500:
                    # Immediate failure on non-retryable 4xx client errors (e.g. 404 Not Found)
                    self.scaler.record_failure(resp.status_code)
                    raise RuntimeError(f"HTTP {resp.status_code} client error: {url}")
                else:
                    self.scaler.record_failure(resp.status_code)
                    if attempt < self.max_retries - 1:
                        time.sleep(self.backoff_factor * attempt)
            except Exception as e:
                last_exception = e
                duration = time.time() - start_t
                self.scaler.record_failure(0)
                if attempt < self.max_retries - 1:
                    time.sleep(self.backoff_factor * attempt)

            attempt += 1

        err_msg = f"Failed to download segment after {self.max_retries} attempts: {url}"
        if last_exception:
            err_msg += f" (Last error: {last_exception})"
        raise RuntimeError(err_msg)

    def download_segment_stream(
        self,
        url: str,
        timeout: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        chunk_size: Optional[int] = None,
    ) -> Generator[bytes, None, None]:
        """Stream a media segment chunk by chunk."""
        t_out = timeout or self.timeout
        c_size = chunk_size or self.config.chunk_size
        req_headers = dict(self.DEFAULT_HEADERS)
        if headers:
            req_headers.update(headers)

        start_t = time.time()
        total_size = 0
        try:
            with self._session.get(url, headers=req_headers, stream=True, timeout=t_out) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=c_size):
                    if chunk:
                        total_size += len(chunk)
                        yield chunk
            duration = time.time() - start_t
            self.scaler.record_success(duration, total_size)
        except Exception as e:
            self.scaler.record_failure(0)
            raise e

    def download_segment_hedged(
        self,
        url: str,
        timeout: Optional[int] = None,
        hedge_delay: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> bytes:
        """Download a segment with tail-latency hedging against slow CDN stragglers."""
        if not self.config.hedging_enabled:
            return self.download_segment(url, timeout=timeout, headers=headers)

        t_out = timeout or self.timeout
        delay = hedge_delay
        if delay is None:
            med_lat = self.scaler.get_median_latency()
            delay = max(self.config.hedge_min_delay, med_lat * self.config.hedge_factor)

        res_queue: queue.Queue = queue.Queue(maxsize=2)
        stop_event = threading.Event()

        def _worker(worker_id: int):
            try:
                data = self.download_segment(url, timeout=t_out, headers=headers)
                if not stop_event.is_set():
                    res_queue.put(("ok", data, worker_id))
            except Exception as e:
                if not stop_event.is_set():
                    res_queue.put(("err", e, worker_id))

        # Start primary request
        t1 = threading.Thread(target=_worker, args=(1,), daemon=True)
        t1.start()

        # Wait up to hedge_delay for primary to finish
        try:
            status, val, _ = res_queue.get(timeout=delay)
            if status == "ok":
                stop_event.set()
                return val
        except queue.Empty:
            # Primary is lagging; launch speculative secondary request
            t2 = threading.Thread(target=_worker, args=(2,), daemon=True)
            t2.start()

        # Wait for either worker to complete
        remaining_timeout = max(1.0, float(t_out) - delay)
        try:
            status, val, _ = res_queue.get(timeout=remaining_timeout)
            if status == "ok":
                stop_event.set()
                return val
            else:
                # If first resulted in error, try waiting briefly for the other worker
                try:
                    status2, val2, _ = res_queue.get(timeout=min(remaining_timeout, 3.0))
                    if status2 == "ok":
                        stop_event.set()
                        return val2
                except queue.Empty:
                    pass
                stop_event.set()
                raise val
        except queue.Empty:
            stop_event.set()
            raise TimeoutError(f"Hedged segment download timed out for {url}")

    def close(self):
        """Close the underlying session and free connections."""
        with self._lock:
            if not self._closed:
                self._session.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
