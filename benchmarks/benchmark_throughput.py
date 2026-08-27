"""
Comprehensive Throughput & Concurrency Scaling Benchmark.

Measures download throughput (MB/s), latency, and scaling acceleration across
different worker pool sizes (1, 4, 8, 16, 32, 48 workers) comparing
pooled persistent streaming against an unpooled single-connection baseline.
"""

import http.server
import os
import queue
import socket
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is on sys.path for direct script execution
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import requests

from crunchyroll.session_pool import ConcurrencyConfig, SessionPool
from crunchyroll.stream_assembler import StreamAssembler


class BenchmarkServerHandler(http.server.BaseHTTPRequestHandler):
    """Ultra-fast, thread-safe HTTP request handler for synthetic segment streaming."""

    protocol_version = "HTTP/1.1"

    # 256 KB synthetic segment payload
    SEGMENT_PAYLOAD = b"\x00\x00\x00\x10moof\x00\x00\x00\x08mdat" + (b"\xaa\xbb\xcc\xdd" * 65530)
    INIT_PAYLOAD = b"\x00\x00\x00\x18ftypiso5\x00\x00\x02\x00iso5mp41\x00\x00\x00\x08moov"

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress request logging for maximum throughput

    def do_GET(self) -> None:
        # Simulate realistic fast CDN edge network latency (3ms RTT)
        time.sleep(0.003)

        data = self.INIT_PAYLOAD if "init" in self.path else self.SEGMENT_PAYLOAD
        try:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


class BenchmarkServer(http.server.ThreadingHTTPServer):
    """Threaded local HTTP server for benchmark execution with high TCP backlog."""
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        super().__init__((host, port), BenchmarkServerHandler)
        self.host, self.port = self.server_address
        self.base_url = f"http://{self.host}:{self.port}"
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "BenchmarkServer":
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)


def _benchmark_unpooled_baseline(base_url: str, num_segments: int) -> Tuple[float, int]:
    """Download segments using unpooled, single-threaded connection requests."""
    tmp_out = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp_path = tmp_out.name
    tmp_out.close()

    total_bytes = 0
    start_t = time.perf_counter()
    try:
        with open(tmp_path, "wb") as f:
            # Init segment
            r = requests.get(f"{base_url}/init.mp4", headers={"Connection": "close"}, timeout=10)
            f.write(r.content)
            total_bytes += len(r.content)

            # Sequential unpooled segments
            for i in range(1, num_segments + 1):
                r = requests.get(f"{base_url}/seg_{i:05d}.mp4", headers={"Connection": "close"}, timeout=10)
                f.write(r.content)
                total_bytes += len(r.content)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    elapsed = time.perf_counter() - start_t
    return elapsed, total_bytes


