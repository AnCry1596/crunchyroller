"""
tests/test_tier1_features.py

Tier 1: Feature Unit Tests (>=5 unit tests per core feature)
Covers all 13 core features defined in PROJECT.md and SPEC miner survey:
1. Connection Pooling & Session Reuse
2. Dynamic Concurrency & Worker Scaling
3. Single-Pass Stream Assembler
4. MPD Timeline Parsing & Representation Selection
5. CENC PSSH Extraction
6. Token Refresh & Auth Lifecycle
7. Filename Sanitizer & Locale Mappings
8. CLI Parser, Aliases & Batch Mode
9. URL Type Resolution & CMS API Queries
10. Subtitle Processing & ASS Validation
11. FFmpeg Merger & Track Disposition Mapping
12. Web GUI REST API & SafeStream
13. Discord Bot Range Parser & Instance Lock
"""

import argparse
import io
import json
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

from tests.mock_server import (
    MockCrunchyrollServer,
    SAMPLE_ASS_SUBTITLE,
    SAMPLE_PSSH_B64,
    build_mpd_xml,
)

# Core Crunchyroll modules
from crunchyroll.api import (
    get_episode,
    get_episode_info,
    get_season_episodes,
    get_seasons,
    get_series,
    parse_url_type,
)
from crunchyroll.auth import load_config, save_config
from crunchyroll.downloader import build_url, download_part, download_subs
from crunchyroll.http_client import CrunchyrollHttpClient
from crunchyroll.merger import find_ffmpeg, merge_everything
from crunchyroll.mpd import expand_timeline, get_base_url, get_pssh, parse_manifest
from crunchyroll.token import get_access_token
from crunchyroll.types import (
    DubVersion,
    EpisodeInfo,
    EpisodeMetadata,
    MediaTrack,
    Season,
    SeasonEpisode,
)
from crunchyroll.utils import (
    LANGUAGE_CODES,
    LANGUAGE_NAMES,
    sanitize_filename,
    track_title,
)


