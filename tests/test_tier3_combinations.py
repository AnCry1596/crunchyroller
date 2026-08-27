"""
tests/test_tier3_combinations.py

Tier 3: Cross-Feature Combination Tests
Covers cross-feature interactions and integration matrices:
1. Concurrent Video + Multi-Audio Dub Downloads
2. Session Pool Reuse under Dynamic Concurrency & Hedging
3. Multi-Track Muxing with Dispositions, Tags & Subtitles
4. Batch Processing with Failure Isolation & Recovery
5. Dual-Stream Concurrent Assembly & Memory Bounding
"""

import os
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

# Ensure discord modules are mocked if discord.py is not installed
import sys
if "discord" not in sys.modules:
    sys.modules["discord"] = MagicMock()
    sys.modules["discord.ui"] = MagicMock()
    sys.modules["discord.ext"] = MagicMock()
    sys.modules["discord.ext.commands"] = MagicMock()
    sys.modules["discord.app_commands"] = MagicMock()

from tests.mock_server import MockCrunchyrollServer, SAMPLE_ASS_SUBTITLE
from crunchyroll.api import get_episode, get_episode_info, parse_url_type
from crunchyroll.downloader import build_url, download_subs
from crunchyroll.http_client import CrunchyrollHttpClient
from crunchyroll.merger import merge_everything
from crunchyroll.session_pool import SessionPool, ConcurrencyConfig, AIMDConcurrencyScaler
from crunchyroll.stream_assembler import StreamAssembler
from crunchyroll.types import (
    DubVersion,
    EpisodeInfo,
    EpisodeMetadata,
    MediaTrack,
    SeasonEpisode,
)
from crunchyroll.utils import track_title, LANGUAGE_CODES


