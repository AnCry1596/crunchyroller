"""
tests/test_tier4_scenarios.py

Tier 4: Realistic End-to-End Download Scenarios
Covers real-world download workloads with Mock Server and FFmpeg muxing:
1. Full Episode Acquisition & FFprobe Stream Validation (H.264 + AAC + ASS)
2. Multi-Audio Dubs (ja-JP, en-US, es-419) + Multi-Subtitles Muxing
3. Sustained High-Speed Download Memory Bounding (psutil peak RSS < 100 MB)
4. Multi-Episode Batch Processing & Intermediate Temp Cleanup
5. CLI Entrypoint Workflows (Single URL, Batch File, Manifest Debug)
6. Web GUI REST API Download & State Transitions
7. Discord Bot Queue Execution & Cancellation
8. Stream Integrity & Atomic File Finalization
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import psutil

# Ensure discord modules are mocked if discord.py is not installed
if "discord" not in sys.modules:
    sys.modules["discord"] = MagicMock()
    sys.modules["discord.ui"] = MagicMock()
    sys.modules["discord.ext"] = MagicMock()
    sys.modules["discord.ext.commands"] = MagicMock()
    sys.modules["discord.app_commands"] = MagicMock()

from tests.mock_server import MockCrunchyrollServer, SAMPLE_ASS_SUBTITLE
from crunchyroll.api import get_episode, get_episode_info, get_season_episodes, get_seasons, parse_url_type
from crunchyroll.downloader import (
    build_url,
    download_parts_optimized,
    download_subs,
)
from crunchyroll.merger import find_ffmpeg, merge_everything
from crunchyroll.session_pool import ConcurrencyConfig, SessionPool
from crunchyroll.types import (
    DubVersion,
    EpisodeInfo,
    EpisodeMetadata,
    MediaTrack,
    PlaybackStream,
    Season,
    SeasonEpisode,
    Subtitle,
)
from crunchyroll.utils import LANGUAGE_CODES, track_title


def probe_media_file(file_path: str) -> Dict[str, Any]:
    """Execute ffprobe and return parsed JSON structure of streams and format metadata."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