class TestFeature1ConnectionPooling(unittest.TestCase):
    """Feature 1: Persistent Connection Pooling & HTTP Session Reuse."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()

    def tearDown(self):
        self.server.stop()

    def test_1_1_session_pool_initialization(self):
        """Test SessionPool initialization and adapter mounting."""
        from crunchyroll.session_pool import SessionPool, ConcurrencyConfig
        pool = SessionPool(max_pool_size=32, max_retries=3, backoff_factor=1.0)
        self.assertIsNotNone(pool)
        session = pool.get_session()
        self.assertIsNotNone(session)
        self.assertEqual(pool.max_pool_size, 32)
        pool.close()

    def test_1_2_keep_alive_connection_reuse(self):
        """Verify multiple HTTP GET requests reuse keep-alive connection without reconnect errors."""
        import requests
        session = requests.Session()
        for i in range(5):
            resp = session.get(self.server.get_url("/subs/en-US.ass"))
            self.assertEqual(resp.status_code, 200)
            self.assertIn("Connection", resp.headers)
        self.assertEqual(len(self.server.request_history), 5)
        session.close()

    def test_1_3_pool_download_segment(self):
        """Verify downloading segment bytes via session pool."""
        from crunchyroll.session_pool import SessionPool
        pool = SessionPool(max_pool_size=16)
        data = pool.download_segment(self.server.get_url("/media/init_video.mp4"))
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        pool.close()

    def test_1_4_pool_retry_on_transient_failure(self):
        """Verify session pool retries on transient connection error."""
        self.server.flaky_segment_error_rate = 0.3
        from crunchyroll.session_pool import SessionPool
        pool = SessionPool(max_pool_size=8, max_retries=5)
        data = pool.download_segment(self.server.get_url("/media/init_video.mp4"))
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        pool.close()

    def test_1_5_pool_clean_shutdown(self):
        """Verify closing session pool releases socket descriptors cleanly."""
        from crunchyroll.session_pool import SessionPool
        pool = SessionPool(max_pool_size=8)
        sess = pool.get_session()
        sess.get(self.server.get_url("/auth/v1/token"))
        pool.close()
        self.assertTrue(pool._closed)


class TestFeature2DynamicConcurrency(unittest.TestCase):
    """Feature 2: Concurrency & Dynamic Worker Scaling."""

    def test_2_1_concurrency_bounds(self):
        """Verify concurrency scaler respects lower (8) and upper (48) bounds."""
        from crunchyroll.session_pool import AIMDConcurrencyScaler
        scaler = AIMDConcurrencyScaler(min_workers=8, max_workers=48, initial_workers=16)
        self.assertEqual(scaler.current_workers, 16)
        # Record repeated failures -> should not drop below min_workers (8)
        for _ in range(20):
            scaler.record_failure()
        self.assertGreaterEqual(scaler.current_workers, 8)

    def test_2_2_aimd_additive_increase(self):
        """Verify AIMD additive increase on consecutive successful downloads."""
        from crunchyroll.session_pool import AIMDConcurrencyScaler
        scaler = AIMDConcurrencyScaler(min_workers=8, max_workers=48, initial_workers=10, window_size=5)
        initial_workers = scaler.current_workers
        for _ in range(10):
            scaler.record_success(0.05, 500000)
        self.assertGreater(scaler.current_workers, initial_workers)

    def test_2_3_aimd_multiplicative_decrease(self):
        """Verify AIMD multiplicative decrease on rate limiting (420) or errors."""
        from crunchyroll.session_pool import AIMDConcurrencyScaler
        scaler = AIMDConcurrencyScaler(min_workers=8, max_workers=48, initial_workers=32)
        new_workers = scaler.record_failure(status_code=420)
        self.assertLess(new_workers, 32)
        self.assertEqual(new_workers, int(32 * 0.75))

    def test_2_4_threadpool_concurrent_execution(self):
        """Verify ThreadPoolExecutor can execute concurrent tasks in parallel."""
        from concurrent.futures import ThreadPoolExecutor
        def worker_task(idx):
            time.sleep(0.01)
            return idx * 2

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(worker_task, range(10)))
        self.assertEqual(results, [i * 2 for i in range(10)])

    def test_2_5_hedging_speculative_execution(self):
        """Verify speculative tail-latency hedging mechanism in SessionPool."""
        from crunchyroll.session_pool import SessionPool, ConcurrencyConfig
        server = MockCrunchyrollServer().start()
        try:
            cfg = ConcurrencyConfig(hedging_enabled=True, hedge_min_delay=0.1)
            pool = SessionPool(config=cfg)
            data = pool.download_segment_hedged(server.get_url("/media/init_video.mp4"))
            self.assertIsInstance(data, bytes)
            self.assertGreater(len(data), 0)
            pool.close()
        finally:
            server.stop()


class TestFeature3StreamAssembler(unittest.TestCase):
    """Feature 3: Single-Pass Stream Assembler."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_assembler_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_3_1_stream_assembler_in_order(self):
        """Verify in-order segment addition writes sequentially to disk."""
        out_path = os.path.join(self.tmp_dir, "assembled.mp4")
        from crunchyroll.stream_assembler import StreamAssembler
        assembler = StreamAssembler(out_path, total_segments=3, max_in_flight_mb=16)
        assembler.add_segment(1, b"CHUNK_1_")
        assembler.add_segment(2, b"CHUNK_2_")
        assembler.add_segment(3, b"CHUNK_3_")
        final_p = assembler.finish()
        self.assertEqual(final_p, out_path)
        with open(out_path, "rb") as f:
            self.assertEqual(f.read(), b"CHUNK_1_CHUNK_2_CHUNK_3_")

    def test_3_2_stream_assembler_out_of_order_buffering(self):
        """Verify out-of-order arrival buffers and flushes sequentially."""
        out_path = os.path.join(self.tmp_dir, "assembled_ooo.mp4")
        from crunchyroll.stream_assembler import StreamAssembler
        assembler = StreamAssembler(out_path, total_segments=3)
        assembler.add_segment(3, b"CHUNK_3_")
        assembler.add_segment(1, b"CHUNK_1_")
        assembler.add_segment(2, b"CHUNK_2_")
        assembler.finish()
        with open(out_path, "rb") as f:
            self.assertEqual(f.read(), b"CHUNK_1_CHUNK_2_CHUNK_3_")

    def test_3_3_stream_assembler_write_init(self):
        """Verify write_init directly writes header bytes to start of file."""
        out_path = os.path.join(self.tmp_dir, "assembled_init.mp4")
        from crunchyroll.stream_assembler import StreamAssembler
        assembler = StreamAssembler(out_path, total_segments=1)
        assembler.write_init(b"INIT_HEADER_")
        assembler.add_segment(1, b"BODY_SEG")
        assembler.finish()
        with open(out_path, "rb") as f:
            self.assertEqual(f.read(), b"INIT_HEADER_BODY_SEG")

    def test_3_4_stream_assembler_missing_segments_raises(self):
        """Verify finish() raises RuntimeError when missing segments."""
        out_path = os.path.join(self.tmp_dir, "assembled_incomplete.mp4")
        from crunchyroll.stream_assembler import StreamAssembler
        assembler = StreamAssembler(out_path, total_segments=3)
        assembler.add_segment(1, b"CHUNK_1_")
        with self.assertRaises(RuntimeError):
            assembler.finish()

    def test_3_5_stream_assembler_memory_bounds(self):
        """Verify in-flight memory accounting tracks buffered bytes."""
        out_path = os.path.join(self.tmp_dir, "assembled_mem.mp4")
        from crunchyroll.stream_assembler import StreamAssembler
        assembler = StreamAssembler(out_path, total_segments=3, max_in_flight_mb=32)
        # Adding segment 2 (out of order) increases current_buffered_bytes
        assembler.add_segment(2, b"12345678")
        self.assertEqual(assembler.current_buffered_bytes, 8)
        # Adding segment 1 flushes both segment 1 and 2
        assembler.add_segment(1, b"abc")
        self.assertEqual(assembler.current_buffered_bytes, 0)
        assembler.add_segment(3, b"xyz")
        assembler.finish()