def _benchmark_pooled_streaming(
    base_url: str,
    num_segments: int,
    worker_count: int,
) -> Tuple[float, int]:
    """Download segments using SessionPool and StreamAssembler with specified worker pool."""
    tmp_out = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp_path = tmp_out.name
    tmp_out.close()

    cfg = ConcurrencyConfig(
        min_workers=worker_count,
        max_workers=worker_count,
        initial_workers=worker_count,
        pool_size=max(64, worker_count * 2),
        aimd_enabled=False,  # Fixed concurrency for controlled benchmark
        hedging_enabled=False,
    )
    pool = SessionPool(config=cfg)

    total_bytes = 0
    start_t = time.perf_counter()
    try:
        init_data = pool.download_segment(f"{base_url}/init.mp4")
        assembler = StreamAssembler(
            output_path=tmp_path,
            total_segments=num_segments,
            max_in_flight_mb=32,
            start_index=1,
        )
        assembler.write_init(init_data)
        total_bytes += len(init_data)

        job_queue: queue.Queue = queue.Queue()
        for i in range(1, num_segments + 1):
            job_queue.put((i, f"{base_url}/seg_{i:05d}.mp4"))

        bytes_lock = threading.Lock()
        worker_error: List[Exception] = []

        def _worker():
            nonlocal total_bytes
            while not job_queue.empty() and not worker_error:
                try:
                    idx, url = job_queue.get_nowait()
                except queue.Empty:
                    break

                try:
                    seg_data = pool.download_segment(url)
                    assembler.add_segment(idx, seg_data)
                    with bytes_lock:
                        total_bytes += len(seg_data)
                except Exception as ex:
                    worker_error.append(ex)
                    assembler.abort(ex)
                    break
                finally:
                    job_queue.task_done()

        threads = [threading.Thread(target=_worker, daemon=True) for _ in range(worker_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if worker_error:
            raise worker_error[0]

        assembler.finish()
    finally:
        pool.close()
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    elapsed = time.perf_counter() - start_t
    return elapsed, total_bytes


def render_ascii_bar(val: float, max_val: float, width: int = 24) -> str:
    """Render an ASCII progress/comparison bar."""
    if max_val <= 0:
        return " " * width
    ratio = min(1.0, max(0.0, val / max_val))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def run_throughput_benchmark(num_segments: int = 48) -> Dict[str, Any]:
    """Execute the full throughput & worker scaling benchmark suite."""
    print("=" * 88)
    print("CRUNCHYROLLER HIGH-PERFORMANCE THROUGHPUT & WORKER SCALING BENCHMARK")
    print("=" * 88)
    print(f"Test Parameters: {num_segments} Segments (256 KB each) per run | Target: 12.0 MB / run")
    print("Initializing benchmark streaming server on 127.0.0.1...")

    server = BenchmarkServer().start()
    results: List[Dict[str, Any]] = []

    try:
        # 1. Baseline: Unpooled Single Worker
        sys.stdout.write("Running Baseline (1 Worker, Unpooled)... ")
        sys.stdout.flush()
        base_time, base_bytes = _benchmark_unpooled_baseline(server.base_url, num_segments)
        base_mb = base_bytes / (1024 * 1024)
        base_tp = base_mb / base_time if base_time > 0 else 0.0
        print(f"Done: {base_tp:.2f} MB/s ({base_time:.3f}s)")

        results.append({
            "mode": "Baseline (Unpooled)",
            "workers": 1,
            "segments": num_segments,
            "total_mb": base_mb,
            "elapsed_s": base_time,
            "throughput_mb_s": base_tp,
            "speedup": 1.0,
            "efficiency": 100.0,
        })

        # 2. Pooled Persistent Workers: 1, 4, 8, 16, 32, 48
        worker_counts = [1, 4, 8, 16, 32, 48]
        for w in worker_counts:
            sys.stdout.write(f"Running Pooled Pipeline ({w:2d} Workers)... ")
            sys.stdout.flush()
            t_elapsed, n_bytes = _benchmark_pooled_streaming(server.base_url, num_segments, w)
            mb = n_bytes / (1024 * 1024)
            tp = mb / t_elapsed if t_elapsed > 0 else 0.0
            speedup = tp / base_tp if base_tp > 0 else 1.0
            eff = (speedup / w) * 100.0 if w > 0 else 0.0
            print(f"Done: {tp:.2f} MB/s ({t_elapsed:.3f}s) -> {speedup:.2f}x speedup")

            results.append({
                "mode": f"Pooled Stream ({w}w)",
                "workers": w,
                "segments": num_segments,
                "total_mb": mb,
                "elapsed_s": t_elapsed,
                "throughput_mb_s": tp,
                "speedup": speedup,
                "efficiency": eff,
            })

    finally:
        server.stop()

    # Formatted Results Table
    print("\n" + "=" * 88)
    print("BENCHMARK RESULTS TABLE")
    print("=" * 88)
    header = f"{'Configuration':<22} | {'Workers':<8} | {'Size (MB)':<10} | {'Time (s)':<9} | {'Throughput':<15} | {'Speedup':<9} | {'Efficiency':<10}"
    print(header)
    print("-" * 88)
    for r in results:
        cfg_name = r["mode"]
        w_str = str(r["workers"])
        size_str = f"{r['total_mb']:.2f} MB"
        time_str = f"{r['elapsed_s']:.3f} s"
        tp_str = f"{r['throughput_mb_s']:.2f} MB/s"
        sp_str = f"{r['speedup']:.2f}x"
        eff_str = f"{r['efficiency']:.1f}%"
        print(f"{cfg_name:<22} | {w_str:<8} | {size_str:<10} | {time_str:<9} | {tp_str:<15} | {sp_str:<9} | {eff_str:<10}")
    print("-" * 88)

    # Scaling Curve Visualization
    max_tp = max(r["throughput_mb_s"] for r in results)
    print("\n" + "=" * 88)
    print("WORKER CONCURRENCY SCALING CURVE (Throughput MB/s)")
    print("=" * 88)
    for r in results:
        bar = render_ascii_bar(r["throughput_mb_s"], max_tp, width=28)
        lbl = f"{r['mode']:<22}"
        tp_s = f"{r['throughput_mb_s']:>7.2f} MB/s"
        sp_s = f"({r['speedup']:>5.2f}x)"
        print(f"{lbl} : [{bar}] {tp_s} {sp_s}")
    print("=" * 88)

    summary = {
        "baseline_throughput_mb_s": base_tp,
        "peak_throughput_mb_s": max_tp,
        "max_speedup": max_tp / base_tp if base_tp > 0 else 1.0,
        "results": results,
    }
    print(f"\nSummary: Peak Throughput: {max_tp:.2f} MB/s | Maximum Acceleration: {summary['max_speedup']:.2f}x vs Baseline")
    print("=" * 88 + "\n")
    return summary


if __name__ == "__main__":
    run_throughput_benchmark()