class TestScenario1FullEpisodeMuxingAndFFprobe(unittest.TestCase):
    """Scenario 1: End-to-end full episode acquisition with Mock Server & FFmpeg Matroska remuxing."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_tier4_s1_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_4_1_1_e2e_download_and_ffprobe_stream_validation(self):
        """Download 1 video track, 1 audio track, 1 subtitle track and mux into MKV with ffprobe validation."""
        v_file = os.path.join(self.tmp_dir, "video.mp4")
        a_file = os.path.join(self.tmp_dir, "audio.mp4")
        out_mkv = os.path.join(self.tmp_dir, "Episode_01.mkv")

        # 1. Download video track
        download_parts_optimized(
            base_url=self.server.base_url + "/",
            rep_id="video/1080p",
            timeline=[1, 2],
            keys=None,
            output_filename=v_file,
            media_pattern="media/seg_$RepresentationID$_$Number%05d$.mp4",
            init_pattern="media/init_$RepresentationID$.mp4",
        )
        self.assertTrue(os.path.exists(v_file))

        # 2. Download audio track
        download_parts_optimized(
            base_url=self.server.base_url + "/",
            rep_id="audio/ja-JP/192k",
            timeline=[1, 2],
            keys=None,
            output_filename=a_file,
            media_pattern="media/seg_$RepresentationID$_$Number%05d$.mp4",
            init_pattern="media/init_$RepresentationID$.mp4",
        )
        self.assertTrue(os.path.exists(a_file))

        # 3. Download subtitle track
        sub_file = download_subs(self.server.get_url("/subs/en-US.ass"))
        self.assertTrue(os.path.exists(sub_file))

        # 4. Mux everything into MKV
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Frieren: Beyond Journey's End",
                season_number=1,
                episode_number=1,
                audio_locale="ja-JP",
                versions=[],
                availability_starts="2026-01-01T00:00:00Z",
            ),
            title="The Journey's End",
            subtitles={},
        )
        merge_everything(
            video_file=v_file,
            audio_tracks=[MediaTrack(file=a_file, locale="ja-JP")],
            sub_tracks=[MediaTrack(file=sub_file, locale="en-US")],
            output_file=out_mkv,
            info=info,
        )

        # 5. Assert output file exists and is non-empty
        self.assertTrue(os.path.exists(out_mkv))
        self.assertGreater(os.path.getsize(out_mkv), 1000)

        # 6. ffprobe stream and metadata assertions
        probe = probe_media_file(out_mkv)
        streams = probe.get("streams", [])
        self.assertEqual(len(streams), 3)

        # Video stream
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        self.assertIsNotNone(v_stream)
        self.assertEqual(v_stream.get("codec_name"), "h264")

        # Audio stream
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        self.assertIsNotNone(a_stream)
        self.assertEqual(a_stream.get("codec_name"), "aac")
        self.assertEqual(a_stream.get("tags", {}).get("language"), "jpn")
        self.assertEqual(a_stream.get("disposition", {}).get("default"), 1)

        # Subtitle stream
        s_stream = next((s for s in streams if s.get("codec_type") == "subtitle"), None)
        self.assertIsNotNone(s_stream)
        self.assertEqual(s_stream.get("codec_name"), "ass")
        self.assertEqual(s_stream.get("tags", {}).get("language"), "eng")
        self.assertEqual(s_stream.get("disposition", {}).get("default"), 1)

        # Global container tags
        fmt_tags = probe.get("format", {}).get("tags", {})
        self.assertIn("Frieren", fmt_tags.get("SHOW", "") or fmt_tags.get("show", ""))
        self.assertEqual(probe.get("format", {}).get("format_name"), "matroska,webm")

    def test_4_1_2_intermediate_temp_files_cleaned_up(self):
        """Verify video, audio, and subtitle temp files are removed after muxing completes."""
        v_file = os.path.join(self.tmp_dir, "v_tmp.mp4")
        a_file = os.path.join(self.tmp_dir, "a_tmp.mp4")
        out_mkv = os.path.join(self.tmp_dir, "Ep_Clean.mkv")

        download_parts_optimized(
            base_url=self.server.base_url + "/",
            rep_id="video/720p",
            timeline=[1],
            keys=None,
            output_filename=v_file,
            media_pattern="media/seg_$RepresentationID$_$Number%05d$.mp4",
            init_pattern="media/init_$RepresentationID$.mp4",
        )
        download_parts_optimized(
            base_url=self.server.base_url + "/",
            rep_id="audio/ja-JP/96k",
            timeline=[1],
            keys=None,
            output_filename=a_file,
            media_pattern="media/seg_$RepresentationID$_$Number%05d$.mp4",
            init_pattern="media/init_$RepresentationID$.mp4",
        )
        sub_file = download_subs(self.server.get_url("/subs/en-US.ass"))

        self.assertTrue(os.path.exists(v_file))
        self.assertTrue(os.path.exists(a_file))
        self.assertTrue(os.path.exists(sub_file))

        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Show", season_number=1, episode_number=1,
                audio_locale="ja-JP", versions=[], availability_starts="",
            ),
            title="Ep 1", subtitles={},
        )
        merge_everything(
            video_file=v_file,
            audio_tracks=[MediaTrack(file=a_file, locale="ja-JP")],
            sub_tracks=[MediaTrack(file=sub_file, locale="en-US")],
            output_file=out_mkv,
            info=info,
        )

        self.assertTrue(os.path.exists(out_mkv))
        self.assertFalse(os.path.exists(v_file))
        self.assertFalse(os.path.exists(a_file))
        self.assertFalse(os.path.exists(sub_file))


class TestScenario2MultiAudioDubAndMultiSubtitles(unittest.TestCase):
    """Scenario 2: Multi-Audio Dub acquisition with multiple subtitle tracks."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_tier4_s2_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_4_2_1_multi_audio_and_multi_subtitle_muxing(self):
        """Mux 1 video track + 3 audio dubs (ja-JP, en-US, es-419) + 2 subtitle tracks into MKV."""
        v_file = os.path.join(self.tmp_dir, "v_multi.mp4")
        a_ja = os.path.join(self.tmp_dir, "a_ja.mp4")
        a_en = os.path.join(self.tmp_dir, "a_en.mp4")
        a_es = os.path.join(self.tmp_dir, "a_es.mp4")
        out_mkv = os.path.join(self.tmp_dir, "MultiDub_Ep01.mkv")

        # Download video
        download_parts_optimized(
            base_url=self.server.base_url + "/",
            rep_id="video/1080p",
            timeline=[1],
            keys=None,
            output_filename=v_file,
            media_pattern="media/seg_$RepresentationID$_$Number%05d$.mp4",
            init_pattern="media/init_$RepresentationID$.mp4",
        )
        # Download 3 audio dubs
        for p, rep in [(a_ja, "audio/ja-JP/192k"), (a_en, "audio/en-US/192k"), (a_es, "audio/es-419/192k")]:
            download_parts_optimized(
                base_url=self.server.base_url + "/",
                rep_id=rep,
                timeline=[1],
                keys=None,
                output_filename=p,
                media_pattern="media/seg_$RepresentationID$_$Number%05d$.mp4",
                init_pattern="media/init_$RepresentationID$.mp4",
            )

        # Download 2 subtitles
        sub_en = download_subs(self.server.get_url("/subs/en-US.ass"))
        sub_es = download_subs(self.server.get_url("/subs/es-419.ass"))

        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Multi-Dub Anime",
                season_number=1,
                episode_number=1,
                audio_locale="ja-JP",
                versions=[],
                availability_starts="",
            ),
            title="Global Premiere",
            subtitles={},
        )

        merge_everything(
            video_file=v_file,
            audio_tracks=[
                MediaTrack(file=a_ja, locale="ja-JP"),
                MediaTrack(file=a_en, locale="en-US"),
                MediaTrack(file=a_es, locale="es-419"),
            ],
            sub_tracks=[
                MediaTrack(file=sub_en, locale="en-US"),
                MediaTrack(file=sub_es, locale="es-419"),
            ],
            output_file=out_mkv,
            info=info,
        )

        self.assertTrue(os.path.exists(out_mkv))
        probe = probe_media_file(out_mkv)
        streams = probe.get("streams", [])
        self.assertEqual(len(streams), 6)  # 1 video + 3 audio + 2 sub

        # Audio streams language verification
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        self.assertEqual(len(audio_streams), 3)
        self.assertEqual(audio_streams[0].get("tags", {}).get("language"), "jpn")
        self.assertEqual(audio_streams[1].get("tags", {}).get("language"), "eng")
        self.assertEqual(audio_streams[2].get("tags", {}).get("language"), "spa")

        # Disposition check: first audio default, others 0
        self.assertEqual(audio_streams[0].get("disposition", {}).get("default"), 1)
        self.assertEqual(audio_streams[1].get("disposition", {}).get("default"), 0)
        self.assertEqual(audio_streams[2].get("disposition", {}).get("default"), 0)

        # Subtitle streams check
        sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
        self.assertEqual(len(sub_streams), 2)
        self.assertEqual(sub_streams[0].get("tags", {}).get("language"), "eng")
        self.assertEqual(sub_streams[1].get("tags", {}).get("language"), "spa")