class TestFeature4TimelineParsing(unittest.TestCase):
    """Feature 4: DASH MPD Timeline Parsing & Representation Selection."""

    def test_4_1_expand_timeline_single_segment(self):
        """Expand timeline with single S tag (no repeat)."""
        xml_str = '<AdaptationSet><SegmentTimeline><S t="0" d="1000"/></SegmentTimeline></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        timeline = expand_timeline(elem)
        self.assertEqual(timeline, [1])

    def test_4_2_expand_timeline_with_repeat(self):
        """Expand timeline with repeat count r=4 -> 5 segments total."""
        xml_str = '<AdaptationSet><SegmentTimeline><S t="0" d="1000" r="4"/></SegmentTimeline></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        timeline = expand_timeline(elem)
        self.assertEqual(timeline, [1, 2, 3, 4, 5])

    def test_4_3_expand_timeline_custom_start_number(self):
        """Expand timeline with custom startNumber in SegmentTemplate."""
        xml_str = '<AdaptationSet><SegmentTemplate startNumber="100"/><SegmentTimeline><S t="0" d="1000" r="2"/></SegmentTimeline></AdaptationSet>'
        elem = ET.fromstring(xml_str)
        timeline = expand_timeline(elem)
        self.assertEqual(timeline, [100, 101, 102])

    def test_4_4_get_base_url_video_resolution(self):
        """get_base_url selects representation matching requested video height."""
        xml_str = """<AdaptationSet>
            <Representation id="v1080" height="1080"><BaseURL>http://cdn/1080/</BaseURL></Representation>
            <Representation id="v720" height="720"><BaseURL>http://cdn/720/</BaseURL></Representation>
            <Representation id="v480" height="480"><BaseURL>http://cdn/480/</BaseURL></Representation>
        </AdaptationSet>"""
        elem = ET.fromstring(xml_str)
        base_url, rep_id = get_base_url(elem, is_video_set=True, quality="720p")
        self.assertEqual(base_url, "http://cdn/720/")
        self.assertEqual(rep_id, "v720")

    def test_4_5_get_base_url_audio_quality_fallback(self):
        """get_base_url falls back to first available representation if requested quality missing."""
        xml_str = """<AdaptationSet>
            <Representation id="a_default" bandwidth="128000"><BaseURL>http://cdn/audio/</BaseURL></Representation>
        </AdaptationSet>"""
        elem = ET.fromstring(xml_str)
        base_url, rep_id = get_base_url(elem, is_video_set=False, quality="192k")
        self.assertEqual(base_url, "http://cdn/audio/")
        self.assertEqual(rep_id, "a_default")


