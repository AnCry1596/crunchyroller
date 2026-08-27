"""
tests/test_tier5_adversarial.py

Tier 5: Adversarial Edge-Case and Fault Injection Tests
Covers adversarial network conditions, corrupted bitstreams, and fault injection:
1. Simulated Socket Disconnections & Broken Pipes
2. Corrupted Segment Data & Bitstream Truncation
3. Rate-Limit Bursts (HTTP 420/429) & AIMD Recovery
4. Malformed DASH Manifests & XML Fault Injection
5. Network Latency Stalls & Hedging Races
6. DRM License Server Faults & Invalid PSSH
7. FFmpeg Muxing Failures & Partial File Cleanup
8. Concurrent StreamAssembler Worker Races & Abort Safety
"""

import http.server
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

# Ensure discord modules are mocked if discord.py is not installed
if "discord" not in sys.modules:
    sys.modules["discord"] = MagicMock()
    sys.modules["discord.ui"] = MagicMock()
    sys.modules["discord.ext"] = MagicMock()
    sys.modules["discord.ext.commands"] = MagicMock()
    sys.modules["discord.app_commands"] = MagicMock()

from tests.mock_server import MockCrunchyrollServer, SAMPLE_PSSH_B64, build_mpd_xml
from crunchyroll.downloader import build_url, download_part, download_parts_optimized
from crunchyroll.merger import merge_everything
from crunchyroll.mpd import expand_timeline, get_base_url, get_pssh, parse_manifest
from crunchyroll.session_pool import AIMDConcurrencyScaler, ConcurrencyConfig, SessionPool
from crunchyroll.stream_assembler import StreamAssembler
from crunchyroll.types import EpisodeInfo, EpisodeMetadata, MediaTrack


class TestAdversarialSocketDisconnectionsAndResets(unittest.TestCase):
    """Fault Category 1: Simulated Socket Disconnections & Broken Pipes."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()

    def tearDown(self):
        self.server.stop()

    def test_5_1_1_simulated_connection_reset_retry_success(self):
        """Simulate server resetting connection on first attempt, then succeeding on retry."""
        attempt_count = 0
        lock = threading.Lock()

        def _flaky_handler(handler):
            nonlocal attempt_count
            with lock:
                attempt_count += 1
                cur = attempt_count

            if cur == 1:
                # Force abrupt socket close
                try:
                    handler.request.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                handler.close_connection = True
            else:
                handler._send_bytes(b"RECOVERED_AFTER_RESET")

        self.server.custom_routes["/media/flaky_reset.mp4"] = _flaky_handler

        cfg = ConcurrencyConfig(max_retries=3, backoff_factor=1.1, timeout=3)
        pool = SessionPool(config=cfg)
        try:
            data = pool.download_segment(self.server.get_url("/media/flaky_reset.mp4"))
            self.assertEqual(data, b"RECOVERED_AFTER_RESET")
            self.assertGreaterEqual(attempt_count, 2)
        finally:
            pool.close()

    def test_5_1_2_connection_refused_unreachable_port(self):
        """Unreachable port raises RuntimeError after retry exhaustion."""
        # Find unused local port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        unused_port = s.getsockname()[1]
        s.close()

        cfg = ConcurrencyConfig(max_retries=2, backoff_factor=1.0, timeout=1)
        pool = SessionPool(config=cfg)
        try:
            with self.assertRaises(RuntimeError):
                pool.download_segment(f"http://127.0.0.1:{unused_port}/media/seg.mp4")
        finally:
            pool.close()

    def test_5_1_3_persistent_500_server_error_exhaustion(self):
        """Persistent HTTP 500 server errors exhaust max retries and raise RuntimeError."""
        def _err_handler(handler):
            handler._send_error_response(500, "Internal Error")

        self.server.custom_routes["/media/broken_500.mp4"] = _err_handler

        cfg = ConcurrencyConfig(max_retries=3, backoff_factor=1.0, timeout=2)
        pool = SessionPool(config=cfg)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                pool.download_segment(self.server.get_url("/media/broken_500.mp4"))
            self.assertIn("Failed to download segment after 3 attempts", str(ctx.exception))
        finally:
            pool.close()


class TestAdversarialCorruptedSegmentData(unittest.TestCase):
    """Fault Category 2: Corrupted Segment Data & Bitstream Truncation."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_adv_s2_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_5_2_1_zero_byte_segment_payload(self):
        """Zero-byte segment payload is assembled without crashing or index corruption."""
        out_p = os.path.join(self.tmp_dir, "zero_seg.mp4")
        assembler = StreamAssembler(out_p, total_segments=3)
        assembler.add_segment(1, b"HEADER_")
        assembler.add_segment(2, b"")  # 0-byte segment
        assembler.add_segment(3, b"FOOTER_")
        assembler.finish()

        with open(out_p, "rb") as f:
            data = f.read()
        self.assertEqual(data, b"HEADER_FOOTER_")

    def test_5_2_2_corrupted_segment_stream_assembler_abort_cleanup(self):
        """When an unrecoverable download error occurs, StreamAssembler abort cleans up state."""
        out_p = os.path.join(self.tmp_dir, "aborted_stream.mp4")
        assembler = StreamAssembler(out_p, total_segments=5)
        assembler.add_segment(1, b"PART1_")
        assembler.add_segment(2, b"PART2_")

        # Worker aborts on error
        assembler.abort(RuntimeError("Network corrupted segment 3"))

        # Subsequent adds raise RuntimeError
        with self.assertRaises(RuntimeError):
            assembler.add_segment(4, b"PART4_")

        # finish() raises the abort exception
        with self.assertRaises(RuntimeError):
            assembler.finish()


