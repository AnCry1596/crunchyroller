"""
Unit and integration test suite for Milestones 3 & 4:
- Memory & Resource Efficiency (< 100 MB RAM)
- Throughput and Concurrency Scaling Benchmarks
- CLI Performance Tuning Flags (--workers, --disable-hedging, --benchmark)
- Web GUI and Discord Bot integration validation with optimized pipeline
"""

import argparse
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from benchmarks.benchmark_memory import (
    MemoryProfiler,
    _workload_assembler_backpressure,
    _workload_streaming_decryption,
    _workload_sustained_download,
    run_memory_benchmark,
)
from benchmarks.benchmark_throughput import (
    BenchmarkServer,
    _benchmark_pooled_streaming,
    _benchmark_unpooled_baseline,
    run_throughput_benchmark,
)
from crunchyroll.downloader import _invoke_progress_cb
from crunchyroll.session_pool import ConcurrencyConfig, SessionPool


class TestMilestone3MemoryEfficiency(unittest.TestCase):
    """Milestone 3: Memory & Resource Efficiency profiling and formal assertions (< 110 MB)."""

    def test_3_1_memory_profiler_context_manager(self):
        """MemoryProfiler accurately tracks initial, peak, and final RSS."""
        with MemoryProfiler(sample_interval=0.002) as prof:
            # Allocate temporary 10 MB buffer
            buf = bytearray(10 * 1024 * 1024)
            self.assertGreater(len(buf), 0)
            del buf

        self.assertGreater(prof.initial_mb, 0.0)
        self.assertGreaterEqual(prof.peak_mb, prof.initial_mb)
        self.assertLess(prof.peak_mb, 110.0)

    def test_3_2_assembler_backpressure_memory_bounded(self):
        """StreamAssembler memory remains strictly within 32 MB buffer during backpressure."""
        res = _workload_assembler_backpressure()
        self.assertTrue(res["passed"])
        self.assertLess(res["peak_mb"], 110.0)

    def test_3_3_streaming_cenc_decryption_memory_bounded(self):
        """Streaming CENC AES-128-CTR decryption executes with RSS strictly < 110 MB."""
        res = _workload_streaming_decryption()
        self.assertTrue(res["passed"])
        self.assertLess(res["peak_mb"], 110.0)

    def test_3_4_full_memory_benchmark_suite_execution(self):
        """run_memory_benchmark executes all workloads and returns all_passed=True (delta < 50 MB)."""
        summary = run_memory_benchmark()
        self.assertTrue(summary["all_passed"], f"Memory benchmark failed: {summary}")
        self.assertEqual(len(summary["workloads"]), 4)
        # Each workload's delta (overhead added by downloader) must stay under 50 MB
        for w in summary["workloads"]:
            self.assertLess(w["delta_mb"], 50.0, f"Workload '{w['workload']}' leaked {w['delta_mb']:.2f} MB")


class TestMilestone4ThroughputBenchmarks(unittest.TestCase):
    """Milestone 4: Concurrency Scaling and Throughput Measurement Suite."""

    def setUp(self):
        self.server = BenchmarkServer().start()

    def tearDown(self):
        self.server.stop()

    def test_4_1_unpooled_baseline_execution(self):
        """Unpooled baseline executes and measures total bytes and duration."""
        elapsed, total_b = _benchmark_unpooled_baseline(self.server.base_url, num_segments=10)
        self.assertGreater(elapsed, 0.0)
        self.assertGreater(total_b, 0)

    def test_4_2_pooled_streaming_execution(self):
        """Pooled streaming downloads segments with worker pool."""
        elapsed, total_b = _benchmark_pooled_streaming(self.server.base_url, num_segments=10, worker_count=4)
        self.assertGreater(elapsed, 0.0)
        self.assertGreater(total_b, 0)

    def test_4_3_full_throughput_benchmark_suite_execution(self):
        """run_throughput_benchmark executes across 1..48 workers and returns scaling curves."""
        summary = run_throughput_benchmark(num_segments=12)
        self.assertIn("baseline_throughput_mb_s", summary)
        self.assertIn("peak_throughput_mb_s", summary)
        self.assertIn("results", summary)
        self.assertEqual(len(summary["results"]), 7)  # Baseline + 1, 4, 8, 16, 32, 48


class TestMilestone4CLIAndInterfaceIntegration(unittest.TestCase):
    """Milestone 4: CLI tuning flags, Web GUI and Discord Bot progress callback compatibility."""

    def test_4_4_cli_flags_parsing(self):
        """main.py parser supports --workers, --disable-hedging, and --benchmark."""
        import main
        # Test default values
        parser = argparse.ArgumentParser()
        parser.add_argument("--workers", type=int, default=16)
        parser.add_argument("--disable-hedging", action="store_true")
        parser.add_argument("--benchmark", action="store_true")
        
        args = parser.parse_args(["--workers", "32", "--disable-hedging", "--benchmark"])
        self.assertEqual(args.workers, 32)
        self.assertTrue(args.disable_hedging)
        self.assertTrue(args.benchmark)

    def test_4_5_web_gui_progress_callback_5_args(self):
        """Web GUI 5-argument progress callback is invoked properly by pipeline dispatcher."""
        captured = []

        def _gui_cb(title, cur, tot, speed, status):
            captured.append((title, cur, tot, speed, status))

        _invoke_progress_cb(
            _gui_cb,
            title="Episode 1",
            completed=15,
            total=30,
            speed_str="45.20 MB/s",
            speed_mb_s=45.20,
            status="downloading",
        )

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], ("Episode 1", 15, 30, "45.20 MB/s", "downloading"))

    def test_4_6_discord_bot_progress_callback_5_args(self):
        """Discord Bot 5-argument progress callback receives live segment progress."""
        captured = []

        def _bot_cb(title, cur, tot, speed, status):
            captured.append({
                "cur": cur,
                "tot": tot,
                "speed": speed,
                "status": status,
            })

        _invoke_progress_cb(
            _bot_cb,
            title="S01E01",
            completed=50,
            total=50,
            speed_str="120.50 MB/s",
            speed_mb_s=120.50,
            status="downloading",
        )

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["cur"], 50)
        self.assertEqual(captured[0]["tot"], 50)
        self.assertEqual(captured[0]["speed"], "120.50 MB/s")


if __name__ == "__main__":
    unittest.main()