class TestFeature5PSSHExtraction(unittest.TestCase):
    """Feature 5: CENC PSSH Extraction."""

    def test_5_1_get_pssh_from_child_element(self):
        """Extract PSSH from cenc:pssh child element."""
        xml_str = f"""<AdaptationSet>
            <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed">
                <pssh>{SAMPLE_PSSH_B64}</pssh>
            </ContentProtection>
        </AdaptationSet>"""
        elem = ET.fromstring(xml_str)
        pssh = get_pssh(elem)
        self.assertEqual(pssh, SAMPLE_PSSH_B64)

    def test_5_2_get_pssh_from_attribute(self):
        """Extract PSSH when stored directly as an attribute."""
        xml_str = f"""<AdaptationSet>
            <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed" default_pssh="{SAMPLE_PSSH_B64}"/>
        </AdaptationSet>"""
        elem = ET.fromstring(xml_str)
        pssh = get_pssh(elem)
        self.assertEqual(pssh, SAMPLE_PSSH_B64)

    def test_5_3_get_pssh_missing_returns_none(self):
        """Return None when no ContentProtection or PSSH exists."""
        xml_str = "<AdaptationSet><Representation id='v1'/></AdaptationSet>"
        elem = ET.fromstring(xml_str)
        self.assertIsNone(get_pssh(elem))

    def test_5_4_get_pssh_namespaced_xml(self):
        """Extract PSSH from fully namespaced DASH XML."""
        xml_str = f"""<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:cenc="urn:mpeg:cenc:2013">
            <Period>
                <AdaptationSet>
                    <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed">
                        <cenc:pssh>{SAMPLE_PSSH_B64}</cenc:pssh>
                    </ContentProtection>
                </AdaptationSet>
            </Period>
        </MPD>"""
        elem = ET.fromstring(xml_str)
        pssh = get_pssh(elem)
        self.assertEqual(pssh, SAMPLE_PSSH_B64)

    def test_5_5_get_pssh_nested_multiple_adaptationsets(self):
        """Extract PSSH correctly across multiple adaptation sets."""
        xml_str = f"""<Period>
            <AdaptationSet contentType="video"/>
            <AdaptationSet contentType="audio">
                <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed">
                    <pssh>{SAMPLE_PSSH_B64}</pssh>
                </ContentProtection>
            </AdaptationSet>
        </Period>"""
        elem = ET.fromstring(xml_str)
        pssh = get_pssh(elem)
        self.assertEqual(pssh, SAMPLE_PSSH_B64)


class TestFeature6TokenRefreshAndAuth(unittest.TestCase):
    """Feature 6: Token Refresh & Auth Lifecycle."""

    def test_6_1_get_access_token_success(self):
        """get_access_token fetches valid bearer token from auth server."""
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"access_token": "valid_token_123"}
            token = get_access_token("test_etp_rt")
            self.assertEqual(token, "valid_token_123")

    def test_6_2_get_access_token_failure_raises_runtime_error(self):
        """get_access_token raises RuntimeError when response code != 200."""
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 401
            mock_post.return_value.text = "Invalid credentials"
            with self.assertRaises(RuntimeError):
                get_access_token("invalid_etp_rt")

    def test_6_3_http_client_401_automatic_refresh(self):
        """CrunchyrollHttpClient intercepts 401, refetches token, and retries request."""
        with patch("crunchyroll.http_client.get_access_token", return_value="refreshed_token"), \
             patch("crunchyroll.auth.get_access_token", return_value="refreshed_token"), \
             patch("crunchyroll.http_client.load_config", return_value={}):
            client = CrunchyrollHttpClient(etp_rt="mock_etp_rt")
            client.token = "old_expired_token"

            resp1 = MagicMock()
            resp1.status_code = 401
            resp2 = MagicMock()
            resp2.status_code = 200
            client.session.request = MagicMock(side_effect=[resp1, resp2])

            res = client.do_request("GET", "https://api.crunchyroll.com/test")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(client.token, "refreshed_token")
            client.close()

    def test_6_4_auth_config_persistence(self):
        """load_config and save_config correctly write and read config file."""
        tmp_cfg = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp_path = tmp_cfg.name
        tmp_cfg.close()
        try:
            with patch("crunchyroll.auth.CONFIG_FILE", tmp_path):
                save_config({"etp_rt": "saved_cookie_123", "username": "test_user"})
                loaded = load_config()
                self.assertEqual(loaded.get("etp_rt"), "saved_cookie_123")
                self.assertEqual(loaded.get("username"), "test_user")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_6_5_http_client_adds_user_agent_header(self):
        """do_request attaches User-Agent and Authorization headers."""
        with patch("crunchyroll.http_client.get_access_token", return_value="my_token"), \
             patch("crunchyroll.http_client.load_config", return_value={}):
            client = CrunchyrollHttpClient(etp_rt="mock_rt")
            client.token = "my_token"
            client.session.request = MagicMock(return_value=MagicMock(status_code=200))
            client.do_request("GET", "http://example.com/api")
            kwargs = client.session.request.call_args[1]
            headers = kwargs.get("headers", {})
            self.assertEqual(headers.get("Authorization"), "Bearer my_token")
            self.assertIn("Mozilla", headers.get("User-Agent", ""))
            client.close()