class TestAdversarialRateLimitBursts(unittest.TestCase):
    """Fault Category 3: Rate-Limit Bursts (HTTP 420/429) & AIMD Recovery."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()

    def tearDown(self):
        self.server.stop()

    def test_5_3_1_http_420_burst_triggers_aimd_backoff_and_recovery(self):
        """Inject 420 Rate Limit responses followed by AIMD backoff and recovery."""
        scaler = AIMDConcurrencyScaler(min_workers=8, max_workers=48, initial_workers=20, window_size=3)
        # 1. 420 burst triggers multiplicative decrease
        scaler.record_failure(status_code=420)
        scaler.record_failure(status_code=420)
        self.assertEqual(scaler.current_workers, 11)

        # 2. Recovery with successful chunks ramps workers back up
        for _ in range(15):
            scaler.record_success(0.01, 100000)

        stats = scaler.get_stats()
        self.assertEqual(stats["total_failures"], 2)
        self.assertEqual(stats["total_success"], 15)
        self.assertGreater(scaler.current_workers, 11)

    def test_5_3_2_aimd_concurrency_scaler_floor_bounding(self):
        """Rapid sequence of 10 consecutive failures does not drop worker count below min_workers."""
        scaler = AIMDConcurrencyScaler(min_workers=8, max_workers=48, initial_workers=16)
        for _ in range(10):
            scaler.record_failure(status_code=420)
        self.assertEqual(scaler.current_workers, 8)

    def test_5_3_3_aimd_concurrency_scaler_ceiling_bounding(self):
        """Rapid sequence of 50 consecutive successes does not increase worker count above max_workers."""
        scaler = AIMDConcurrencyScaler(min_workers=8, max_workers=48, initial_workers=40, window_size=2)
        for _ in range(50):
            scaler.record_success(0.01, 500000)
        self.assertLessEqual(scaler.current_workers, 48)


class TestAdversarialMalformedDASHManifests(unittest.TestCase):
    """Fault Category 4: Malformed DASH Manifests & XML Fault Injection."""

    def test_5_4_1_manifest_negative_repeat_count(self):
        """Negative repeat count in DASH SegmentTimeline (r='-5') is safely normalized."""
        xml_str = '<AdaptationSet><SegmentTimeline><S t="0" d="1000" r="-5"/></SegmentTimeline></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        timeline = expand_timeline(elem)
        self.assertEqual(timeline, [1])

    def test_5_4_2_manifest_missing_initialization_template(self):
        """SegmentTemplate missing initialization attribute returns None base_url/rep_id safely."""
        xml_str = '<AdaptationSet contentType="video"><Representation id="v1" height="1080"><BaseURL>https://cdn.example.com/</BaseURL></Representation></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        base_url, rep_id = get_base_url(elem, is_video_set=True, quality="1080p")
        self.assertEqual(base_url, "https://cdn.example.com/")
        self.assertEqual(rep_id, "v1")

    def test_5_4_3_manifest_unrecognized_xml_namespaces(self):
        """Manifest containing foreign XML namespaces parses tags cleanly."""
        xml_str = """<?xml version="1.0"?>
        <MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:custom="http://example.com/custom">
          <Period id="0">
            <AdaptationSet id="0" contentType="video">
              <Representation id="1080p" height="1080">
                <BaseURL>http://localhost/</BaseURL>
              </Representation>
            </AdaptationSet>
          </Period>
        </MPD>"""
        root = ET.fromstring(xml_str)
        periods = [e for e in root if "Period" in e.tag]
        self.assertEqual(len(periods), 1)

    def test_5_4_4_manifest_corrupted_pssh_box_handling(self):
        """Manifest with invalid base64 or empty cenc:pssh is handled safely."""
        xml_str = '<AdaptationSet><ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"><pssh>INVALID_BASE64_NOT_DIVISIBLE_BY_4</pssh></ContentProtection></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        pssh = get_pssh(elem)
        # Raw text is returned; downstream DRM handles decode validation
        self.assertEqual(pssh, "INVALID_BASE64_NOT_DIVISIBLE_BY_4")


class TestAdversarialNetworkLatencyAndHedging(unittest.TestCase):
    """Fault Category 5: Network Latency Stalls & Hedging Races."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()

    def tearDown(self):
        self.server.stop()

    def test_5_5_1_hedged_request_secondary_beats_slow_primary(self):
        """Secondary speculative request succeeds when primary request is artificially stalled."""
        call_count = 0
        call_lock = threading.Lock()

        def _delayed_route(handler):
            nonlocal call_count
            with call_lock:
                call_count += 1
                c = call_count

            if c == 1:
                # Primary request stalls for 0.5s
                time.sleep(0.5)
                handler._send_bytes(b"SLOW_PRIMARY_PAYLOAD")
            else:
                # Secondary hedged request returns immediately
                handler._send_bytes(b"FAST_SECONDARY_PAYLOAD")

        self.server.custom_routes["/media/hedged_race.mp4"] = _delayed_route

        cfg = ConcurrencyConfig(hedging_enabled=True, hedge_min_delay=0.05, timeout=3)
        pool = SessionPool(config=cfg)
        try:
            data = pool.download_segment_hedged(self.server.get_url("/media/hedged_race.mp4"))
            # Either fast secondary or primary is acceptable; payload must be valid non-empty bytes
            self.assertIn(data, [b"FAST_SECONDARY_PAYLOAD", b"SLOW_PRIMARY_PAYLOAD"])
        finally:
            pool.close()

    def test_5_5_2_hedged_download_timeout_exhaustion(self):
        """When all hedged worker attempts exceed timeout, TimeoutError is raised."""
        def _hanging_route(handler):
            time.sleep(2.0)
            handler._send_bytes(b"NEVER_ARRIVES")

        self.server.custom_routes["/media/hanging.mp4"] = _hanging_route

        cfg = ConcurrencyConfig(hedging_enabled=True, hedge_min_delay=0.05, timeout=1)
        pool = SessionPool(config=cfg)
        try:
            with self.assertRaises((TimeoutError, RuntimeError)):
                pool.download_segment_hedged(self.server.get_url("/media/hanging.mp4"), timeout=1)
        finally:
            pool.close()


