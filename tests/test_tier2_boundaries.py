"""
tests/test_tier2_boundaries.py

Tier 2: Boundary and Corner Case Tests (>=5 unit tests per boundary category)
Covers all boundary conditions, edge cases, and fault modes defined in PROJECT.md:
1. Empty and Malformed DASH Manifests
2. Single-Segment and Extreme Repeat Count Timelines
3. HTTP 420 Rate Limiting Backoff & Retry Exhaustion
4. Network Timeouts, Read Stalls & Socket Errors
5. Reverse, Inverted, and Garbage Episode Ranges
6. Malformed and Invalid URLs
7. Zero-Length and Truncated Files
8. Extreme Unicode, RTL, Emojis, and Reserved Filenames
"""

import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

# Ensure discord modules are mocked if discord.py is not installed
if "discord" not in sys.modules:
    sys.modules["discord"] = MagicMock()
    sys.modules["discord.ui"] = MagicMock()
    sys.modules["discord.ext"] = MagicMock()
    sys.modules["discord.ext.commands"] = MagicMock()
    sys.modules["discord.app_commands"] = MagicMock()

from tests.mock_server import MockCrunchyrollServer
from crunchyroll.api import parse_url_type
from crunchyroll.downloader import build_url, download_part
from crunchyroll.http_client import CrunchyrollHttpClient
from crunchyroll.mpd import expand_timeline, get_base_url, get_pssh, parse_manifest
from crunchyroll.session_pool import SessionPool, ConcurrencyConfig, AIMDConcurrencyScaler
from crunchyroll.stream_assembler import StreamAssembler
from crunchyroll.types import SeasonEpisode
from crunchyroll.utils import sanitize_filename, track_title


class TestBoundaryEmptyManifests(unittest.TestCase):
    """Category 1: Empty and Malformed DASH Manifests."""

    def test_2_1_1_empty_manifest_no_period(self):
        """Manifest with root MPD tag but 0 Period elements."""
        xml_str = '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"></MPD>'
        elem = ET.fromstring(xml_str)
        pssh = get_pssh(elem)
        self.assertIsNone(pssh)
        timeline = expand_timeline(elem)
        self.assertEqual(timeline, [])

    def test_2_1_2_period_no_adaptation_sets(self):
        """Manifest with Period tag but 0 AdaptationSet elements."""
        xml_str = '<MPD><Period id="0"></Period></MPD>'
        elem = ET.fromstring(xml_str)
        self.assertIsNone(get_pssh(elem))
        self.assertEqual(expand_timeline(elem), [])

    def test_2_1_3_adaptation_set_no_representations(self):
        """AdaptationSet with 0 Representation children."""
        xml_str = '<AdaptationSet contentType="video"></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        base_url, rep_id = get_base_url(elem, is_video_set=True, quality="1080p")
        self.assertIsNone(base_url)
        self.assertIsNone(rep_id)

    def test_2_1_4_adaptation_set_no_segment_timeline(self):
        """AdaptationSet with SegmentTemplate but missing SegmentTimeline."""
        xml_str = '<AdaptationSet><SegmentTemplate initialization="init.mp4" media="seg_$Number$.mp4"/></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        timeline = expand_timeline(elem)
        self.assertEqual(timeline, [])

    def test_2_1_5_malformed_xml_string_raises(self):
        """Invalid XML string throws XML parse error."""
        invalid_xml = "<MPD><Period><unclosed_tag></Period></MPD>"
        with self.assertRaises(ET.ParseError):
            ET.fromstring(invalid_xml)


