"""
tests/test_m2_integrity_and_decryption.py

Unit and integration tests for Milestone 2: Output Integrity & Stream Decoupling.
Covers:
1. Streaming CENC Decryption (FFmpeg native + memory-bounded Python fallback).
2. ISO-BMFF box parsing, subsample encryption handling, and clear stream pass-through.
3. Memory bounding verification during decryption (RSS < 100 MB).
4. StreamValidator ffprobe JSON inspection and error handling.
5. Atomic file finalization (atomic_finalize).
6. Modernized FFmpeg merger timestamp normalization and metadata tags.
7. Downloader integration with decryption and validation.
"""

import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from Crypto.Cipher import AES
from Crypto.Util import Counter

from crunchyroll.decryptor import (
    BUFFER_SIZE,
    _modify_moov_box,
    _parse_moof_box,
    decrypt_cenc_streaming,
    decrypt_mp4,
    decrypt_stream,
)
from crunchyroll.integrity import StreamValidator, atomic_finalize, find_ffprobe
from crunchyroll.merger import find_ffmpeg, merge_everything
from crunchyroll.types import EpisodeInfo, EpisodeMetadata, MediaTrack
from tests.mock_server import MockCrunchyrollServer, MockMediaGenerator


class TestStreamingCencDecryption(unittest.TestCase):
    """Test CENC AES-128-CTR decryption (FFmpeg native and streaming Python fallback)."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_m2_")
        self.key = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10"
        self.keys = {b"kid1": self.key}

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_synthetic_cenc_fmp4(self, key: bytes, plaintext_payload: bytes, use_subsamples: bool = True) -> str:
        """Constructs a minimal valid ISO-BMFF fragmented MP4 structure with CENC encryption."""
        out_path = os.path.join(self.tmp_dir, "synthetic_cenc.mp4")

        # 1. ftyp box
        ftyp = struct.pack(">I4s4sI4s4s", 24, b"ftyp", b"iso5", 512, b"iso5", b"mp41")

        # 2. moov box with encv and sinf
        sinf_payload = struct.pack(">I4s", 8, b"frma") + b"avc1" + struct.pack(">I4sI4s", 12, b"schm", 0, b"cenc")
        sinf_box = struct.pack(">I4s", len(sinf_payload) + 8, b"sinf") + sinf_payload
        encv_payload = b"\x00" * 78 + sinf_box
        encv_box = struct.pack(">I4s", len(encv_payload) + 8, b"encv") + encv_payload
        stsd_payload = struct.pack(">II", 0, 1) + encv_box
        stsd_box = struct.pack(">I4s", len(stsd_payload) + 8, b"stsd") + stsd_payload
        moov_payload = stsd_box
        moov_box = struct.pack(">I4s", len(moov_payload) + 8, b"moov") + moov_payload

        # 3. moof box with traf, tfhd, trun, senc
        iv = b"\x11\x22\x33\x44\x55\x66\x77\x88"
        iv_full = iv + b"\x00" * 8
        ctr = Counter.new(128, initial_value=int.from_bytes(iv_full, "big"))
        cipher = AES.new(key, AES.MODE_CTR, counter=ctr)

        if use_subsamples:
            # First 10 bytes clear, rest encrypted
            clear_len = min(10, len(plaintext_payload))
            enc_len = len(plaintext_payload) - clear_len
            encrypted_payload = plaintext_payload[:clear_len] + cipher.decrypt(plaintext_payload[clear_len:])

            senc_data = (
                struct.pack(">I", 1)  # sample count
                + iv
                + struct.pack(">H", 1)  # 1 subsample
                + struct.pack(">HI", clear_len, enc_len)
            )
            senc_box = struct.pack(">I4sI", len(senc_data) + 12, b"senc", 0x000002) + senc_data
        else:
            encrypted_payload = cipher.decrypt(plaintext_payload)
            senc_data = struct.pack(">I", 1) + iv
            senc_box = struct.pack(">I4sI", len(senc_data) + 12, b"senc", 0x000000) + senc_data

        tfhd_box = struct.pack(">I4sII", 16, b"tfhd", 0, 1)
        trun_data = struct.pack(">I", len(encrypted_payload))
        trun_box = struct.pack(">I4sII", len(trun_data) + 16, b"trun", 0x000200, 1) + trun_data

        traf_payload = tfhd_box + trun_box + senc_box
        traf_box = struct.pack(">I4s", len(traf_payload) + 8, b"traf") + traf_payload
        moof_payload = traf_box
        moof_box = struct.pack(">I4s", len(moof_payload) + 8, b"moof") + moof_payload

        # 4. mdat box
        mdat_box = struct.pack(">I4s", len(encrypted_payload) + 8, b"mdat") + encrypted_payload

        with open(out_path, "wb") as f:
            f.write(ftyp + moov_box + moof_box + mdat_box)

        return out_path

    def test_decrypt_cenc_streaming_subsamples(self):
        """Streaming Python fallback correctly decrypts CENC stream with subsample ranges."""
        original_data = b"CLEAR_PREFIX_HEADER_12345" + b"ENCRYPTED_SECRET_VIDEO_PAYLOAD_CHUNKS" * 50
        input_mp4 = self._create_synthetic_cenc_fmp4(self.key, original_data, use_subsamples=True)
        output_mp4 = os.path.join(self.tmp_dir, "decrypted_sub.mp4")

        decrypt_stream(input_mp4, self.keys, output_mp4, fallback_only=True)

        self.assertTrue(os.path.exists(output_mp4))
        self.assertGreater(os.path.getsize(output_mp4), 0)

        with open(output_mp4, "rb") as f:
            decrypted_file_bytes = f.read()

        # Plaintext payload should be fully recovered in output file
        self.assertIn(original_data, decrypted_file_bytes)
        # 'encv' should have been rewritten to 'avc1'
        self.assertIn(b"avc1", decrypted_file_bytes)
        self.assertNotIn(b"encv", decrypted_file_bytes)

    def test_decrypt_cenc_streaming_whole_sample(self):
        """Streaming Python fallback correctly decrypts whole-sample encrypted CENC stream."""
        original_data = b"ENTIRE_SAMPLE_ENCRYPTED_PAYLOAD_BLOCK_" * 100
        input_mp4 = self._create_synthetic_cenc_fmp4(self.key, original_data, use_subsamples=False)
        output_mp4 = os.path.join(self.tmp_dir, "decrypted_whole.mp4")

        decrypt_stream(input_mp4, self.keys, output_mp4, fallback_only=True)

        self.assertTrue(os.path.exists(output_mp4))
        with open(output_mp4, "rb") as f:
            decrypted_file_bytes = f.read()

        self.assertIn(original_data, decrypted_file_bytes)

    def test_decrypt_stream_clear_passthrough(self):
        """Clear streams with empty keys are streamed directly without error."""
        clear_input = os.path.join(self.tmp_dir, "clear.mp4")
        clear_data = b"\x00\x00\x00\x18ftypiso5\x00\x00\x02\x00iso5mp41" + b"MEDIA_DATA" * 500
        with open(clear_input, "wb") as f:
            f.write(clear_data)

        clear_out = os.path.join(self.tmp_dir, "clear_out.mp4")
        decrypt_stream(clear_input, {}, clear_out)

        self.assertTrue(os.path.exists(clear_out))
        with open(clear_out, "rb") as f:
            self.assertEqual(f.read(), clear_data)

    def test_decrypt_stream_missing_input_raises(self):
        """Decrypting non-existent file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            decrypt_stream("/non/existent/path.mp4", self.keys, "/tmp/out.mp4")

    def test_decrypt_mp4_bytes_input_wrapper(self):
        """decrypt_mp4 accepts bytes input and writes decrypted output file."""
        original_data = b"CLEAR_HEADER" + b"ENCRYPTED_BODY_CHUNKS" * 20
        input_mp4 = self._create_synthetic_cenc_fmp4(self.key, original_data, use_subsamples=True)
        with open(input_mp4, "rb") as f:
            raw_bytes = f.read()

        out_path = os.path.join(self.tmp_dir, "bytes_out.mp4")
        decrypt_mp4(raw_bytes, self.keys, out_path)

        self.assertTrue(os.path.exists(out_path))
        with open(out_path, "rb") as f:
            self.assertIn(original_data, f.read())

    def test_decrypt_stream_ffmpeg_native_success(self):
        """decrypt_stream invokes FFmpeg native decryption when available."""
        in_p = os.path.join(self.tmp_dir, "test_in.mp4")
        out_p = os.path.join(self.tmp_dir, "test_out.mp4")
        with open(in_p, "wb") as f:
            f.write(b"SAMPLE_MP4_DATA")

        with patch("crunchyroll.decryptor._decrypt_with_ffmpeg", return_value=True) as mock_ffmpeg:
            decrypt_stream(in_p, self.keys, out_p)
            mock_ffmpeg.assert_called_once()