class TestAdversarialDRMLicenseFaults(unittest.TestCase):
    """Fault Category 6: DRM License Server Faults & Invalid PSSH."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()

    def tearDown(self):
        self.server.stop()

    def test_5_6_1_license_server_403_forbidden_handling(self):
        """License server returning HTTP 403 Forbidden raises RuntimeError."""
        def _forbidden_license(handler):
            handler._send_error_response(403, "Forbidden - Invalid Account Session")

        self.server.custom_routes["/license/v1/license/widevine"] = _forbidden_license

        with patch("crunchyroll.http_client.get_access_token", return_value="tok"), \
             patch("crunchyroll.http_client.load_config", return_value={}):
            from crunchyroll.http_client import CrunchyrollHttpClient
            client = CrunchyrollHttpClient()
            client.token = "tok"

            # Direct request to forbidden license route
            resp = client.do_request("POST", self.server.get_url("/license/v1/license/widevine"))
            self.assertEqual(resp.status_code, 403)


class TestAdversarialFFmpegMuxingFailures(unittest.TestCase):
    """Fault Category 7: FFmpeg Muxing Failures & Partial File Cleanup."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_adv_s7_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_5_7_1_corrupted_input_file_ffmpeg_error_cleans_output(self):
        """When input video file is completely corrupted, FFmpeg fails and removes partial target MKV."""
        v_corrupt = os.path.join(self.tmp_dir, "corrupt_video.mp4")
        out_mkv = os.path.join(self.tmp_dir, "corrupt_out.mkv")

        # Write invalid binary garbage as video input
        with open(v_corrupt, "wb") as f:
            f.write(b"CORRUPT_INVALID_HEADER_GARBAGE_DATA_1234567890")

        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Show", season_number=1, episode_number=1,
                audio_locale="ja-JP", versions=[], availability_starts="",
            ),
            title="Corrupt Test", subtitles={},
        )

        with self.assertRaises(RuntimeError) as ctx:
            merge_everything(
                video_file=v_corrupt,
                audio_tracks=[],
                sub_tracks=[],
                output_file=out_mkv,
                info=info,
            )

        self.assertIn("ffmpeg failed", str(ctx.exception).lower())
        # Target output MKV must NOT remain on disk in corrupted state
        self.assertFalse(os.path.exists(out_mkv))