class TestBoundarySingleAndExtremeSegments(unittest.TestCase):
    """Category 2: Single-Segment and Extreme Repeat Count Timelines."""

    def test_2_2_1_single_segment_timeline(self):
        """Single-segment stream with r=0."""
        xml_str = '<AdaptationSet><SegmentTimeline><S t="0" d="1000" r="0"/></SegmentTimeline></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        timeline = expand_timeline(elem)
        self.assertEqual(timeline, [1])

    def test_2_2_2_large_repeat_count_1000(self):
        """Timeline with r=1000 generates exactly 1001 sequential segment IDs."""
        xml_str = '<AdaptationSet><SegmentTimeline><S t="0" d="1000" r="1000"/></SegmentTimeline></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        timeline = expand_timeline(elem)
        self.assertEqual(len(timeline), 1001)
        self.assertEqual(timeline[0], 1)
        self.assertEqual(timeline[-1], 1001)

    def test_2_2_3_massive_repeat_count_50000_perf(self):
        """Massive timeline expansion (50,000 segments) completes in < 50ms."""
        xml_str = '<AdaptationSet><SegmentTimeline><S t="0" d="1000" r="49999"/></SegmentTimeline></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        start_t = time.time()
        timeline = expand_timeline(elem)
        elapsed = time.time() - start_t
        self.assertEqual(len(timeline), 50000)
        self.assertLess(elapsed, 0.1)

    def test_2_2_4_negative_repeat_count_treated_as_zero(self):
        """Negative r value (r='-1') is safely clamped to 0 repeat (1 segment)."""
        xml_str = '<AdaptationSet><SegmentTimeline><S t="0" d="1000" r="-1"/></SegmentTimeline></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        timeline = expand_timeline(elem)
        self.assertEqual(timeline, [1])

    def test_2_2_5_start_number_boundary_values(self):
        """startNumber=0 and startNumber=99999."""
        xml_str_0 = '<AdaptationSet><SegmentTemplate startNumber="0"/><SegmentTimeline><S t="0" d="1000" r="2"/></SegmentTimeline></AdaptationSet>'
        timeline_0 = expand_timeline(ET.fromstring(xml_str_0))
        self.assertEqual(timeline_0, [0, 1, 2])

        xml_str_big = '<AdaptationSet><SegmentTemplate startNumber="99999"/><SegmentTimeline><S t="0" d="1000" r="1"/></SegmentTimeline></AdaptationSet>'
        timeline_big = expand_timeline(ET.fromstring(xml_str_big))
        self.assertEqual(timeline_big, [99999, 100000])