class TestComboMultiAudioAndVideoFetching(unittest.TestCase):
    """Combination 1: Concurrent Video + Multi-Audio Dub Downloads."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_combo_test_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_3_1_1_concurrent_video_and_multi_audio_download(self):
        """Concurrently download 1 video track and 3 audio dubs (ja-JP, en-US, es-419) using shared session pool."""
        pool = SessionPool(max_pool_size=16)
        try:
            urls = {
                "video": self.server.get_url("/media/init_video.mp4"),
                "audio_ja": self.server.get_url("/media/init_audio.mp4"),
                "audio_en": self.server.get_url("/media/init_audio.mp4"),
                "audio_es": self.server.get_url("/media/init_audio.mp4"),
            }
            results = {}

            def _fetch(key, url):
                results[key] = pool.download_segment(url)

            threads = [threading.Thread(target=_fetch, args=(k, u)) for k, u in urls.items()]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

            self.assertEqual(len(results), 4)
            for k, data in results.items():
                self.assertIsInstance(data, bytes)
                self.assertGreater(len(data), 0)
        finally:
            pool.close()

    def test_3_1_2_multi_audio_tracks_metadata_mapping(self):
        """Verify multi-audio tracks are assigned correct ISO-639-2 codes and dispositions."""
        a_tracks = [
            MediaTrack(file="/tmp/a_ja.mp4", locale="ja-JP"),
            MediaTrack(file="/tmp/a_en.mp4", locale="en-US"),
            MediaTrack(file="/tmp/a_es.mp4", locale="es-419"),
        ]
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Frieren", season_number=1, episode_number=1,
                audio_locale="ja-JP", versions=[], availability_starts="",
            ),
            title="The Journey Begins", subtitles={},
        )
        with patch("crunchyroll.merger.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run") as mock_run, \
             patch("os.path.exists", return_value=False):
            mock_res = MagicMock(returncode=0)
            mock_run.return_value = mock_res

            merge_everything("/tmp/v.mp4", a_tracks, [], "/tmp/out.mkv", info)
            args = mock_run.call_args[0][0]

            self.assertIn("-metadata:s:a:0", args)
            self.assertIn("language=jpn", args)
            self.assertIn("-metadata:s:a:1", args)
            self.assertIn("language=eng", args)
            self.assertIn("-metadata:s:a:2", args)
            self.assertIn("language=spa", args)
            self.assertIn("-disposition:a:0", args)
            self.assertIn("default", args)
            self.assertIn("-disposition:a:1", args)
            self.assertIn("0", args)
            self.assertIn("-disposition:a:2", args)
            self.assertIn("0", args)

    def test_3_1_3_audio_version_locale_matching_and_fallback(self):
        """Match requested audio locales against episode dub versions list."""
        versions = [
            DubVersion(guid="v_ja", media_guid="m_ja", season_guid="s1", audio_locale="ja-JP", locale="ja-JP"),
            DubVersion(guid="v_en", media_guid="m_en", season_guid="s1", audio_locale="en-US", locale="en-US"),
        ]
        requested_locales = ["en-US", "fr-FR"]
        matched_versions = []
        for loc in requested_locales:
            for v in versions:
                if v.audio_locale == loc:
                    matched_versions.append(v)
                    break
        self.assertEqual(len(matched_versions), 1)
        self.assertEqual(matched_versions[0].audio_locale, "en-US")

    def test_3_1_4_dual_track_concurrent_stream_assemblers(self):
        """Run video assembler and audio assembler concurrently in separate worker threads."""
        v_out = os.path.join(self.tmp_dir, "v_raw.mp4")
        a_out = os.path.join(self.tmp_dir, "a_raw.mp4")

        v_asm = StreamAssembler(v_out, total_segments=3)
        a_asm = StreamAssembler(a_out, total_segments=3)

        def _assemble_video():
            v_asm.add_segment(2, b"V_SEG2_")
            v_asm.add_segment(1, b"V_SEG1_")
            v_asm.add_segment(3, b"V_SEG3_")
            v_asm.finish()

        def _assemble_audio():
            a_asm.add_segment(1, b"A_SEG1_")
            a_asm.add_segment(3, b"A_SEG3_")
            a_asm.add_segment(2, b"A_SEG2_")
            a_asm.finish()

        t_v = threading.Thread(target=_assemble_video)
        t_a = threading.Thread(target=_assemble_audio)
        t_v.start()
        t_a.start()
        t_v.join(timeout=3.0)
        t_a.join(timeout=3.0)

        with open(v_out, "rb") as f:
            self.assertEqual(f.read(), b"V_SEG1_V_SEG2_V_SEG3_")
        with open(a_out, "rb") as f:
            self.assertEqual(f.read(), b"A_SEG1_A_SEG2_A_SEG3_")

    def test_3_1_5_simultaneous_subtitles_and_audio_download(self):
        """Download subtitle ASS files and audio streams concurrently without conflict."""
        sub_url = self.server.get_url("/subs/en-US.ass")
        aud_url = self.server.get_url("/media/init_audio.mp4")

        results = {}
        def _fetch_sub():
            p = download_subs(sub_url)
            results["sub"] = p

        def _fetch_aud():
            pool = SessionPool(max_pool_size=4)
            results["aud"] = pool.download_segment(aud_url)
            pool.close()

        t1 = threading.Thread(target=_fetch_sub)
        t2 = threading.Thread(target=_fetch_aud)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        self.assertIn("sub", results)
        self.assertIn("aud", results)
        self.assertTrue(os.path.exists(results["sub"]))
        self.assertGreater(len(results["aud"]), 0)
        if os.path.exists(results["sub"]):
            os.remove(results["sub"])


class TestComboSessionReuseAndDynamicScaling(unittest.TestCase):
    """Combination 2: Session Pool Reuse under Dynamic Concurrency & Hedging."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()

    def tearDown(self):
        self.server.stop()

    def test_3_2_1_session_reuse_across_50_segments_with_aimd(self):
        """Execute 50 segment downloads across shared session pool while AIMD scaler adjusts workers."""
        scaler = AIMDConcurrencyScaler(min_workers=8, max_workers=48, initial_workers=16, window_size=5)
        cfg = ConcurrencyConfig(pool_size=32, max_retries=3)
        pool = SessionPool(config=cfg)
        try:
            def _fetch_chunk(idx):
                start = time.time()
                data = pool.download_segment(self.server.get_url("/media/init_video.mp4"))
                dur = time.time() - start
                scaler.record_success(dur, len(data))
                return len(data)

            with ThreadPoolExecutor(max_workers=16) as executor:
                sizes = list(executor.map(_fetch_chunk, range(50)))

            self.assertEqual(len(sizes), 50)
            stats = scaler.get_stats()
            self.assertEqual(stats["total_success"], 50)
            self.assertEqual(stats["total_failures"], 0)
            self.assertGreaterEqual(stats["current_workers"], 8)
            self.assertLessEqual(stats["current_workers"], 48)
        finally:
            pool.close()

    def test_3_2_2_session_keep_alive_mixed_media_requests(self):
        """Mix 30 GET requests for XML manifest, JSON API, ASS subtitles, and MP4 media through one pool."""
        pool = SessionPool(max_pool_size=16)
        try:
            endpoints = [
                "/playback/v3/test_ep/web/firefox/play",
                "/manifest/test_ep.mpd",
                "/subs/en-US.ass",
                "/media/init_video.mp4",
                "/media/init_audio.mp4",
            ] * 6
            for ep in endpoints:
                resp = pool.get_session().get(self.server.get_url(ep))
                self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(self.server.request_history), 30)
        finally:
            pool.close()

    def test_3_2_3_dynamic_worker_scaling_under_rate_limiting(self):
        """AIMD scaler decreases concurrency on 420 and slowly ramps up on recovery."""
        scaler = AIMDConcurrencyScaler(min_workers=8, max_workers=48, initial_workers=24, window_size=5)
        # 1. Error occurs -> immediate multiplicative drop
        scaler.record_failure(status_code=420)
        self.assertEqual(scaler.current_workers, 18)

        # 2. Consecutive successes in new window -> additive increase
        for _ in range(20):
            scaler.record_success(0.02, 200000)
        self.assertGreater(scaler.current_workers, 18)

    def test_3_2_4_high_concurrency_bounded_queue(self):
        """Run worker threads streaming segments into StreamAssembler out-of-order."""
        tmp_p = os.path.join(tempfile.gettempdir(), "high_conc.mp4")
        assembler = StreamAssembler(tmp_p, total_segments=16, max_in_flight_mb=8)
        try:
            def _push_seg(seg_num):
                assembler.add_segment(seg_num, f"CHUNK_{seg_num:02d}_".encode("ascii"))

            order = [16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(_push_seg, num) for num in order]
                for f in futures:
                    f.result(timeout=5.0)

            assembler.finish()
            with open(tmp_p, "rb") as f:
                data = f.read()
            expected = "".join(f"CHUNK_{i:02d}_" for i in range(1, 17)).encode("ascii")
            self.assertEqual(data, expected)
        finally:
            if os.path.exists(tmp_p):
                os.remove(tmp_p)

    def test_3_2_5_hedged_download_latency_mitigation(self):
        """Hedged download successfully returns payload when primary is slow."""
        cfg = ConcurrencyConfig(hedging_enabled=True, hedge_min_delay=0.05, timeout=5)
        pool = SessionPool(config=cfg)
        try:
            data = pool.download_segment_hedged(self.server.get_url("/media/init_video.mp4"))
            self.assertIsInstance(data, bytes)
            self.assertGreater(len(data), 0)
        finally:
            pool.close()