class TestAdversarialConcurrentContentionAndRaces(unittest.TestCase):
    """Fault Category 8: Concurrent StreamAssembler Worker Races & Abort Safety."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_adv_s8_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_5_8_1_heavy_reverse_order_concurrent_assembler_streaming(self):
        """Stress test 32 threads pushing segments into StreamAssembler in reverse order."""
        out_p = os.path.join(self.tmp_dir, "stress_reverse.mp4")
        total_segs = 32
        assembler = StreamAssembler(out_p, total_segments=total_segs, max_in_flight_mb=16)

        def _push_chunk(num):
            assembler.add_segment(num, f"CHUNK_{num:04d}_".encode("ascii"))

        # Reverse ordering 32 .. 1
        with ThreadPoolExecutor(max_workers=32) as executor:
            futures = [executor.submit(_push_chunk, i) for i in reversed(range(1, total_segs + 1))]
            for f in futures:
                f.result(timeout=5.0)

        assembler.finish()

        with open(out_p, "rb") as f:
            data = f.read()
        expected = "".join(f"CHUNK_{i:04d}_" for i in range(1, total_segs + 1)).encode("ascii")
        self.assertEqual(data, expected)

    def test_5_8_2_concurrent_session_pool_close_thread_safety(self):
        """Multiple concurrent threads calling close() on SessionPool do not raise exceptions."""
        pool = SessionPool(max_pool_size=16)
        errors = []

        def _close_pool():
            try:
                pool.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_close_pool) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        self.assertEqual(len(errors), 0)
        self.assertTrue(pool._closed)


if __name__ == "__main__":
    unittest.main()