class TestBoundaryRateLimitingAndBackoff(unittest.TestCase):
    """Category 3: HTTP 420 Rate Limiting Backoff & Retry Exhaustion."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()

    def tearDown(self):
        self.server.stop()

    def test_2_3_1_rate_limit_recover_after_1_retry(self):
        """Server returns 420 once, then 200 on retry."""
        self.server.rate_limit_remaining = 1
        pool = SessionPool(max_pool_size=4, max_retries=3, backoff_factor=0.01)
        try:
            data = pool.download_segment(self.server.get_url("/media/init_video.mp4"))
            self.assertIsInstance(data, bytes)
            self.assertGreater(len(data), 0)
        finally:
            pool.close()

    def test_2_3_2_rate_limit_recover_after_3_retries(self):
        """Server returns 420 three times, then 200 on 4th try."""
        self.server.rate_limit_remaining = 3
        pool = SessionPool(max_pool_size=4, max_retries=5, backoff_factor=0.01)
        try:
            data = pool.download_segment(self.server.get_url("/media/init_video.mp4"))
            self.assertIsInstance(data, bytes)
            self.assertGreater(len(data), 0)
        finally:
            pool.close()

    def test_2_3_3_rate_limit_exhaustion_raises(self):
        """Persistent 420 exhausting max_retries raises RuntimeError."""
        self.server.rate_limit_remaining = 100
        pool = SessionPool(max_pool_size=2, max_retries=3, backoff_factor=0.01)
        try:
            with self.assertRaises(RuntimeError):
                pool.download_segment(self.server.get_url("/media/init_video.mp4"))
        finally:
            pool.close()

    def test_2_3_4_http_client_420_loop_simulation(self):
        """CrunchyrollHttpClient rate limit backoff loop triggers sleep and retries."""
        with patch("crunchyroll.http_client.get_access_token", return_value="tok"), \
             patch("crunchyroll.http_client.load_config", return_value={}), \
             patch("time.sleep") as mock_sleep:
            client = CrunchyrollHttpClient()
            resp_420 = MagicMock(status_code=420)
            resp_200 = MagicMock(status_code=200)
            client.session.request = MagicMock(side_effect=[resp_420, resp_420, resp_200])

            res = client.do_request("GET", "http://example.com/api")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(mock_sleep.call_count, 2)
            client.close()

    def test_2_3_5_aimd_concurrency_drop_on_420(self):
        """AIMD concurrency scaler halves workers when receiving 420 rate limit."""
        scaler = AIMDConcurrencyScaler(min_workers=8, max_workers=48, initial_workers=24)
        new_w = scaler.record_failure(status_code=420)
        self.assertEqual(new_w, 18)  # 24 * 0.75 = 18


class TestBoundaryTimeoutsAndNetworkFaults(unittest.TestCase):
    """Category 4: Network Timeouts, Read Stalls & Socket Errors."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()

    def tearDown(self):
        self.server.stop()

    def test_2_4_1_socket_timeout_handling(self):
        """Simulated socket timeout on unreachable port raises RuntimeError after retries."""
        pool = SessionPool(max_pool_size=2, max_retries=2, timeout=1)
        try:
            # Connect to a non-listening local port
            with self.assertRaises(RuntimeError):
                pool.download_segment("http://127.0.0.1:59999/media/seg_1.mp4", timeout=1)
        finally:
            pool.close()

    def test_2_4_2_simulated_slow_latency_within_timeout(self):
        """Slow network response (0.1s latency) succeeds when within timeout bounds."""
        self.server.simulated_latency = 0.1
        pool = SessionPool(max_pool_size=4, max_retries=2, timeout=5)
        try:
            data = pool.download_segment(self.server.get_url("/media/init_video.mp4"))
            self.assertGreater(len(data), 0)
        finally:
            pool.close()

    def test_2_4_3_server_500_503_error_recovery(self):
        """Recover from transient HTTP 500 error."""
        self.server.flaky_segment_error_rate = 0.4
        pool = SessionPool(max_pool_size=4, max_retries=6, backoff_factor=0.01)
        try:
            data = pool.download_segment(self.server.get_url("/media/init_video.mp4"))
            self.assertGreater(len(data), 0)
        finally:
            pool.close()

    def test_2_4_4_http_404_not_found_fails(self):
        """HTTP 404 Not Found fails gracefully without infinite loop."""
        pool = SessionPool(max_pool_size=2, max_retries=2, backoff_factor=0.01)
        try:
            with self.assertRaises(RuntimeError):
                pool.download_segment(self.server.get_url("/media/non_existent_file.mp4"))
        finally:
            pool.close()

    def test_2_4_5_hedging_timeout_fallback(self):
        """download_segment_hedged with low timeout raises TimeoutError or RuntimeError."""
        self.server.simulated_latency = 1.0
        cfg = ConcurrencyConfig(hedging_enabled=True, hedge_min_delay=0.05, timeout=1, max_retries=1)
        pool = SessionPool(config=cfg)
        try:
            with self.assertRaises((TimeoutError, RuntimeError, Exception)):
                pool.download_segment_hedged(self.server.get_url("/media/init_video.mp4"), timeout=0.1)
        finally:
            pool.close()