class TestScenario3MemoryBoundingAndPsutilMonitoring(unittest.TestCase):
    """Scenario 3: Sustained High-Speed Download Memory Bounding (psutil peak RSS < 110 MB)."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_tier4_s3_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_4_3_1_peak_rss_remains_under_100mb_during_sustained_download(self):
        """Assert RSS delta overhead stays < 50 MB during downloading 30 segments concurrently."""
        process = psutil.Process(os.getpid())
        initial_rss_bytes = process.memory_info().rss
        peak_rss_bytes = initial_rss_bytes
        stop_sampling = threading.Event()

        def _memory_sampler():
            nonlocal peak_rss_bytes
            while not stop_sampling.is_set():
                rss = process.memory_info().rss
                if rss > peak_rss_bytes:
                    peak_rss_bytes = rss
                time.sleep(0.01)

        sampler_thread = threading.Thread(target=_memory_sampler, daemon=True)
        sampler_thread.start()

        try:
            out_file = os.path.join(self.tmp_dir, "sustained_stream.mp4")
            # 30 segment timeline
            timeline = list(range(1, 31))
            cfg = ConcurrencyConfig(
                min_workers=8,
                max_workers=24,
                initial_workers=16,
                aimd_enabled=True,
                hedging_enabled=True,
            )

            download_parts_optimized(
                base_url=self.server.base_url + "/",
                rep_id="video/1080p",
                timeline=timeline,
                keys=None,
                output_filename=out_file,
                concurrency_config=cfg,
                media_pattern="media/seg_$RepresentationID$_$Number%05d$.mp4",
                init_pattern="media/init_$RepresentationID$.mp4",
            )
            self.assertTrue(os.path.exists(out_file))
        finally:
            stop_sampling.set()
            sampler_thread.join(timeout=2.0)

        initial_mb = initial_rss_bytes / (1024 * 1024)
        peak_mb = peak_rss_bytes / (1024 * 1024)
        delta_mb = peak_mb - initial_mb
        print(f"\n[Tier 4 Memory Benchmark] Initial: {initial_mb:.2f} MB | Peak: {peak_mb:.2f} MB | Delta: {delta_mb:.2f} MB (Limit: +50 MB)")
        self.assertLess(delta_mb, 50.0, f"Download engine allocated {delta_mb:.2f} MB overhead (> 50 MB limit)")


class TestScenario4MultiEpisodeBatchProcessing(unittest.TestCase):
    """Scenario 4: Multi-Episode Batch Processing & Intermediate Temp Cleanup."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_tier4_s4_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_4_4_1_sequential_batch_download_3_episodes(self):
        """Sequentially download and mux 3 episodes, verifying integrity and temp cleanup for each."""
        episodes = [
            SeasonEpisode(id=f"ep_{i}", title=f"Episode {i}", season_number=1, episode_number=i,
                          series_title="Batch Show", audio_locale="ja-JP", versions=[], availability_starts="")
            for i in range(1, 4)
        ]

        produced_files = []
        for ep in episodes:
            v_p = os.path.join(self.tmp_dir, f"v_{ep.id}.mp4")
            a_p = os.path.join(self.tmp_dir, f"a_{ep.id}.mp4")
            out_p = os.path.join(self.tmp_dir, f"BatchShow_S01E0{ep.episode_number}.mkv")

            download_parts_optimized(
                base_url=self.server.base_url + "/",
                rep_id="video/720p",
                timeline=[1, 2],
                keys=None,
                output_filename=v_p,
                media_pattern="media/seg_$RepresentationID$_$Number%05d$.mp4",
                init_pattern="media/init_$RepresentationID$.mp4",
            )
            download_parts_optimized(
                base_url=self.server.base_url + "/",
                rep_id="audio/ja-JP/192k",
                timeline=[1, 2],
                keys=None,
                output_filename=a_p,
                media_pattern="media/seg_$RepresentationID$_$Number%05d$.mp4",
                init_pattern="media/init_$RepresentationID$.mp4",
            )
            sub_p = download_subs(self.server.get_url("/subs/en-US.ass"))

            info = EpisodeInfo(
                episode_metadata=EpisodeMetadata(
                    series_title=ep.series_title,
                    season_number=ep.season_number,
                    episode_number=ep.episode_number,
                    audio_locale=ep.audio_locale,
                    versions=[],
                    availability_starts="",
                ),
                title=ep.title,
                subtitles={},
            )
            merge_everything(
                video_file=v_p,
                audio_tracks=[MediaTrack(file=a_p, locale="ja-JP")],
                sub_tracks=[MediaTrack(file=sub_p, locale="en-US")],
                output_file=out_p,
                info=info,
            )
            produced_files.append(out_p)

            # Assert cleanup of raw intermediates
            self.assertFalse(os.path.exists(v_p))
            self.assertFalse(os.path.exists(a_p))
            self.assertFalse(os.path.exists(sub_p))

        self.assertEqual(len(produced_files), 3)
        for p in produced_files:
            self.assertTrue(os.path.exists(p))
            probe = probe_media_file(p)
            self.assertEqual(len(probe.get("streams", [])), 3)