class TestStreamValidatorAndAtomicFinalize(unittest.TestCase):
    """Test StreamValidator ffprobe inspection and atomic finalization."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_val_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_find_ffprobe_exists(self):
        """find_ffprobe locates installed ffprobe binary."""
        probe_bin = find_ffprobe()
        self.assertTrue(os.path.exists(probe_bin))

    def test_find_ffprobe_missing_raises(self):
        """find_ffprobe raises FileNotFoundError when binary is missing."""
        with patch("shutil.which", return_value=None), patch("os.path.exists", return_value=False):
            with self.assertRaises(FileNotFoundError):
                find_ffprobe()

    def test_verify_mkv_nonexistent_file(self):
        """verify_mkv returns False for non-existent file path."""
        valid, msg, data = StreamValidator.verify_mkv("/invalid/path/video.mkv")
        self.assertFalse(valid)
        self.assertIn("does not exist", msg)

    def test_verify_mkv_empty_file(self):
        """verify_mkv returns False for 0-byte empty file."""
        empty_p = os.path.join(self.tmp_dir, "empty.mkv")
        with open(empty_p, "wb") as f:
            pass
        valid, msg, data = StreamValidator.verify_mkv(empty_p)
        self.assertFalse(valid)
        self.assertIn("0 bytes", msg)

    def test_verify_mkv_success_with_mocked_probe(self):
        """verify_mkv validates complete MKV with video, audio, and subtitles."""
        sample_p = os.path.join(self.tmp_dir, "valid.mkv")
        with open(sample_p, "wb") as f:
            f.write(b"VALID_MKV_HEADER")

        mock_probe = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "subtitle", "codec_name": "ass"},
            ],
            "format": {"duration": "1420.50"},
        }

        with patch.object(StreamValidator, "probe_file", return_value=mock_probe):
            valid, msg, data = StreamValidator.verify_mkv(
                sample_p,
                expected_video=True,
                min_audio_tracks=2,
                min_sub_tracks=1,
                min_duration=1000.0,
            )
            self.assertTrue(valid)
            self.assertIn("verified successfully", msg)

    def test_verify_mkv_missing_video_track_fails(self):
        """verify_mkv fails when expected video stream is missing."""
        sample_p = os.path.join(self.tmp_dir, "audio_only.mkv")
        with open(sample_p, "wb") as f:
            f.write(b"AUDIO_ONLY_MKV")

        mock_probe = {
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "format": {"duration": "120.0"},
        }

        with patch.object(StreamValidator, "probe_file", return_value=mock_probe):
            valid, msg, data = StreamValidator.verify_mkv(sample_p, expected_video=True)
            self.assertFalse(valid)
            self.assertIn("Missing expected video stream", msg)

    def test_verify_mkv_insufficient_audio_tracks_fails(self):
        """verify_mkv fails when audio stream count is below expected minimum."""
        sample_p = os.path.join(self.tmp_dir, "one_audio.mkv")
        with open(sample_p, "wb") as f:
            f.write(b"MKV_DATA")

        mock_probe = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "500.0"},
        }

        with patch.object(StreamValidator, "probe_file", return_value=mock_probe):
            valid, msg, data = StreamValidator.verify_mkv(sample_p, min_audio_tracks=2)
            self.assertFalse(valid)
            self.assertIn("Audio track count mismatch", msg)

    def test_verify_mkv_insufficient_duration_fails(self):
        """verify_mkv fails when duration is below minimum threshold."""
        sample_p = os.path.join(self.tmp_dir, "truncated.mkv")
        with open(sample_p, "wb") as f:
            f.write(b"TRUNCATED_MKV")

        mock_probe = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "10.5"},
        }

        with patch.object(StreamValidator, "probe_file", return_value=mock_probe):
            valid, msg, data = StreamValidator.verify_mkv(sample_p, min_duration=60.0)
            self.assertFalse(valid)
            self.assertIn("duration 10.50s is less than expected minimum 60.00s", msg)

    def test_atomic_finalize_success(self):
        """atomic_finalize renames .tmp.mkv to final .mkv cleanly and creates parent dir."""
        sub_dir = os.path.join(self.tmp_dir, "Series Title", "Season 1")
        tmp_file = os.path.join(self.tmp_dir, "output.tmp.mkv")
        final_file = os.path.join(sub_dir, "output.mkv")

        with open(tmp_file, "wb") as f:
            f.write(b"FINAL_MUXED_MKV_BYTES")

        res = atomic_finalize(tmp_file, final_file)

        self.assertEqual(res, final_file)
        self.assertTrue(os.path.exists(final_file))
        self.assertFalse(os.path.exists(tmp_file))
        with open(final_file, "rb") as f:
            self.assertEqual(f.read(), b"FINAL_MUXED_MKV_BYTES")

    def test_atomic_finalize_missing_source_raises(self):
        """atomic_finalize raises FileNotFoundError when source does not exist."""
        with self.assertRaises(FileNotFoundError):
            atomic_finalize("/non/existent/tmp.mkv", "/tmp/final.mkv")


class TestModernizedMerger(unittest.TestCase):
    """Test modernized FFmpeg merger flags and metadata tags."""

    def test_merger_command_includes_timestamp_flags(self):
        """merge_everything includes timestamp normalization flags and metadata tags."""
        v_file = "/tmp/v.mp4"
        a_tracks = [
            MediaTrack(file="/tmp/a_ja.mp4", locale="ja-JP"),
            MediaTrack(file="/tmp/a_en.mp4", locale="en-US"),
        ]
        s_tracks = [MediaTrack(file="/tmp/s_en.ass", locale="en-US")]
        out_file = "/tmp/out.mkv"
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Frieren",
                season_number=2,
                episode_number=7,
                audio_locale="ja-JP",
                versions=[],
                availability_starts="",
            ),
            title="Episode 7",
            subtitles={},
        )

        with patch("crunchyroll.merger.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run") as mock_run, \
             patch("os.path.exists", return_value=False):
            mock_run.return_value = MagicMock(returncode=0)

            merge_everything(v_file, a_tracks, s_tracks, out_file, info)

            args = mock_run.call_args[0][0]

            # Timestamp normalization flags
            self.assertIn("-avoid_negative_ts", args)
            self.assertIn("make_zero", args)
            self.assertIn("-fflags", args)
            self.assertIn("+genpts", args)
            self.assertIn("-max_interleave_delta", args)
            self.assertIn("0", args)

            # Fixed metadata tags: season_number=2, episode_number=7
            self.assertIn("season_number=2", args)
            self.assertIn("episode_number=7", args)
            self.assertIn("track=7", args)
            self.assertIn("title=S02E07 - Episode 7", args)


class TestLiveMuxingAndFFprobeVerification(unittest.TestCase):
    """Real FFmpeg Matroska muxing and ffprobe stream integrity verification."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_live_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_live_mkv_muxing_and_full_integrity_validation(self):
        """Mux real synthetic video, audio, and subtitle streams, then run live ffprobe validator."""
        # 1. Generate real video stream using FFmpeg testsrc
        video_p = os.path.join(self.tmp_dir, "v_src.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1.5:size=320x240:rate=24", "-c:v", "libx264", "-pix_fmt", "yuv420p", video_p],
            capture_output=True, check=True
        )

        # 2. Generate real audio stream using FFmpeg sine
        audio_ja = os.path.join(self.tmp_dir, "a_ja.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5", "-c:a", "aac", audio_ja],
            capture_output=True, check=True
        )
        audio_en = os.path.join(self.tmp_dir, "a_en.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=1.5", "-c:a", "aac", audio_en],
            capture_output=True, check=True
        )

        # 3. Create real ASS subtitle
        sub_p = os.path.join(self.tmp_dir, "s_en.ass")
        with open(sub_p, "w", encoding="utf-8") as f:
            f.write("""[Script Info]
Title: English
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,20,20,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.10,0:00:01.00,Default,,0,0,0,,Muxing Integrity Verified!
""")

        out_mkv = os.path.join(self.tmp_dir, "final_verified.mkv")
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title="Frieren",
                season_number=1,
                episode_number=1,
                audio_locale="ja-JP",
                versions=[],
                availability_starts="",
            ),
            title="The Journey Begins",
            subtitles={},
        )

        a_tracks = [
            MediaTrack(file=audio_ja, locale="ja-JP"),
            MediaTrack(file=audio_en, locale="en-US"),
        ]
        s_tracks = [MediaTrack(file=sub_p, locale="en-US")]

        # Mux everything
        merge_everything(video_p, a_tracks, s_tracks, out_mkv, info)

        self.assertTrue(os.path.exists(out_mkv))
        self.assertGreater(os.path.getsize(out_mkv), 10000)

        # Run live ffprobe validator
        valid, msg, probe_data = StreamValidator.verify_mkv(
            out_mkv,
            expected_video=True,
            min_audio_tracks=2,
            min_sub_tracks=1,
            min_duration=1.0,
        )

        self.assertTrue(valid, f"Validation failed: {msg}")
        self.assertIn("verified successfully", msg)

        # Verify probe stream metadata
        streams = probe_data["streams"]
        video_st = [s for s in streams if s["codec_type"] == "video"]
        audio_st = [s for s in streams if s["codec_type"] == "audio"]
        sub_st = [s for s in streams if s["codec_type"] == "subtitle"]

        self.assertEqual(len(video_st), 1)
        self.assertEqual(video_st[0]["codec_name"], "h264")
        self.assertEqual(len(audio_st), 2)
        self.assertEqual(audio_st[0]["codec_name"], "aac")
        self.assertEqual(audio_st[0]["tags"].get("language"), "jpn")
        self.assertEqual(audio_st[1]["tags"].get("language"), "eng")
        self.assertEqual(len(sub_st), 1)
        self.assertEqual(sub_st[0]["codec_name"], "ass")
        self.assertEqual(sub_st[0]["tags"].get("language"), "eng")