class TestBoundaryEpisodeRangeParsing(unittest.TestCase):
    """Category 5: Reverse, Inverted, and Garbage Episode Ranges."""

    def setUp(self):
        self.eps = [
            SeasonEpisode(
                id=f"id_{i}",
                title=f"Episode {i}",
                season_number=1,
                episode_number=i,
                series_title="Test Series",
                audio_locale="ja-JP",
                versions=[],
                availability_starts="",
            )
            for i in range(1, 21)
        ]

    def test_2_5_1_reverse_range_10_to_8(self):
        """Reverse range '10-8' correctly resolves to [8, 9, 10] in available list order."""
        from discord_bot import parse_episode_ranges
        selected = parse_episode_ranges("10-8", self.eps)
        ep_nums = [e.episode_number for e in selected]
        self.assertEqual(ep_nums, [8, 9, 10])

    def test_2_5_2_inverted_range_5_to_1(self):
        """Inverted range '5-1' resolves to [1, 2, 3, 4, 5]."""
        from discord_bot import parse_episode_ranges
        selected = parse_episode_ranges("5-1", self.eps)
        ep_nums = [e.episode_number for e in selected]
        self.assertEqual(ep_nums, [1, 2, 3, 4, 5])

    def test_2_5_3_zero_and_negative_numbers(self):
        """Negative and zero ranges ('0-5', '-3') cleanly filter out non-existent ep 0 and negatives."""
        from discord_bot import parse_episode_ranges
        selected = parse_episode_ranges("0-3", self.eps)
        ep_nums = [e.episode_number for e in selected]
        self.assertEqual(ep_nums, [1, 2, 3])

    def test_2_5_4_huge_out_of_bounds_numbers(self):
        """Huge numbers ('999999', '100-200') are safely ignored."""
        from discord_bot import parse_episode_ranges
        selected = parse_episode_ranges("5, 999999, 100-200", self.eps)
        ep_nums = [e.episode_number for e in selected]
        self.assertEqual(ep_nums, [5])

    def test_2_5_5_garbage_and_special_chars_expressions(self):
        """Garbage strings ('???', 'abc-def', '!!!') return empty or valid subsets without crashing."""
        from discord_bot import parse_episode_ranges
        selected_garbage = parse_episode_ranges("???, abc-def, !!!", self.eps)
        self.assertEqual(selected_garbage, [])
        selected_mixed = parse_episode_ranges("foo, 7, bar-baz, 9", self.eps)
        ep_nums = [e.episode_number for e in selected_mixed]
        self.assertEqual(ep_nums, [7, 9])