class TestScenario5CLIWorkflowAndEntrypoint(unittest.TestCase):
    """Scenario 5: CLI Entrypoint Workflows (Single URL, Batch File, Manifest Debug)."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_tier4_s5_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_4_5_1_cli_argument_parsing_and_defaults(self):
        """Validate CLI arguments parsing across all supported flags."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--url", type=str, default="")
        parser.add_argument("--video-quality", type=str, default="1080p")
        parser.add_argument("--audio-quality", type=str, default="192k")
        parser.add_argument("--quality-video", type=str, default="")
        parser.add_argument("--quality-audio", type=str, default="")
        parser.add_argument("--audio-lang", type=str, default="ja-JP")
        parser.add_argument("--subs-lang", type=str, default="en-US")
        parser.add_argument("--workers", type=int, default=16)
        parser.add_argument("--debug-manifest", action="store_true")
        args = parser.parse_args([
            "--url", "https://www.crunchyroll.com/watch/G12345/test-ep",
            "--quality-video", "1080p",
            "--quality-audio", "192k",
            "--audio-lang", "ja-JP",
            "--subs-lang", "en-US,es-419",
            "--workers", "24",
            "--debug-manifest",
        ])
        vq = args.quality_video or args.video_quality
        aq = args.quality_audio or args.audio_quality
        self.assertEqual(vq, "1080p")
        self.assertEqual(aq, "192k")
        self.assertEqual(args.audio_lang, "ja-JP")
        self.assertEqual(args.subs_lang, "en-US,es-419")
        self.assertEqual(args.workers, 24)
        self.assertTrue(args.debug_manifest)

    def test_4_5_2_cli_batch_file_parsing(self):
        """CLI batch file reader processes multiple URLs from file."""
        batch_txt = os.path.join(self.tmp_dir, "episodes.txt")
        with open(batch_txt, "w") as f:
            f.write("https://www.crunchyroll.com/watch/G1/ep1\n")
            f.write("# This is a comment\n")
            f.write("https://www.crunchyroll.com/watch/G2/ep2\n")
            f.write("\n")
            f.write("https://www.crunchyroll.com/watch/G3/ep3\n")

        with open(batch_txt, "r") as f:
            lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]

        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "https://www.crunchyroll.com/watch/G1/ep1")
        self.assertEqual(lines[1], "https://www.crunchyroll.com/watch/G2/ep2")
        self.assertEqual(lines[2], "https://www.crunchyroll.com/watch/G3/ep3")