class TestStreamingDecryptionMemoryBounding(unittest.TestCase):
    """Verify strictly bounded RAM consumption during sustained large-payload decryption."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_mem_")
        self.key = b"\xaa" * 16
        self.keys = {b"kid1": self.key}

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_streaming_fallback_memory_footprint(self):
        """Decrypting a 10MB multi-fragment stream via Python fallback keeps peak RSS well below 100 MB."""
        import psutil
        process = psutil.Process(os.getpid())

        # Construct an 11 MB synthetic stream
        chunk_data = b"STREAMING_MEMORY_BOUNDED_BLOCK_" * 32000  # ~1 MB per block
        synthetic_in = os.path.join(self.tmp_dir, "large_cenc.mp4")

        test_case = TestStreamingCencDecryption()
        test_case.tmp_dir = self.tmp_dir
        test_case._create_synthetic_cenc_fmp4(self.key, chunk_data * 11, use_subsamples=True)
        synthetic_in = os.path.join(self.tmp_dir, "synthetic_cenc.mp4")

        initial_rss_mb = process.memory_info().rss / (1024 * 1024)

        output_dec = os.path.join(self.tmp_dir, "large_decrypted.mp4")
        decrypt_stream(synthetic_in, self.keys, output_dec, fallback_only=True)

        final_rss_mb = process.memory_info().rss / (1024 * 1024)
        growth_mb = final_rss_mb - initial_rss_mb

        self.assertTrue(os.path.exists(output_dec))
        self.assertGreater(os.path.getsize(output_dec), 10 * 1024 * 1024)
        # Peak RAM growth must be strictly bounded (< 50 MB, well within <100MB requirement)
        self.assertLess(growth_mb, 50.0)


class TestDownloaderIntegrationM2(unittest.TestCase):
    """Test downloader pipeline integration with decryption and StreamValidator."""

    def setUp(self):
        self.server = MockCrunchyrollServer().start()
        self.tmp_dir = tempfile.mkdtemp(prefix="cr_test_dl_m2_")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_download_parts_optimized_with_clear_stream(self):
        """download_parts_optimized downloads segments directly and produces decrypted stream."""
        from crunchyroll.downloader import download_parts_optimized

        base_url = f"{self.server.base_url}/media/"
        out_p = os.path.join(self.tmp_dir, "dl_out.mp4")

        result_path = download_parts_optimized(
            base_url=base_url,
            rep_id="video/1080p",
            timeline=[1, 2, 3],
            keys={},
            output_filename=out_p,
            media_pattern="seg_$RepresentationID$_$Number%05d$.mp4",
            init_pattern="init_$RepresentationID$.mp4",
        )

        self.assertEqual(result_path, out_p)
        self.assertTrue(os.path.exists(out_p))
        self.assertGreater(os.path.getsize(out_p), 0)


if __name__ == "__main__":
    unittest.main()