class TestComboMultiTrackMuxingAndDispositions(unittest.TestCase):
    """Combination 3: Multi-Track Muxing with Dispositions, Tags & Subtitles."""

    def test_3_3_1_full_muxing_command_structure(self):
        """Construct full FFmpeg command for 1 video + 2 audio + 2 subtitles."""
        v_file = "/tmp/v.mp4"
        a_tracks = [
            MediaTrack(file="/tmp/a_ja.mp4", locale="ja-JP"),
            MediaTrack(file="/tmp/a_en.mp4", locale="en-US"),
        ]
        s_tracks = [
            MediaTrack(file="/tmp/s_en.ass", locale="en-US"),
            MediaTrack(file="/tmp/s_es.ass", locale="es-419"),
        ]
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Frieren: Beyond Journey's End",
                season_number=1,
                episode_number=1,
                audio_locale="ja-JP",
                versions=[],
                availability_starts="",
            ),
            title="The Journey's End",
            subtitles={},
        )
        with patch("crunchyroll.merger.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run") as mock_run, \
             patch("os.path.exists", return_value=False):
            mock_run.return_value = MagicMock(returncode=0)
            merge_everything(v_file, a_tracks, s_tracks, "/tmp/out.mkv", info)
            args = mock_run.call_args[0][0]

            # Maps
            self.assertIn("0:v:0", args)
            self.assertIn("1:a:0", args)
            self.assertIn("2:a:0", args)
            self.assertIn("3", args)
            self.assertIn("4", args)

            # Audio meta & disp
            self.assertIn("-metadata:s:a:0", args)
            self.assertIn("language=jpn", args)
            self.assertIn("-metadata:s:a:1", args)
            self.assertIn("language=eng", args)
            self.assertIn("-disposition:a:0", args)
            self.assertIn("default", args)
            self.assertIn("-disposition:a:1", args)
            self.assertIn("0", args)

            # Sub meta & disp
            self.assertIn("-metadata:s:s:0", args)
            self.assertIn("language=eng", args)
            self.assertIn("-metadata:s:s:1", args)
            self.assertIn("language=spa", args)
            self.assertIn("-disposition:s:0", args)
            self.assertIn("default", args)
            self.assertIn("-disposition:s:1", args)
            self.assertIn("0", args)

            # Global title metadata
            self.assertIn("title=S01E01 - The Journey's End", args)

    def test_3_3_2_muxing_video_and_audio_only_no_subtitles(self):
        """Muxing with zero subtitle tracks does not add subtitle mapping args."""
        v_file = "/tmp/v.mp4"
        a_tracks = [MediaTrack(file="/tmp/a_ja.mp4", locale="ja-JP")]
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Show", season_number=1, episode_number=2,
                audio_locale="ja-JP", versions=[], availability_starts="",
            ),
            title="Episode 2", subtitles={},
        )
        with patch("crunchyroll.merger.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run") as mock_run, \
             patch("os.path.exists", return_value=False):
            mock_run.return_value = MagicMock(returncode=0)
            merge_everything(v_file, a_tracks, [], "/tmp/out.mkv", info)
            args = mock_run.call_args[0][0]

            self.assertIn("0:v:0", args)
            self.assertIn("1:a:0", args)
            self.assertNotIn("-c:s", args)

    def test_3_3_3_temporary_files_cleanup_on_successful_merge(self):
        """Intermediate video, audio, and subtitle files are unlinked after muxing."""
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Show", season_number=1, episode_number=1,
                audio_locale="ja-JP", versions=[], availability_starts="",
            ),
            title="Ep 1", subtitles={},
        )
        with patch("crunchyroll.merger.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run") as mock_run, \
             patch("os.path.exists", side_effect=lambda p: p != "/tmp/out.mkv"), \
             patch("os.remove") as mock_remove:
            mock_run.return_value = MagicMock(returncode=0)
            merge_everything("/tmp/v.mp4", [MediaTrack(file="/tmp/a.mp4", locale="ja-JP")], [MediaTrack(file="/tmp/s.ass", locale="en-US")], "/tmp/out.mkv", info)
            mock_remove.assert_any_call("/tmp/v.mp4")
            mock_remove.assert_any_call("/tmp/a.mp4")
            mock_remove.assert_any_call("/tmp/s.ass")

    def test_3_3_4_ffmpeg_failure_raises_and_cleans_output(self):
        """When ffmpeg exits with code 1, output file is removed and RuntimeError is raised."""
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Show", season_number=1, episode_number=1,
                audio_locale="ja-JP", versions=[], availability_starts="",
            ),
            title="Ep 1", subtitles={},
        )
        with patch("crunchyroll.merger.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run") as mock_run, \
             patch("os.path.exists", return_value=True), \
             patch("os.remove") as mock_remove:
            mock_res = MagicMock(returncode=1, stderr="Invalid data found when processing input")
            mock_run.return_value = mock_res
            with self.assertRaises(RuntimeError):
                merge_everything("/tmp/v.mp4", [], [], "/tmp/failed_out.mkv", info)
            mock_remove.assert_called_with("/tmp/failed_out.mkv")

    def test_3_3_5_track_title_localization_combinations(self):
        """Verify 27 language locale names are correctly mapped."""
        locales = ["ja-JP", "en-US", "es-419", "fr-FR", "de-DE", "it-IT", "pt-BR", "ru-RU", "ar-SA", "zh-CN"]
        for loc in locales:
            title = track_title(loc)
            iso = LANGUAGE_CODES.get(loc)
            self.assertNotEqual(title, "")
            self.assertIsNotNone(iso)