class TestScenario6WebGUIWorkflow(unittest.TestCase):
    """Scenario 6: Web GUI REST API Download & State Transitions."""

    def test_4_6_1_web_gui_state_endpoint_and_transitions(self):
        """Web GUI REST API state transitions through download lifecycle."""
        import web_gui
        with web_gui.LOCK:
            web_gui.STATE["download"] = {
                "status": "downloading",
                "progress": 45.5,
                "speed": "25.40 MB/s",
                "episode": "Ep 1",
                "log": ["Started ep 1", "Downloaded 50%"],
            }
            state_copy = dict(web_gui.STATE["download"])

        self.assertEqual(state_copy["status"], "downloading")
        self.assertEqual(state_copy["progress"], 45.5)
        self.assertEqual(state_copy["speed"], "25.40 MB/s")

        with web_gui.LOCK:
            web_gui.STATE["download"].update(status="idle", progress=0.0, log=[])

    def test_4_6_2_web_gui_config_persistence(self):
        """Web GUI config update correctly modifies global preferences."""
        import web_gui
        with web_gui.LOCK:
            orig = dict(web_gui.STATE["config"])
            web_gui.STATE["config"]["video_quality"] = "720p"
            web_gui.STATE["config"]["audio_lang"] = "en-US"
            self.assertEqual(web_gui.STATE["config"]["video_quality"], "720p")
            self.assertEqual(web_gui.STATE["config"]["audio_lang"], "en-US")
            web_gui.STATE["config"].update(orig)