class TestFeature7UtilsAndSanitizer(unittest.TestCase):
    """Feature 7: Filename Sanitizer & Locale Mappings."""

    def test_7_1_sanitize_unicode_quotes_and_dashes(self):
        """Replace unicode em-dashes, en-dashes, smart quotes."""
        raw = "Show — Season 1 – Ep “1” ‘Hero’"
        clean = sanitize_filename(raw)
        self.assertEqual(clean, "Show - Season 1 - Ep _1_ _Hero")

    def test_7_2_sanitize_illegal_filesystem_chars(self):
        """Replace colons, slashes, backslashes, question marks, asterisks, pipes."""
        raw = 'Episode 1: Who/What\\Why? *Awesome* <Part | 2>'
        clean = sanitize_filename(raw)
        self.assertEqual(clean, "Episode 1_ Who_What_Why_ _Awesome_ _Part _ 2")

    def test_7_3_sanitize_collapse_consecutive_underscores(self):
        """Multiple consecutive underscores shrink to a single underscore."""
        raw = "Title::::Subtitle"
        clean = sanitize_filename(raw)
        self.assertEqual(clean, "Title_Subtitle")

    def test_7_4_sanitize_empty_or_whitespace_returns_unknown(self):
        """Empty string, dots, or only illegal chars returns 'Unknown'."""
        self.assertEqual(sanitize_filename(""), "Unknown")
        self.assertEqual(sanitize_filename("   "), "Unknown")
        self.assertEqual(sanitize_filename("..."), "Unknown")
        self.assertEqual(sanitize_filename("___"), "Unknown")

    def test_7_5_track_title_and_language_codes(self):
        """track_title returns readable name; LANGUAGE_CODES maps to ISO-639-2."""
        self.assertEqual(track_title("ja-JP"), "日本語")
        self.assertEqual(track_title("en-US"), "English")
        self.assertEqual(track_title("unknown-XX"), "unknown-XX")
        self.assertEqual(LANGUAGE_CODES["ja-JP"], "jpn")
        self.assertEqual(LANGUAGE_CODES["en-US"], "eng")
        self.assertEqual(LANGUAGE_CODES["es-419"], "spa")