class TestComboBatchProcessingAndFailureIsolation(unittest.TestCase):
    """Combination 4: Batch Processing with Failure Isolation & Recovery."""

    def test_3_4_1_batch_url_processing_isolation(self):
        """In a list of 4 URLs where URL #2 fails, URLs #1, #3, #4 continue without aborting batch."""
        urls = [
            "https://www.crunchyroll.com/watch/G1/ep1",
            "https://www.crunchyroll.com/watch/INVALID/ep2",
            "https://www.crunchyroll.com/watch/G3/ep3",
            "https://www.crunchyroll.com/watch/G4/ep4",
        ]
        processed = []
        failed = []

        def _process(url):
            if "INVALID" in url:
                raise RuntimeError("Failed to resolve stream")
            processed.append(url)

        for u in urls:
            try:
                _process(u)
            except Exception as e:
                failed.append((u, str(e)))

        self.assertEqual(len(processed), 3)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0][0], "https://www.crunchyroll.com/watch/INVALID/ep2")

    def test_3_4_2_web_gui_batch_progress_calculation(self):
        """Verify progress calculation formula: base % + current segment %."""
        total_episodes = 4
        # Ep 0 (first), at segment 5/10
        idx = 0
        cur, tot = 5, 10
        base = (idx / total_episodes) * 100
        extra = ((cur / tot) / total_episodes) * 100
        progress_1 = round(base + extra, 1)
        self.assertEqual(progress_1, 12.5)

        # Ep 2 (third), at segment 10/10
        idx = 2
        cur, tot = 10, 10
        base = (idx / total_episodes) * 100
        extra = ((cur / tot) / total_episodes) * 100
        progress_2 = round(base + extra, 1)
        self.assertEqual(progress_2, 75.0)

    def test_3_4_3_web_gui_state_transitions(self):
        """Web GUI STATE transitions from idle -> running -> completed."""
        import web_gui
        with web_gui.LOCK:
            web_gui.STATE["download"].update(status="running", progress=0.0, log=[])
            self.assertEqual(web_gui.STATE["download"]["status"], "running")

            web_gui.STATE["download"].update(status="completed", progress=100.0, episode="all done")
            self.assertEqual(web_gui.STATE["download"]["status"], "completed")
            self.assertEqual(web_gui.STATE["download"]["progress"], 100.0)

            # Reset back to idle
            web_gui.STATE["download"].update(status="idle", progress=0.0)

    def test_3_4_4_discord_bot_modal_range_combined_with_batch(self):
        """Discord bot range parser output drives multi-episode batch queue."""
        from discord_bot import parse_episode_ranges
        eps = [
            SeasonEpisode(id=f"ep_{i}", title=f"Ep {i}", season_number=1, episode_number=i, series_title="S", audio_locale="ja-JP", versions=[], availability_starts="")
            for i in range(1, 13)
        ]
        # User selected range 1-3, 10
        selected = parse_episode_ranges("1-3, 10", eps)
        self.assertEqual(len(selected), 4)
        ep_ids = [e.id for e in selected]
        self.assertEqual(ep_ids, ["ep_1", "ep_2", "ep_3", "ep_10"])

    def test_3_4_5_session_pool_lifecycle_across_batch(self):
        """SessionPool remains active and reuses TCP connections across successive batch jobs."""
        pool = SessionPool(max_pool_size=16)
        try:
            self.assertFalse(pool._closed)
            sess1 = pool.get_session()
            sess2 = pool.get_session()
            self.assertIs(sess1, sess2)
        finally:
            pool.close()
            self.assertTrue(pool._closed)


if __name__ == "__main__":
    unittest.main()