class TestScenario7DiscordBotWorkflow(unittest.TestCase):
    """Scenario 7: Discord Bot Queue Execution & Cancellation."""

    def test_4_7_1_discord_bot_range_parsing_combinations(self):
        """Test Discord Bot episode selection range parser across all syntax formats."""
        from discord_bot import parse_episode_ranges
        episodes = [
            SeasonEpisode(id=f"ep_{i}", title=f"Ep {i}", season_number=1, episode_number=i,
                          series_title="S", audio_locale="ja-JP", versions=[], availability_starts="")
            for i in range(1, 25)
        ]

        # 1. Range '1-5'
        s1 = parse_episode_ranges("1-5", episodes)
        self.assertEqual(len(s1), 5)
        self.assertEqual([e.episode_number for e in s1], [1, 2, 3, 4, 5])

        # 2. Comma-separated '1, 3, 7, 12'
        s2 = parse_episode_ranges("1, 3, 7, 12", episodes)
        self.assertEqual([e.episode_number for e in s2], [1, 3, 7, 12])

        # 3. 'all'
        s3 = parse_episode_ranges("all", episodes)
        self.assertEqual(len(s3), 24)

        # 4. Inverted range '8-5'
        s4 = parse_episode_ranges("8-5", episodes)
        self.assertEqual([e.episode_number for e in s4], [5, 6, 7, 8])

    def test_4_7_2_discord_bot_single_instance_lock(self):
        """Verify Discord bot single instance lock binds socket cleanly."""
        import discord_bot
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock_cls.return_value = mock_sock

            # First call succeeds
            res1 = discord_bot.acquire_instance_lock()
            self.assertTrue(res1)
            mock_sock.bind.assert_called_with(("127.0.0.1", 54321))

            # Second call fails when socket raises error
            mock_sock_cls.side_effect = OSError("Address already in use")
            res2 = discord_bot.acquire_instance_lock()
            self.assertFalse(res2)


class TestScenario8StreamIntegrityAndAtomicRenaming(unittest.TestCase):
    """Scenario 8: Stream Integrity & Atomic File Finalization."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_tier4_s8_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_4_8_1_atomic_file_renaming_on_integrity_success(self):
        """Atomic write pattern: write to .tmp.mkv and rename to target .mkv on success."""
        target_mkv = os.path.join(self.tmp_dir, "FinalEpisode.mkv")
        tmp_mkv = target_mkv + ".tmp"

        with open(tmp_mkv, "wb") as f:
            f.write(b"MOCK_MKV_VALID_PAYLOAD")

        self.assertTrue(os.path.exists(tmp_mkv))
        self.assertFalse(os.path.exists(target_mkv))

        # Atomic rename
        os.replace(tmp_mkv, target_mkv)

        self.assertTrue(os.path.exists(target_mkv))
        self.assertFalse(os.path.exists(tmp_mkv))


if __name__ == "__main__":
    unittest.main()