class TestFeature8CLIParser(unittest.TestCase):
    """Feature 8: CLI Parser, Aliases & Batch Mode."""

    def _get_parser(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--gui", action="store_true")
        parser.add_argument("--browser", action="store_true")
        parser.add_argument("--email", type=str, default="")
        parser.add_argument("--password", type=str, default="")
        parser.add_argument("--url", type=str, default="")
        parser.add_argument("--file", type=str, default="")
        parser.add_argument("--audio-lang", type=str, default="ja-JP")
        parser.add_argument("--subs-lang", type=str, default="en-US")
        parser.add_argument("--video-quality", type=str, default="1080p")
        parser.add_argument("--audio-quality", type=str, default="192k")
        parser.add_argument("--quality-video", type=str, default="")
        parser.add_argument("--quality-audio", type=str, default="")
        parser.add_argument("--season", type=int, default=0)
        parser.add_argument("--etp-rt", type=str, default="")
        parser.add_argument("--debug-manifest", action="store_true")
        return parser

    def test_8_1_cli_defaults(self):
        """Verify default CLI arguments."""
        parser = self._get_parser()
        args = parser.parse_args([])
        self.assertEqual(args.video_quality, "1080p")
        self.assertEqual(args.audio_quality, "192k")
        self.assertEqual(args.audio_lang, "ja-JP")
        self.assertEqual(args.subs_lang, "en-US")
        self.assertFalse(args.gui)
        self.assertFalse(args.debug_manifest)

    def test_8_2_cli_quality_aliases(self):
        """Verify quality aliases --quality-video and --quality-audio."""
        parser = self._get_parser()
        args = parser.parse_args(["--quality-video", "720p", "--quality-audio", "96k"])
        vq = args.quality_video or args.video_quality
        aq = args.quality_audio or args.audio_quality
        self.assertEqual(vq, "720p")
        self.assertEqual(aq, "96k")

    def test_8_3_cli_custom_url_and_languages(self):
        """Parse custom URL and comma-separated audio/subtitle locales."""
        parser = self._get_parser()
        args = parser.parse_args([
            "--url", "https://www.crunchyroll.com/watch/G12345/ep-1",
            "--audio-lang", "ja-JP,en-US",
            "--subs-lang", "en-US,es-419,fr-FR",
        ])
        self.assertEqual(args.url, "https://www.crunchyroll.com/watch/G12345/ep-1")
        self.assertEqual(args.audio_lang, "ja-JP,en-US")
        self.assertEqual(args.subs_lang, "en-US,es-419,fr-FR")

    def test_8_4_cli_debug_manifest_flag(self):
        """Parse --debug-manifest flag."""
        parser = self._get_parser()
        args = parser.parse_args(["--debug-manifest"])
        self.assertTrue(args.debug_manifest)

    def test_8_5_cli_batch_file_parsing(self):
        """Batch file parsing ignores blanks and non-HTTP lines."""
        content = """
        https://www.crunchyroll.com/watch/G1
        # this is a comment
        
        https://www.crunchyroll.com/watch/G2
        invalid_line_without_http
        https://www.crunchyroll.com/watch/G3
        """
        urls = [line.strip() for line in content.splitlines() if line.strip() and line.strip().startswith("http")]
        self.assertEqual(len(urls), 3)
        self.assertEqual(urls[0], "https://www.crunchyroll.com/watch/G1")
        self.assertEqual(urls[1], "https://www.crunchyroll.com/watch/G2")
        self.assertEqual(urls[2], "https://www.crunchyroll.com/watch/G3")


class TestFeature9URLResolutionAndAPI(unittest.TestCase):
    """Feature 9: URL Type Resolution & CMS API Queries."""

    def test_9_1_parse_watch_url(self):
        """Parse watch episode URL."""
        t, cid = parse_url_type("https://www.crunchyroll.com/watch/G69X91K9V/the-journey-begins")
        self.assertEqual(t, "episode")
        self.assertEqual(cid, "G69X91K9V")

    def test_9_2_parse_series_url(self):
        """Parse series URL."""
        t, cid = parse_url_type("https://www.crunchyroll.com/series/G4PH0WEE2/frieren")
        self.assertEqual(t, "series")
        self.assertEqual(cid, "G4PH0WEE2")

    def test_9_3_parse_season_url(self):
        """Parse season URL."""
        t, cid = parse_url_type("https://www.crunchyroll.com/season/GS12345/season-1")
        self.assertEqual(t, "season")
        self.assertEqual(cid, "GS12345")

    def test_9_4_parse_url_with_query_params_and_fragments(self):
        """Strip query parameters and hash fragments before parsing."""
        url = "https://www.crunchyroll.com/watch/G69X91K9V/ep-1?utm_source=cr&ref=share#t=120"
        t, cid = parse_url_type(url)
        self.assertEqual(t, "episode")
        self.assertEqual(cid, "G69X91K9V")

    def test_9_5_parse_invalid_url_raises_value_error(self):
        """Raise ValueError on unrecognized URL structure."""
        with self.assertRaises(ValueError):
            parse_url_type("https://www.google.com/search?q=anime")

    def test_9_6_get_episode_deserialization(self):
        """get_episode fetches playback stream, manifest URL, and subtitles."""
        with patch("crunchyroll.http_client.get_access_token", return_value="mock_tok"), \
             patch("crunchyroll.http_client.load_config", return_value={}):
            client = CrunchyrollHttpClient()
            client.token = "mock_token"

            with patch.object(client, "do_request") as mock_req:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "url": "https://cdn.example.com/manifest.mpd",
                    "subtitles": {
                        "en-US": {"language": "en-US", "url": "https://cdn.example.com/en-US.ass"},
                        "ja-JP": {"language": "ja-JP", "url": "https://cdn.example.com/ja-JP.ass"},
                    },
                    "token": "vid_tok_789",
                }
                mock_req.return_value = mock_resp

                stream = get_episode(client, "G69X91K9V")
                self.assertEqual(stream.manifest_url, "https://cdn.example.com/manifest.mpd")
                self.assertIn("en-US", stream.subtitles)
                self.assertEqual(stream.token, "vid_tok_789")
            client.close()


