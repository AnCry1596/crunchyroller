"""
tests/test_challenger_stress.py

Empirical Stress Testing & Adversarial Verification Suite by Challenger 1.
Tests:
1. High Concurrency Bursts (48-96 parallel workers) on SessionPool, AIMDConcurrencyScaler, StreamAssembler.
2. Tail-Latency Hedging under simulated delay spikes and packet drops / socket resets.
3. Rapid Rate-Limiting (HTTP 420/429) Backoff, Floor Bounding, and AIMD Ramp-Up Recovery.
4. Download Throughput Acceleration Benchmarking, Race Conditions, and Deadlock Resistance.
"""

import collections
import http.server
import io
import json
import os
import queue
import random
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import requests
from crunchyroll.downloader import (
    build_url,
    download_part,
    download_parts_optimized,
)
from crunchyroll.session_pool import (
    AIMDConcurrencyScaler,
    ConcurrencyConfig,
    SessionPool,
)
from crunchyroll.stream_assembler import StreamAssembler
from tests.mock_server import MockCrunchyrollServer, SAMPLE_PSSH_B64


class StressMockServerHandler(http.server.BaseHTTPRequestHandler):
    """Configurable HTTP server handler for high-stress and fault-injection testing."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Quiet logging

    @property
    def server_inst(self) -> "StressMockServer":
        return self.server  # type: ignore

    def do_GET(self) -> None:
        with self.server_inst.lock:
            self.server_inst.total_requests += 1
            req_id = self.server_inst.total_requests

        path = self.path

        # 1. Check custom route overrides
        if path in self.server_inst.routes:
            handler_fn = self.server_inst.routes[path]
            handler_fn(self)
            return

        # 2. Check rate limit burst injection
        with self.server_inst.lock:
            if self.server_inst.rate_limit_burst_count > 0:
                self.server_inst.rate_limit_burst_count -= 1
                self._send_status(420, b"Rate Limited (420)")
                return

        # 3. Check flaky error rate
        if self.server_inst.flaky_error_rate > 0.0:
            if random.random() < self.server_inst.flaky_error_rate:
                self._send_status(500, b"Simulated 500 Error")
                return

        # 4. Check delay spike injection
        if self.server_inst.delay_spike_probability > 0.0:
            if random.random() < self.server_inst.delay_spike_probability:
                time.sleep(self.server_inst.delay_spike_seconds)
        elif self.server_inst.fixed_delay > 0.0:
            time.sleep(self.server_inst.fixed_delay)

        # 5. Check socket reset injection
        if self.server_inst.socket_reset_probability > 0.0:
            if random.random() < self.server_inst.socket_reset_probability:
                try:
                    self.request.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                self.close_connection = True
                return

        # Standard segment payload generation
        if "init" in path:
            payload = b"\x00\x00\x00\x18ftypiso5\x00\x00\x02\x00iso5mp41\x00\x00\x00\x08moov"
        else:
            # Generate deterministic payload based on path or index
            seg_num = 1
            parts = path.split("_")
            for p in parts:
                num_str = p.replace(".mp4", "")
                if num_str.isdigit():
                    seg_num = int(num_str)
                    break
            payload = f"SEGMENT_{seg_num:05d}_DATA_".encode("ascii") + (b"X" * 1024)

        self._send_bytes(payload, "video/mp4")

    def _send_status(self, code: int, body: bytes) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_bytes(self, data: bytes, content_type: str = "application/octet-stream") -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


class StressMockServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        super().__init__((host, port), StressMockServerHandler)
        self.host, self.port = self.server_address
        self.base_url = f"http://{self.host}:{self.port}"
        self.lock = threading.Lock()
        self.total_requests = 0
        self.rate_limit_burst_count = 0
        self.flaky_error_rate = 0.0
        self.delay_spike_probability = 0.0
        self.delay_spike_seconds = 0.5
        self.fixed_delay = 0.0
        self.socket_reset_probability = 0.0
        self.routes: Dict[str, Any] = {}
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "StressMockServer":
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def reset_state(self) -> None:
        with self.lock:
            self.total_requests = 0
            self.rate_limit_burst_count = 0
            self.flaky_error_rate = 0.0
            self.delay_spike_probability = 0.0
            self.delay_spike_seconds = 0.5
            self.fixed_delay = 0.0
            self.socket_reset_probability = 0.0
            self.routes.clear()


class TestChallengerHighConcurrency(unittest.TestCase):
    """Scope 1: High Concurrency Bursts (48-96 parallel workers)."""

    def setUp(self):
        self.server = StressMockServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_challenger_c_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_c1_1_stream_assembler_96_workers_random_burst(self):
        """Subject StreamAssembler to 96 concurrent threads pushing 512 chunks in randomized order."""
        total_chunks = 512
        out_p = os.path.join(self.tmp_dir, "burst_96w_512.mp4")
        assembler = StreamAssembler(
            output_path=out_p,
            total_segments=total_chunks,
            max_in_flight_mb=8,
            start_index=1,
        )

        indices = list(range(1, total_chunks + 1))
        random.seed(42)
        random.shuffle(indices)

        def _push(idx: int):
            chunk_data = f"CHUNK_{idx:05d}_DATA#".encode("ascii")
            assembler.add_segment(idx, chunk_data)

        start_t = time.perf_counter()
        with ThreadPoolExecutor(max_workers=96) as executor:
            futures = [executor.submit(_push, i) for i in indices]
            for f in as_completed(futures):
                f.result()

        assembler.finish()
        elapsed = time.perf_counter() - start_t

        self.assertEqual(assembler.written_segments, total_chunks)
        with open(out_p, "rb") as f:
            content = f.read()

        expected = "".join(f"CHUNK_{i:05d}_DATA#" for i in range(1, total_chunks + 1)).encode("ascii")
        self.assertEqual(content, expected)
        self.assertLess(elapsed, 5.0, f"96-worker assembly took too long: {elapsed:.2f}s")

    def test_c1_2_stream_assembler_strict_memory_backpressure_64_workers(self):
        """Subject StreamAssembler to strict 1MB RAM limit under 64 workers sending 8MB in reverse order."""
        total_chunks = 64
        # Each chunk is 128 KB -> 64 * 128 KB = 8 MB total data
        chunk_size = 128 * 1024
        out_p = os.path.join(self.tmp_dir, "backpressure_64w.mp4")

        # 1 MB max memory capacity
        assembler = StreamAssembler(
            output_path=out_p,
            total_segments=total_chunks,
            max_in_flight_mb=1,
            start_index=1,
        )

        chunks = {i: f"[{i:03d}]".encode("ascii") + (b"A" * (chunk_size - 5)) for i in range(1, total_chunks + 1)}

        max_observed_mem = 0
        mem_lock = threading.Lock()

        def _push_reverse(idx: int):
            nonlocal max_observed_mem
            assembler.add_segment(idx, chunks[idx])
            with mem_lock:
                cur = assembler.current_buffered_bytes
                if cur > max_observed_mem:
                    max_observed_mem = cur

        # Submit in reverse order: chunk 64 down to 1
        with ThreadPoolExecutor(max_workers=64) as executor:
            futures = [executor.submit(_push_reverse, i) for i in reversed(range(1, total_chunks + 1))]
            for f in as_completed(futures):
                f.result()

        assembler.finish()

        # Check total written size
        file_sz = os.path.getsize(out_p)
        self.assertEqual(file_sz, total_chunks * chunk_size)
        # Verify assembler buffered memory was properly bounded (never grew to 8 MB)
        self.assertLessEqual(max_observed_mem, 2 * 1024 * 1024, "Buffer exceeded strict backpressure capacity!")

    def test_c1_3_session_pool_aimd_scaler_64_thread_contention(self):
        """Stress-test AIMDConcurrencyScaler under 64 concurrent threads hammering successes and failures."""
        scaler = AIMDConcurrencyScaler(min_workers=8, max_workers=48, initial_workers=16, window_size=5)

        def _hammer(worker_id: int):
            for i in range(100):
                if (worker_id + i) % 7 == 0:
                    scaler.record_failure(status_code=429)
                else:
                    scaler.record_success(duration=0.005 + (i * 0.0001), size_bytes=262144)
                _ = scaler.current_workers
                _ = scaler.get_median_latency()
                _ = scaler.get_stats()

        threads = [threading.Thread(target=_hammer, args=(w,)) for w in range(64)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = scaler.get_stats()
        self.assertGreaterEqual(stats["current_workers"], 8)
        self.assertLessEqual(stats["current_workers"], 48)
        self.assertEqual(stats["total_success"] + stats["total_failures"], 64 * 100)

    def test_c1_4_session_pool_48_parallel_burst_downloads(self):
        """Subject SessionPool to 48 parallel HTTP requests simultaneously."""
        cfg = ConcurrencyConfig(
            min_workers=8,
            max_workers=48,
            initial_workers=48,
            pool_size=64,
            timeout=5,
        )
        pool = SessionPool(config=cfg)

        def _download_task(idx: int):
            url = f"{self.server.base_url}/media/seg_{idx:05d}.mp4"
            data = pool.download_segment(url)
            self.assertTrue(data.startswith(f"SEGMENT_{idx:05d}_DATA_".encode("ascii")))
            return len(data)

        try:
            with ThreadPoolExecutor(max_workers=48) as executor:
                futures = [executor.submit(_download_task, i) for i in range(1, 49)]
                results = [f.result(timeout=10.0) for f in futures]
            self.assertEqual(len(results), 48)
        finally:
            pool.close()

    def test_c1_5_download_parts_optimized_48_workers_end_to_end(self):
        """Run download_parts_optimized on 60 segments with 48 concurrent workers against mock server."""
        cfg = ConcurrencyConfig(
            min_workers=8,
            max_workers=48,
            initial_workers=48,
            pool_size=64,
        )
        timeline = list(range(1, 61))
        out_p = os.path.join(self.tmp_dir, "optimized_48w.mp4")

        # download_parts_optimized uses $RepresentationID$_segment_$Number$.mp4
        saved_file = download_parts_optimized(
            base_url=f"{self.server.base_url}/media/",
            rep_id="v1",
            timeline=timeline,
            keys=None,
            output_filename=out_p,
            concurrency_config=cfg,
            media_pattern="seg_$Number$.mp4",
            init_pattern="init.mp4",
        )
        self.assertTrue(os.path.exists(saved_file))
        sz = os.path.getsize(saved_file)
        self.assertGreater(sz, 60 * 1000)

    def test_c1_6_stream_assembler_1000_segments_out_of_order_96_threads(self):
        """Subject StreamAssembler to 1000 segments pushed across 96 concurrent threads in random order."""
        total = 1000
        out_p = os.path.join(self.tmp_dir, "large_1000_chunks.mp4")
        assembler = StreamAssembler(out_p, total_segments=total, max_in_flight_mb=16)

        indices = list(range(1, total + 1))
        random.seed(99)
        random.shuffle(indices)

        def _push(idx: int):
            assembler.add_segment(idx, f"C{idx:06d}:".encode("ascii"))

        with ThreadPoolExecutor(max_workers=96) as executor:
            futures = [executor.submit(_push, i) for i in indices]
            for f in as_completed(futures):
                f.result()

        assembler.finish()
        self.assertEqual(assembler.written_segments, total)
        expected = "".join(f"C{i:06d}:" for i in range(1, total + 1)).encode("ascii")
        with open(out_p, "rb") as f:
            data = f.read()
        self.assertEqual(data, expected)


class TestChallengerTailLatencyHedging(unittest.TestCase):
    """Scope 2: Tail-latency hedging under artificial network delay spikes and drops."""

    def setUp(self):
        self.server = StressMockServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_challenger_h_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_c2_1_tail_latency_spike_hedging_acceleration(self):
        """Simulate severe 1.2s delay on primary request; verify secondary hedged request finishes in ~0.3s."""
        req_count = 0
        req_lock = threading.Lock()

        def _straggler_handler(handler):
            nonlocal req_count
            with req_lock:
                req_count += 1
                c = req_count

            if c == 1:
                # Primary worker stalls for 1.2 seconds
                time.sleep(1.2)
                handler._send_bytes(b"PAYLOAD_FROM_PRIMARY_STRAGGLER")
            else:
                # Speculative secondary worker finishes in 10ms
                time.sleep(0.01)
                handler._send_bytes(b"PAYLOAD_FROM_FAST_SECONDARY")

        self.server.routes["/media/straggler.mp4"] = _straggler_handler

        cfg = ConcurrencyConfig(
            hedging_enabled=True,
            hedge_min_delay=0.1,  # Hedge triggers after 100ms
            timeout=5,
        )
        pool = SessionPool(config=cfg)
        try:
            start_t = time.perf_counter()
            data = pool.download_segment_hedged(f"{self.server.base_url}/media/straggler.mp4", hedge_delay=0.1)
            elapsed = time.perf_counter() - start_t

            self.assertIn(data, [b"PAYLOAD_FROM_FAST_SECONDARY", b"PAYLOAD_FROM_PRIMARY_STRAGGLER"])
            # Should finish much faster than 1.2s straggler delay
            self.assertLess(elapsed, 0.7, f"Hedging did not accelerate straggler! Took {elapsed:.3f}s")
            self.assertGreaterEqual(req_count, 2, "Speculative secondary request was not triggered")
        finally:
            pool.close()

    def test_c2_2_primary_packet_drop_secondary_recovers(self):
        """Simulate primary request abruptly dropping socket; secondary request recovers cleanly."""
        req_count = 0
        req_lock = threading.Lock()

        def _drop_handler(handler):
            nonlocal req_count
            with req_lock:
                req_count += 1
                c = req_count

            if c == 1:
                # Primary abruptly aborts
                try:
                    handler.request.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                handler.close_connection = True
            else:
                handler._send_bytes(b"RECOVERED_BY_SECONDARY_HEDGE")

        self.server.routes["/media/drop.mp4"] = _drop_handler

        cfg = ConcurrencyConfig(
            hedging_enabled=True,
            hedge_min_delay=0.05,
            timeout=5,
        )
        pool = SessionPool(config=cfg)
        try:
            data = pool.download_segment_hedged(f"{self.server.base_url}/media/drop.mp4", hedge_delay=0.05)
            self.assertEqual(data, b"RECOVERED_BY_SECONDARY_HEDGE")
        finally:
            pool.close()

    def test_c2_3_hedging_duplicate_delivery_assembler_safety(self):
        """Verify that when both hedged workers return the same chunk, StreamAssembler discards duplicate."""
        out_p = os.path.join(self.tmp_dir, "hedged_dup.mp4")
        assembler = StreamAssembler(out_p, total_segments=3)

        assembler.add_segment(1, b"PART1_")
        # Duplicate delivery of segment 2 (simulating both primary and secondary hedge completing)
        assembler.add_segment(2, b"PART2_")
        assembler.add_segment(2, b"PART2_")  # Duplicate
        assembler.add_segment(3, b"PART3_")
        assembler.finish()

        with open(out_p, "rb") as f:
            data = f.read()
        self.assertEqual(data, b"PART1_PART2_PART3_")

    def test_c2_4_burst_hedging_multiple_concurrent_stragglers(self):
        """Simulate 30 concurrent hedged downloads where 30% of requests suffer 800ms delays."""
        with self.server.lock:
            self.server.delay_spike_probability = 0.30
            self.server.delay_spike_seconds = 0.80

        cfg = ConcurrencyConfig(
            hedging_enabled=True,
            hedge_min_delay=0.08,
            pool_size=64,
            timeout=5,
        )
        pool = SessionPool(config=cfg)

        def _fetch_hedged(idx: int):
            return pool.download_segment_hedged(
                f"{self.server.base_url}/media/seg_{idx:05d}.mp4",
                hedge_delay=0.08,
            )

        try:
            start_t = time.perf_counter()
            with ThreadPoolExecutor(max_workers=30) as executor:
                futures = [executor.submit(_fetch_hedged, i) for i in range(1, 31)]
                results = [f.result(timeout=10.0) for f in futures]
            elapsed = time.perf_counter() - start_t

            self.assertEqual(len(results), 30)
            # With hedging at 80ms, all 30 should complete well before 30 * 800ms or even a single 5s timeout
            self.assertLess(elapsed, 4.0, f"Hedged burst took too long: {elapsed:.2f}s")
        finally:
            pool.close()


class TestChallengerRateLimitBackoffAndRecovery(unittest.TestCase):
    """Scope 3: Rapid rate-limiting (HTTP 420/429) backoff and recovery."""

    def setUp(self):
        self.server = StressMockServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_challenger_r_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_c3_1_http_429_storm_with_exponential_backoff(self):
        """Inject a burst of 3 consecutive HTTP 429 errors; verify urllib3 retry resolves with valid payload."""
        with self.server.lock:
            self.server.rate_limit_burst_count = 3

        cfg = ConcurrencyConfig(
            max_retries=5,
            backoff_factor=0.2,
            timeout=5,
        )
        pool = SessionPool(config=cfg)
        try:
            start_t = time.perf_counter()
            data = pool.download_segment(f"{self.server.base_url}/media/seg_00001.mp4")
            elapsed = time.perf_counter() - start_t

            self.assertTrue(data.startswith(b"SEGMENT_00001_DATA_"))
            # Verified that urllib3 retry absorbed the 3 rate-limit responses and succeeded
            self.assertGreater(self.server.total_requests, 3)
            stats = pool.scaler.get_stats()
            self.assertEqual(stats["total_success"], 1)
        finally:
            pool.close()

    def test_c3_2_rapid_420_burst_aimd_floor_and_recovery_cycle(self):
        """Verify full cycle: initial concurrency -> 420 burst drops to min -> recovery ramps back up."""
        scaler = AIMDConcurrencyScaler(
            min_workers=8,
            max_workers=48,
            initial_workers=32,
            window_size=4,
        )
        self.assertEqual(scaler.current_workers, 32)

        # 1. First 420 failure -> drops to 24 (32 * 0.75)
        scaler.record_failure(status_code=420)
        self.assertEqual(scaler.current_workers, 24)

        # 2. Repeated failures hit floor of min_workers (8)
        for _ in range(10):
            scaler.record_failure(status_code=420)
        self.assertEqual(scaler.current_workers, 8)

        # 3. Clean recovery phase with high throughput
        for _ in range(40):
            scaler.record_success(duration=0.01, size_bytes=500000)

        # Concurrency must have scaled back up above the min floor
        self.assertGreater(scaler.current_workers, 8)
        self.assertLessEqual(scaler.current_workers, 48)

    def test_c3_3_multi_worker_flaky_rate_limit_resilience(self):
        """Run 16 concurrent workers downloading 32 segments with 15% random HTTP 500/420 flakiness."""
        with self.server.lock:
            self.server.flaky_error_rate = 0.15

        cfg = ConcurrencyConfig(
            min_workers=8,
            max_workers=16,
            initial_workers=16,
            max_retries=6,
            backoff_factor=1.1,
            timeout=5,
        )
        pool = SessionPool(config=cfg)
        out_p = os.path.join(self.tmp_dir, "flaky_out.mp4")

        total_segs = 32
        assembler = StreamAssembler(out_p, total_segments=total_segs)

        job_q: queue.Queue = queue.Queue()
        for i in range(1, total_segs + 1):
            job_q.put((i, f"{self.server.base_url}/media/seg_{i:05d}.mp4"))

        worker_errs: List[Exception] = []

        def _worker():
            while not job_q.empty() and not worker_errs:
                try:
                    idx, url = job_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    data = pool.download_segment(url)
                    assembler.add_segment(idx, data)
                except Exception as ex:
                    worker_errs.append(ex)
                    assembler.abort(ex)
                    break
                finally:
                    job_q.task_done()

        try:
            threads = [threading.Thread(target=_worker) for _ in range(16)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15.0)

            self.assertEqual(len(worker_errs), 0, f"Flaky network test failed with errors: {worker_errs}")
            assembler.finish()
            self.assertEqual(assembler.written_segments, total_segs)
        finally:
            pool.close()

    def test_c3_4_direct_420_outer_retry_and_backoff(self):
        """Mock server returns 420; test that SessionPool outer loop retries with exponential backoff and tracks failures."""
        calls = 0
        call_lock = threading.Lock()

        def _burst_420(handler):
            nonlocal calls
            with call_lock:
                calls += 1
                c = calls
            if c <= 2:
                handler._send_status(420, b"Rate Limited (420)")
            else:
                handler._send_bytes(b"RECOVERED_AFTER_2_RATE_LIMITS")

        self.server.routes["/media/rate_limit_test.mp4"] = _burst_420

        cfg = ConcurrencyConfig(
            max_retries=4,
            backoff_factor=0.1,  # Short backoff for fast test
            timeout=3,
        )
        pool = SessionPool(config=cfg)
        try:
            data = pool.download_segment(f"{self.server.base_url}/media/rate_limit_test.mp4")
            self.assertEqual(data, b"RECOVERED_AFTER_2_RATE_LIMITS")
            self.assertGreaterEqual(calls, 3)
        finally:
            pool.close()

    def test_c3_5_persistent_429_exhaustion_raises_runtime_error(self):
        """Server continuously returns 429; verify RuntimeError is raised after max_retries."""
        def _always_429(handler):
            handler._send_status(429, b"Too Many Requests")

        self.server.routes["/media/permanent_429.mp4"] = _always_429

        cfg = ConcurrencyConfig(
            max_retries=3,
            backoff_factor=0.05,
            timeout=2,
        )
        pool = SessionPool(config=cfg)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                pool.download_segment(f"{self.server.base_url}/media/permanent_429.mp4")
            self.assertIn("Failed to download segment after 3 attempts", str(ctx.exception))
        finally:
            pool.close()


class TestChallengerThroughputAndDeadlockSafety(unittest.TestCase):
    """Scope 4: Download throughput acceleration, zero race conditions, and zero deadlocks."""

    def setUp(self):
        self.server = StressMockServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_challenger_t_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_c4_1_throughput_acceleration_multifold_speedup(self):
        """Measure speedup of 48-worker pooled pipeline vs unpooled 1-worker baseline."""
        num_segments = 48
        # Simulate 5ms RTT per request
        self.server.fixed_delay = 0.005

        # 1. Unpooled Baseline (1 worker, connection close)
        start_t = time.perf_counter()
        base_bytes = 0
        for i in range(1, num_segments + 1):
            r = requests.get(f"{self.server.base_url}/media/seg_{i:05d}.mp4", headers={"Connection": "close"}, timeout=5)
            base_bytes += len(r.content)
        baseline_time = time.perf_counter() - start_t
        baseline_tp = (base_bytes / (1024 * 1024)) / baseline_time

        # 2. 48-worker Pooled StreamAssembler Pipeline
        out_p = os.path.join(self.tmp_dir, "tp_48w.mp4")
        cfg = ConcurrencyConfig(
            min_workers=48,
            max_workers=48,
            initial_workers=48,
            pool_size=64,
            aimd_enabled=False,
            hedging_enabled=False,
        )
        pool = SessionPool(config=cfg)

        start_t = time.perf_counter()
        assembler = StreamAssembler(out_p, total_segments=num_segments)
        job_q: queue.Queue = queue.Queue()
        for i in range(1, num_segments + 1):
            job_q.put((i, f"{self.server.base_url}/media/seg_{i:05d}.mp4"))

        def _worker():
            while not job_q.empty():
                try:
                    idx, url = job_q.get_nowait()
                except queue.Empty:
                    break
                data = pool.download_segment(url)
                assembler.add_segment(idx, data)
                job_q.task_done()

        threads = [threading.Thread(target=_worker) for _ in range(48)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assembler.finish()
        pooled_time = time.perf_counter() - start_t
        pooled_bytes = os.path.getsize(out_p)
        pooled_tp = (pooled_bytes / (1024 * 1024)) / pooled_time
        pool.close()

        speedup = pooled_tp / baseline_tp if baseline_tp > 0 else 1.0
        print(f"\n[Challenger Throughput] Baseline: {baseline_tp:.2f} MB/s | 48-Worker Pooled: {pooled_tp:.2f} MB/s | Speedup: {speedup:.2f}x")

        self.assertGreater(speedup, 2.0, f"Expected significant concurrency speedup, got {speedup:.2f}x")
        self.assertEqual(assembler.written_segments, num_segments)

    def test_c4_2_stress_rapid_lifecycle_pool_and_assembler_no_deadlocks(self):
        """Rapidly instantiate and teardown 30 SessionPool and StreamAssembler instances concurrently."""
        errors: List[Exception] = []

        def _cycle(cycle_id: int):
            try:
                cfg = ConcurrencyConfig(min_workers=8, max_workers=16, initial_workers=8, pool_size=16)
                with SessionPool(config=cfg) as pool:
                    p = os.path.join(self.tmp_dir, f"cycle_{cycle_id}.mp4")
                    with StreamAssembler(p, total_segments=4) as asm:
                        for i in range(1, 5):
                            d = pool.download_segment(f"{self.server.base_url}/media/seg_{i:05d}.mp4")
                            asm.add_segment(i, d)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_cycle, i) for i in range(30)]
            for f in as_completed(futures):
                f.result(timeout=10.0)

        self.assertEqual(len(errors), 0, f"Rapid lifecycle encountered errors: {errors}")

    def test_c4_3_assembler_abort_race_conditions_all_threads_unblock(self):
        """Ensure that when StreamAssembler aborts under heavy thread contention, zero threads hang/deadlock."""
        out_p = os.path.join(self.tmp_dir, "abort_race.mp4")
        assembler = StreamAssembler(out_p, total_segments=100, max_in_flight_mb=1)

        blocked_workers = 32
        finished_workers = 0
        lock = threading.Lock()

        def _contending_worker(worker_id: int):
            nonlocal finished_workers
            try:
                # Workers push chunks > next_expected_index so they block on memory
                assembler.add_segment(worker_id + 50, b"X" * (512 * 1024))
            except Exception:
                pass
            finally:
                with lock:
                    finished_workers += 1

        threads = [threading.Thread(target=_contending_worker, args=(i,)) for i in range(blocked_workers)]
        for t in threads:
            t.start()

        # Give threads time to enter wait condition
        time.sleep(0.1)

        # Abort the assembler
        assembler.abort(RuntimeError("Forced intentional abort"))

        for t in threads:
            t.join(timeout=2.0)
            self.assertFalse(t.is_alive(), "Worker thread deadlocked waiting on condition!")

        self.assertEqual(finished_workers, blocked_workers)

    def test_c4_4_concurrent_finish_abort_race(self):
        """Race finish() vs abort() vs add_segment() concurrently to guarantee no deadlock or crash."""
        out_p = os.path.join(self.tmp_dir, "finish_abort_race.mp4")
        assembler = StreamAssembler(out_p, total_segments=20, max_in_flight_mb=2)

        def _adder():
            for i in range(1, 21):
                try:
                    assembler.add_segment(i, b"SEG_DATA")
                except Exception:
                    pass

        def _finisher():
            time.sleep(0.01)
            try:
                assembler.finish()
            except Exception:
                pass

        def _aborter():
            time.sleep(0.01)
            try:
                assembler.abort(RuntimeError("Race abort"))
            except Exception:
                pass

        threads = [
            threading.Thread(target=_adder),
            threading.Thread(target=_adder),
            threading.Thread(target=_finisher),
            threading.Thread(target=_aborter),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)
            self.assertFalse(t.is_alive(), "Thread deadlocked during concurrent finish/abort race!")

    def test_c4_5_connection_reuse_and_tcp_pool_bounding(self):
        """Stress-test 64 threads downloading 192 total segments through single SessionPool."""
        cfg = ConcurrencyConfig(
            min_workers=16,
            max_workers=64,
            initial_workers=32,
            pool_size=64,
            timeout=5,
        )
        pool = SessionPool(config=cfg)

        downloaded_count = 0
        lock = threading.Lock()

        def _task(idx: int):
            nonlocal downloaded_count
            url = f"{self.server.base_url}/media/seg_{idx:05d}.mp4"
            data = pool.download_segment(url)
            self.assertGreater(len(data), 0)
            with lock:
                downloaded_count += 1

        try:
            with ThreadPoolExecutor(max_workers=64) as executor:
                futures = [executor.submit(_task, i) for i in range(1, 193)]
                for f in as_completed(futures):
                    f.result(timeout=10.0)

            self.assertEqual(downloaded_count, 192)
        finally:
            pool.close()


if __name__ == "__main__":
    unittest.main()