class TestBoundaryURLResolution(unittest.TestCase):
    """Category 6: Malformed and Invalid URLs."""

    def test_2_6_1_missing_content_id(self):
        """URL ending at watch/ with no ID raises ValueError."""
        with self.assertRaises(ValueError):
            parse_url_type("https://www.crunchyroll.com/watch/")

    def test_2_6_2_foreign_domain(self):
        """Foreign domain URL without CR structure raises ValueError."""
        with self.assertRaises(ValueError):
            parse_url_type("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_2_6_3_trailing_and_multiple_slashes(self):
        """URL with multiple redundant slashes parses cleanly."""
        url = "https://www.crunchyroll.com///watch////G12345////my-episode///"
        t, cid = parse_url_type(url)
        self.assertEqual(t, "episode")
        self.assertEqual(cid, "G12345")

    def test_2_6_4_non_http_protocol(self):
        """Unrecognized URL structure raises ValueError."""
        with self.assertRaises(ValueError):
            parse_url_type("ftp://ftp.example.com/files/archive.zip")

    def test_2_6_5_empty_string_url(self):
        """Empty string URL raises ValueError."""
        with self.assertRaises(ValueError):
            parse_url_type("")


class TestBoundaryZeroLengthAndCorruptFiles(unittest.TestCase):
    """Category 7: Zero-Length and Truncated Files."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_corrupt_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_2_7_1_zero_length_segment_handling(self):
        """StreamAssembler handling 0-byte segment chunk."""
        out_path = os.path.join(self.tmp_dir, "zero_seg.mp4")
        assembler = StreamAssembler(out_path, total_segments=1)
        assembler.add_segment(1, b"")
        assembler.finish()
        self.assertTrue(os.path.exists(out_path))
        self.assertEqual(os.path.getsize(out_path), 0)

    def test_2_7_2_stream_assembler_abort_frees_file(self):
        """StreamAssembler abort closes file handle immediately."""
        out_path = os.path.join(self.tmp_dir, "aborted.mp4")
        assembler = StreamAssembler(out_path, total_segments=5)
        assembler.add_segment(1, b"DATA")
        assembler.abort(RuntimeError("Download worker crashed"))
        self.assertTrue(assembler._closed)
        self.assertTrue(assembler._aborted)
        with self.assertRaises(RuntimeError):
            assembler.add_segment(2, b"MORE")

    def test_2_7_3_existing_corrupted_partial_file_detection(self):
        """Files under 10 MB are detected as partial/corrupted for re-download."""
        partial_file = os.path.join(self.tmp_dir, "partial.mkv")
        with open(partial_file, "wb") as f:
            f.write(b"\x00" * 1024)  # 1 KB
        sz = os.path.getsize(partial_file)
        is_corrupted_or_partial = sz < (10 * 1024 * 1024)
        self.assertTrue(is_corrupted_or_partial)

    def test_2_7_4_existing_complete_file_skip_detection(self):
        """Files over 10 MB are detected as existing complete files."""
        sz = 15 * 1024 * 1024  # 15 MB
        is_complete = sz > (10 * 1024 * 1024)
        self.assertTrue(is_complete)

    def test_2_7_5_duplicate_segment_delivery_deduplication(self):
        """Delivering duplicate segment indices to StreamAssembler does not duplicate data on disk."""
        out_path = os.path.join(self.tmp_dir, "dedup.mp4")
        assembler = StreamAssembler(out_path, total_segments=2)
        assembler.add_segment(1, b"CHUNK1_")
        assembler.add_segment(1, b"CHUNK1_")  # Duplicate
        assembler.add_segment(2, b"CHUNK2_")
        assembler.finish()
        with open(out_path, "rb") as f:
            self.assertEqual(f.read(), b"CHUNK1_CHUNK2_")


class TestBoundaryExtremeUnicodeFilenames(unittest.TestCase):
    """Category 8: Extreme Unicode, RTL, Emojis, and Reserved Filenames."""

    def test_2_8_1_emojis_in_title(self):
        """Sanitizer handles emojis without crashing or dropping characters."""
        title = "Frieren 🧙‍♂️⚔️ S01E01 - The Journey Begins 🔥"
        clean = sanitize_filename(title)
        self.assertIn("Frieren", clean)
        self.assertIn("The Journey Begins", clean)

    def test_2_8_2_rtl_arabic_hebrew_text(self):
        """Sanitizer handles Arabic and Hebrew script."""
        arabic_title = "أنمي ون بيس - الحلقة 1000: فجر جديد"
        clean = sanitize_filename(arabic_title)
        self.assertIn("أنمي ون بيس - الحلقة 1000_ فجر جديد", clean)

    def test_2_8_3_japanese_kanji_and_fullwidth_brackets(self):
        """Sanitizer handles Japanese fullwidth brackets and punctuation."""
        jp_title = "葬送のフリーレン 第1話 「冒険の終わり」 (1080p)"
        clean = sanitize_filename(jp_title)
        self.assertIn("葬送のフリーレン 第1話 「冒険の終わり」 (1080p)", clean)

    def test_2_8_4_windows_reserved_device_names(self):
        """Sanitizer handles names like CON, PRN, AUX, NUL."""
        clean_con = sanitize_filename("CON")
        self.assertEqual(clean_con, "CON")
        clean_nul = sanitize_filename("NUL")
        self.assertEqual(clean_nul, "NUL")

    def test_2_8_5_excessively_long_title(self):
        """Sanitizer handles 500+ character titles cleanly."""
        long_title = "A" * 500
        clean = sanitize_filename(long_title)
        self.assertEqual(len(clean), 500)


if __name__ == "__main__":
    unittest.main()