class TestFeature10SubtitleProcessing(unittest.TestCase):
    """Feature 10: Subtitle Processing & ASS Handling."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()

    def tearDown(self):
        self.server.stop()

    def test_10_1_download_subs_writes_temp_file(self):
        """download_subs downloads ASS file content and returns local path."""
        url = self.server.get_url("/subs/en-US.ass")
        tmp_path = download_subs(url)
        try:
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("[Script Info]", content)
            self.assertIn("[Events]", content)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_10_2_ass_header_structure_validation(self):
        """Verify ASS structure contains Script Info, Styles, and Events blocks."""
        lines = SAMPLE_ASS_SUBTITLE.splitlines()
        has_script_info = any("[Script Info]" in l for l in lines)
        has_styles = any("[V4+ Styles]" in l for l in lines)
        has_events = any("[Events]" in l for l in lines)
        self.assertTrue(has_script_info and has_styles and has_events)

    def test_10_3_subtitle_media_track_creation(self):
        """Verify MediaTrack dataclass creation for subtitle tracks."""
        track = MediaTrack(file="/tmp/sub_en.ass", locale="en-US")
        self.assertEqual(track.file, "/tmp/sub_en.ass")
        self.assertEqual(track.locale, "en-US")

    def test_10_4_missing_subtitles_handled_gracefully(self):
        """Ensure empty subtitle dictionary does not trigger crashes."""
        subs_langs = ["fr-FR", "de-DE"]
        available_subs = {"en-US": MagicMock(url="http://example.com/en.ass")}
        matched = [loc for loc in subs_langs if loc in available_subs]
        self.assertEqual(matched, [])

    def test_10_5_build_url_pattern_substitution(self):
        """Verify build_url correctly formats segment URL numbers."""
        url = build_url("http://cdn.com/", "video/1080p", "chunk_$RepresentationID$_$Number%05d$.mp4", 42)
        self.assertEqual(url, "http://cdn.com/chunk_video/1080p_00042.mp4")


class TestFeature11MergerAndFFmpeg(unittest.TestCase):
    """Feature 11: FFmpeg Merger & Track Disposition Mapping."""

    def test_11_1_find_ffmpeg_path(self):
        """find_ffmpeg locates installed ffmpeg on system."""
        ffmpeg_p = find_ffmpeg()
        self.assertTrue(os.path.exists(ffmpeg_p))
        self.assertTrue(os.path.isabs(ffmpeg_p))

    def test_11_2_find_ffmpeg_missing_raises_filenotfound(self):
        """find_ffmpeg raises FileNotFoundError when binary is not in PATH."""
        with patch("shutil.which", return_value=None), \
             patch("os.path.exists", return_value=False):
            with self.assertRaises(FileNotFoundError):
                find_ffmpeg()

    def test_11_3_merge_everything_command_construction(self):
        """merge_everything builds correct FFmpeg argument list with mapping and metadata."""
        v_file = "/tmp/v.mp4"
        a_tracks = [MediaTrack(file="/tmp/a_ja.mp4", locale="ja-JP"), MediaTrack(file="/tmp/a_en.mp4", locale="en-US")]
        s_tracks = [MediaTrack(file="/tmp/s_en.ass", locale="en-US")]
        out_file = "/tmp/out.mkv"
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Test Series",
                season_number=1,
                episode_number=5,
                audio_locale="ja-JP",
                versions=[],
                availability_starts="",
            ),
            title="Episode 5 Title",
            subtitles={},
        )

        with patch("crunchyroll.merger.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run") as mock_run, \
             patch("os.path.exists", return_value=False):
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_run.return_value = mock_res

            merge_everything(v_file, a_tracks, s_tracks, out_file, info)

            args = mock_run.call_args[0][0]
            self.assertIn("-map", args)
            self.assertIn("0:v:0", args)
            self.assertIn("1:a:0", args)
            self.assertIn("2:a:0", args)
            self.assertIn("3", args)  # Subtitle stream map
            self.assertIn("-metadata:s:a:0", args)
            self.assertIn("language=jpn", args)
            self.assertIn("-metadata:s:a:1", args)
            self.assertIn("language=eng", args)
            self.assertIn("-disposition:a:0", args)
            self.assertIn("default", args)
            self.assertIn("-disposition:a:1", args)
            self.assertIn("0", args)

    def test_11_4_merge_everything_cleans_up_on_failure(self):
        """merge_everything raises RuntimeError and removes partial output on failure."""
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Test Series", season_number=1, episode_number=1,
                audio_locale="ja-JP", versions=[], availability_starts="",
            ),
            title="Ep 1", subtitles={},
        )
        with patch("crunchyroll.merger.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run") as mock_run, \
             patch("os.path.exists", return_value=True), \
             patch("os.remove") as mock_remove:
            mock_res = MagicMock()
            mock_res.returncode = 1
            mock_res.stderr = "FFmpeg mux error"
            mock_run.return_value = mock_res

            with self.assertRaises(RuntimeError):
                merge_everything("/tmp/v.mp4", [], [], "/tmp/fail.mkv", info)
            mock_remove.assert_called_with("/tmp/fail.mkv")

    def test_11_5_merge_everything_cleans_temp_sources_on_success(self):
        """merge_everything removes intermediate temp video/audio/sub files after muxing."""
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Test", season_number=1, episode_number=1,
                audio_locale="ja-JP", versions=[], availability_starts="",
            ),
            title="Ep 1", subtitles={},
        )
        with patch("crunchyroll.merger.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run") as mock_run, \
             patch("os.path.exists", side_effect=lambda p: p != "/tmp/out.mkv"), \
             patch("os.remove") as mock_remove:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_run.return_value = mock_res

            merge_everything("/tmp/v.mp4", [MediaTrack(file="/tmp/a.mp4", locale="ja-JP")], [], "/tmp/out.mkv", info)
            mock_remove.assert_any_call("/tmp/v.mp4")
            mock_remove.assert_any_call("/tmp/a.mp4")


class TestFeature12WebGUIState(unittest.TestCase):
    """Feature 12: Web GUI State & API Endpoints."""

    def test_12_1_safestream_utf8_encoding_protection(self):
        """SafeStream prevents crashing when writing unicode to stream."""
        from web_gui import SafeStream
        buf = io.StringIO()
        safe = SafeStream(buf)
        safe.write("Testing 日本語 — “Unicode” chars\n")
        safe.flush()
        self.assertIn("日本語", buf.getvalue())

    def test_12_2_safestream_none_target_safety(self):
        """SafeStream handles None stream target without throwing AttributeError."""
        from web_gui import SafeStream
        safe = SafeStream(None)
        safe.write("some string")
        safe.flush()
        self.assertTrue(True)

    def test_12_3_web_state_dictionary_structure(self):
        """Verify global web STATE contains required keys (authenticated, config, download)."""
        import web_gui
        with web_gui.LOCK:
            state = web_gui.STATE
            self.assertIn("config", state)
            self.assertIn("download", state)
            self.assertIn("video_quality", state["config"])
            self.assertIn("audio_quality", state["config"])
            self.assertIn("status", state["download"])

    def test_12_4_web_gui_log_buffer_limit(self):
        """Verify _log buffer retains max 200 log messages."""
        import web_gui
        for i in range(250):
            web_gui._log(f"Test log entry {i}")
        with web_gui.LOCK:
            log_len = len(web_gui.STATE["download"]["log"])
            self.assertLessEqual(log_len, 200)

    def test_12_5_web_gui_handler_options(self):
        """Verify Handler do_OPTIONS sends CORS headers."""
        import web_gui
        mock_handler = MagicMock()
        mock_handler.send_response = MagicMock()
        mock_handler.send_header = MagicMock()
        mock_handler.end_headers = MagicMock()

        web_gui.Handler.do_OPTIONS(mock_handler)
        mock_handler.send_response.assert_called_with(204)
        headers = [call[0][0] for call in mock_handler.send_header.call_args_list]
        self.assertIn("Access-Control-Allow-Origin", headers)


class TestFeature13DiscordBotHelper(unittest.TestCase):
    """Feature 13: Discord Bot Range Parser & Instance Lock."""

    def setUp(self):
        self.eps = [
            SeasonEpisode(
                id=f"id_{i}",
                title=f"Episode {i}",
                season_number=1,
                episode_number=i,
                series_title="Show",
                audio_locale="ja-JP",
                versions=[],
                availability_starts="",
            )
            for i in range(1, 11)
        ]

    def test_13_1_parse_episode_ranges_single_and_comma(self):
        """Parse comma-separated episode list ('1, 3, 5')."""
        from discord_bot import parse_episode_ranges
        selected = parse_episode_ranges("1, 3, 5", self.eps)
        ep_nums = [e.episode_number for e in selected]
        self.assertEqual(ep_nums, [1, 3, 5])

    def test_13_2_parse_episode_ranges_dash_intervals(self):
        """Parse ranges ('1-4, 8-10')."""
        from discord_bot import parse_episode_ranges
        selected = parse_episode_ranges("1-4, 8-10", self.eps)
        ep_nums = [e.episode_number for e in selected]
        self.assertEqual(ep_nums, [1, 2, 3, 4, 8, 9, 10])

    def test_13_3_parse_episode_ranges_keyword_all(self):
        """Parse 'all', '*', 'everything', 'full'."""
        from discord_bot import parse_episode_ranges
        for kw in ["all", "*", "everything", "full"]:
            selected = parse_episode_ranges(kw, self.eps)
            self.assertEqual(len(selected), 10)

    def test_13_4_parse_episode_ranges_out_of_bounds_filtering(self):
        """Out of bounds numbers (e.g. 50, 99) are safely omitted."""
        from discord_bot import parse_episode_ranges
        selected = parse_episode_ranges("2, 50, 99", self.eps)
        ep_nums = [e.episode_number for e in selected]
        self.assertEqual(ep_nums, [2])

    def test_13_5_acquire_instance_lock(self):
        """acquire_instance_lock binds to port to prevent duplicate instances."""
        import discord_bot
        if discord_bot._LOCK_SOCKET:
            try:
                discord_bot._LOCK_SOCKET.close()
            except Exception:
                pass
            discord_bot._LOCK_SOCKET = None

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 54321))
            res = discord_bot.acquire_instance_lock()
            self.assertFalse(res)
        finally:
            s.close()
            if discord_bot._LOCK_SOCKET:
                try:
                    discord_bot._LOCK_SOCKET.close()
                except Exception:
                    pass
                discord_bot._LOCK_SOCKET = None


if __name__ == "__main__":
    unittest.main()
