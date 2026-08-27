"""
tests/mock_server.py

High-performance, thread-safe mock HTTP server for Crunchyroll DASH streaming,
auth, CMS, DRM license exchange, subtitles, and fault injection simulation.
"""

import http.server
import json
import os
import random
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple


SAMPLE_ASS_SUBTITLE = """[Script Info]
Title: English (US)
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.601
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,55,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.5,1,2,20,20,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.50,0:00:03.00,Default,,0,0,0,,Hello from Crunchyroller Mock Server!
Dialogue: 0,0:00:03.50,0:00:06.00,Default,,0,0,0,,High-performance DASH streaming test verified.
"""

SAMPLE_PSSH_B64 = "AAAAW3Bzc2gAAAAA7e+LqXnWSs6jyCfc1R0h7QAAADsIARIQ1234567890123456Gg4xMjM0NTY3ODkwMTIzNCIQ1234567890123456Kg4xMjM0NTY3ODkwMTIzNA=="


class MockMediaGenerator:
    """Generates small, valid synthetic MP4/DASH chunks and assets using FFmpeg or bitstream synthesis."""

    _cached_video_init: Optional[bytes] = None
    _cached_video_seg: Optional[bytes] = None
    _cached_audio_init: Optional[bytes] = None
    _cached_audio_seg: Optional[bytes] = None
    _lock = threading.Lock()

    @classmethod
    def get_video_init_and_seg(cls) -> Tuple[bytes, bytes]:
        with cls._lock:
            if cls._cached_video_init is not None and cls._cached_video_seg is not None:
                return cls._cached_video_init, cls._cached_video_seg

            tmpdir = tempfile.mkdtemp(prefix="cr_mock_v_")
            try:
                mpd_path = os.path.join(tmpdir, "manifest.mpd")
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-b:v", "500k",
                    "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
                    "-seg_duration", "1",
                    "-init_seg_name", "init_video.mp4",
                    "-media_seg_name", "seg_video_$Number%05d$.mp4",
                    "-f", "dash",
                    mpd_path
                ]
                subprocess.run(cmd, capture_output=True, check=True)
                init_p = os.path.join(tmpdir, "init_video.mp4")
                seg1_p = os.path.join(tmpdir, "seg_video_00001.mp4")
                if not os.path.exists(seg1_p):
                    segs = [f for f in os.listdir(tmpdir) if f.startswith("seg_video")]
                    seg1_p = os.path.join(tmpdir, segs[0]) if segs else init_p

                with open(init_p, "rb") as f:
                    cls._cached_video_init = f.read()
                with open(seg1_p, "rb") as f:
                    cls._cached_video_seg = f.read()
            except Exception:
                cls._cached_video_init = b"\x00\x00\x00\x18ftypiso5\x00\x00\x02\x00iso5mp41\x00\x00\x00\x08moov"
                cls._cached_video_seg = b"\x00\x00\x00\x10moof\x00\x00\x00\x08mdat"
            finally:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)

            return cls._cached_video_init, cls._cached_video_seg

    @classmethod
    def get_audio_init_and_seg(cls) -> Tuple[bytes, bytes]:
        with cls._lock:
            if cls._cached_audio_init is not None and cls._cached_audio_seg is not None:
                return cls._cached_audio_init, cls._cached_audio_seg

            tmpdir = tempfile.mkdtemp(prefix="cr_mock_a_")
            try:
                mpd_path = os.path.join(tmpdir, "manifest.mpd")
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
                    "-c:a", "aac", "-b:a", "128k",
                    "-seg_duration", "1",
                    "-init_seg_name", "init_audio.mp4",
                    "-media_seg_name", "seg_audio_$Number%05d$.mp4",
                    "-f", "dash",
                    mpd_path
                ]
                subprocess.run(cmd, capture_output=True, check=True)
                init_p = os.path.join(tmpdir, "init_audio.mp4")
                seg1_p = os.path.join(tmpdir, "seg_audio_00001.mp4")
                if not os.path.exists(seg1_p):
                    segs = [f for f in os.listdir(tmpdir) if f.startswith("seg_audio")]
                    seg1_p = os.path.join(tmpdir, segs[0]) if segs else init_p

                with open(init_p, "rb") as f:
                    cls._cached_audio_init = f.read()
                with open(seg1_p, "rb") as f:
                    cls._cached_audio_seg = f.read()
            except Exception:
                cls._cached_audio_init = b"\x00\x00\x00\x18ftypiso5\x00\x00\x02\x00iso5mp41\x00\x00\x00\x08moov"
                cls._cached_audio_seg = b"\x00\x00\x00\x10moof\x00\x00\x00\x08mdat"
            finally:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)

            return cls._cached_audio_init, cls._cached_audio_seg


def build_mpd_xml(
    base_url: str,
    video_qualities: Optional[List[Dict[str, Any]]] = None,
    audio_qualities: Optional[List[Dict[str, Any]]] = None,
    segment_count: int = 5,
    pssh: Optional[str] = None,
) -> str:
    """Constructs a standard DASH MPD XML string matching Crunchyroll's structure."""
    if video_qualities is None:
        video_qualities = [
            {"id": "video/1080p", "height": "1080", "bandwidth": "5000000"},
            {"id": "video/720p", "height": "720", "bandwidth": "2500000"},
            {"id": "video/480p", "height": "480", "bandwidth": "1200000"},
            {"id": "video/360p", "height": "360", "bandwidth": "600000"},
        ]

    if audio_qualities is None:
        audio_qualities = [
            {"id": "audio/ja-JP/192k", "bandwidth": "192000"},
            {"id": "audio/ja-JP/96k", "bandwidth": "96000"},
        ]

    cp_xml = ""
    if pssh:
        cp_xml = f"""
      <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed" default_KID="12345678-1234-1234-1234-123456789012">
        <cenc:pssh xmlns:cenc="urn:mpeg:cenc:2013">{pssh}</cenc:pssh>
      </ContentProtection>"""

    v_reps = ""
    for r in video_qualities:
        v_reps += f"""
      <Representation id="{r['id']}" bandwidth="{r['bandwidth']}" width="1920" height="{r['height']}" codecs="avc1.640028" frameRate="30/1">
        <BaseURL>{base_url}</BaseURL>
      </Representation>"""

    a_reps = ""
    for r in audio_qualities:
        a_reps += f"""
      <Representation id="{r['id']}" bandwidth="{r['bandwidth']}" codecs="mp4a.40.2" audioSamplingRate="48000">
        <BaseURL>{base_url}</BaseURL>
      </Representation>"""

    r_attr = f'r="{segment_count - 1}"' if segment_count > 1 else ""

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:cenc="urn:mpeg:cenc:2013" minBufferTime="PT1.5S" type="static" mediaPresentationDuration="PT{segment_count}S" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011">
  <Period id="0" duration="PT{segment_count}S">
    <AdaptationSet id="0" contentType="video" mimeType="video/mp4" subsegmentAlignment="true">{cp_xml}
      <SegmentTemplate startNumber="1" initialization="media/init_$RepresentationID$.mp4" media="media/seg_$RepresentationID$_$Number%05d$.mp4">
        <SegmentTimeline>
          <S t="0" d="1000" {r_attr}/>
        </SegmentTimeline>
      </SegmentTemplate>{v_reps}
    </AdaptationSet>
    <AdaptationSet id="1" contentType="audio" mimeType="audio/mp4" subsegmentAlignment="true">{cp_xml}
      <SegmentTemplate startNumber="1" initialization="media/init_$RepresentationID$.mp4" media="media/seg_$RepresentationID$_$Number%05d$.mp4">
        <SegmentTimeline>
          <S t="0" d="1000" {r_attr}/>
        </SegmentTimeline>
      </SegmentTemplate>{a_reps}
    </AdaptationSet>
  </Period>
</MPD>"""
    return xml


class MockCrunchyrollRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler implementing Crunchyroll Auth, CMS, DASH, and DRM endpoints."""

    timeout = 2.0

    def log_message(self, format: str, *args: Any) -> None:
        pass

    @property
    def server_instance(self) -> "MockCrunchyrollServer":
        return self.server  # type: ignore

    def _record_request(self, status: int) -> None:
        headers_dict = {k.lower(): v for k, v in self.headers.items()}
        self.server_instance.request_history.append({
            "method": self.command,
            "path": self.path,
            "headers": headers_dict,
            "timestamp": time.time(),
            "status": status,
        })

    def _send_json(self, data: Any, status: int = 200) -> None:
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self._record_request(status)
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_bytes(self, data: bytes, content_type: str = "application/octet-stream", status: int = 200) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self._record_request(status)
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_xml(self, xml_str: str, status: int = 200) -> None:
        try:
            data = xml_str.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/dash+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self._record_request(status)
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error_response(self, status: int, message: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            body = message.encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self._record_request(status)
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self) -> None:
        try:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()
            self._record_request(204)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:
        if self.server_instance.simulated_latency > 0:
            time.sleep(self.server_instance.simulated_latency)

        with self.server_instance.state_lock:
            if self.server_instance.rate_limit_remaining > 0:
                self.server_instance.rate_limit_remaining -= 1
                self._send_error_response(420, "Rate Limited")
                return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in self.server_instance.custom_routes:
            handler_fn = self.server_instance.custom_routes[path]
            handler_fn(self)
            return

        if path == "/auth/v1/token":
            self._send_json({
                "access_token": self.server_instance.current_token,
                "token_type": "Bearer",
                "expires_in": 300,
            })
            return

        if path == "/license/v1/license/widevine":
            self._send_bytes(b"\x08\x01\x12\x10mock_license_data", "application/octet-stream")
            return

        self._send_error_response(404, f"Not Found: {path}")

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if "/playback/v3/" in path and path.endswith("/delete"):
            self._send_json({"success": True}, 200)
            return

        self._send_error_response(404, f"Not Found: {path}")

    def do_GET(self) -> None:
        if self.server_instance.simulated_latency > 0:
            time.sleep(self.server_instance.simulated_latency)

        with self.server_instance.state_lock:
            if self.server_instance.rate_limit_remaining > 0:
                self.server_instance.rate_limit_remaining -= 1
                self._send_error_response(420, "Rate Limited")
                return

            if self.server_instance.auth_fail_remaining > 0:
                auth_header = self.headers.get("Authorization", "")
                if not auth_header.endswith(self.server_instance.renewed_token):
                    self.server_instance.auth_fail_remaining -= 1
                    self._send_error_response(401, "Unauthorized - Token Expired")
                    return

            if self.server_instance.flaky_segment_error_rate > 0:
                if random.random() < self.server_instance.flaky_segment_error_rate:
                    self._send_error_response(500, "Internal Server Error (Simulated Flakiness)")
                    return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in self.server_instance.custom_routes:
            handler_fn = self.server_instance.custom_routes[path]
            handler_fn(self)
            return

        if path.startswith("/playback/v3/") and path.endswith("/web/firefox/play"):
            content_id = path.split("/")[3]
            manifest_url = f"{self.server_instance.base_url}/manifest/{content_id}.mpd"
            subtitles = {
                "en-US": {
                    "language": "en-US",
                    "url": f"{self.server_instance.base_url}/subs/en-US.ass",
                },
                "es-419": {
                    "language": "es-419",
                    "url": f"{self.server_instance.base_url}/subs/es-419.ass",
                },
                "ja-JP": {
                    "language": "ja-JP",
                    "url": f"{self.server_instance.base_url}/subs/ja-JP.ass",
                },
            }
            self._send_json({
                "url": manifest_url,
                "subtitles": subtitles,
                "token": f"video_token_{content_id}",
            })
            return

        if path.startswith("/content/v2/cms/objects/"):
            content_id = path.split("/")[-1]
            obj = {
                "title": f"Episode Title for {content_id}",
                "episode_metadata": {
                    "series_title": self.server_instance.series_title,
                    "season_number": 1,
                    "episode_number": 1,
                    "audio_locale": "ja-JP",
                    "versions": [
                        {
                            "guid": content_id,
                            "media_guid": f"mg_{content_id}",
                            "season_guid": "sg_12345",
                            "audio_locale": "ja-JP",
                            "locale": "ja-JP",
                        },
                        {
                            "guid": f"{content_id}_en",
                            "media_guid": f"mg_{content_id}_en",
                            "season_guid": "sg_12345",
                            "audio_locale": "en-US",
                            "locale": "en-US",
                        },
                    ],
                    "availability_starts": "2026-01-01T00:00:00Z",
                },
            }
            self._send_json({"data": [obj]})
            return

        if "/content/v2/cms/series/" in path and path.endswith("/seasons"):
            seasons = [
                {
                    "id": "season_1",
                    "season_number": 1,
                    "audio_locale": "ja-JP",
                    "title": "Season 1",
                },
                {
                    "id": "season_2",
                    "season_number": 2,
                    "audio_locale": "ja-JP",
                    "title": "Season 2",
                },
            ]
            self._send_json({"data": seasons})
            return

        if path.startswith("/content/v2/cms/series/"):
            series_id = path.split("/")[-1]
            self._send_json({
                "data": [
                    {
                        "id": series_id,
                        "title": self.server_instance.series_title,
                        "description": "Mock anime series for testing.",
                    }
                ]
            })
            return

        if "/content/v2/cms/seasons/" in path and path.endswith("/episodes"):
            episodes = [
                {
                    "id": f"ep_{i}",
                    "title": f"Mock Episode {i}",
                    "sequence_number": i,
                    "episode_number": i,
                    "season_number": 1,
                    "series_title": self.server_instance.series_title,
                    "audio_locale": "ja-JP",
                    "episode_metadata": {
                        "series_title": self.server_instance.series_title,
                        "season_number": 1,
                        "episode_number": i,
                        "audio_locale": "ja-JP",
                        "versions": [
                            {
                                "guid": f"ep_{i}",
                                "media_guid": f"mg_{i}",
                                "season_guid": "sg_12345",
                                "audio_locale": "ja-JP",
                                "locale": "ja-JP",
                            }
                        ],
                    },
                }
                for i in range(1, 4)
            ]
            self._send_json({"data": episodes})
            return

        if path.startswith("/manifest/") and path.endswith(".mpd"):
            base_url = f"{self.server_instance.base_url}/"
            pssh = SAMPLE_PSSH_B64 if self.server_instance.enable_drm else None
            mpd_xml = build_mpd_xml(
                base_url=base_url,
                segment_count=self.server_instance.manifest_segment_count,
                pssh=pssh,
            )
            self._send_xml(mpd_xml)
            return

        if path.startswith("/subs/") and path.endswith(".ass"):
            self._send_bytes(SAMPLE_ASS_SUBTITLE.encode("utf-8"), "text/x-ssa; charset=utf-8")
            return

        # 8. Media Segments (/media/init_*.mp4, /media/seg_*.mp4)
        if path.startswith("/media/"):
            if "non_existent" in path or "404" in path:
                self._send_error_response(404, "Segment Not Found")
                return

            is_init = "init" in path
            if "audio" in path or "192" in path or "96" in path:
                a_init, a_seg = MockMediaGenerator.get_audio_init_and_seg()
                self._send_bytes(a_init if is_init else a_seg, "audio/mp4")
                return
            elif "video" in path or "1080" in path or "720" in path or "480" in path or "360" in path:
                v_init, v_seg = MockMediaGenerator.get_video_init_and_seg()
                self._send_bytes(v_init if is_init else v_seg, "video/mp4")
                return
            elif "init" in path or "seg" in path:
                # Default media chunk fallback
                v_init, v_seg = MockMediaGenerator.get_video_init_and_seg()
                self._send_bytes(v_init if is_init else v_seg, "video/mp4")
                return
            else:
                self._send_error_response(404, "Segment Not Found")
                return

        self._send_error_response(404, f"Not Found: {path}")


class MockCrunchyrollServer(http.server.ThreadingHTTPServer):
    """Local multi-threaded HTTP server simulating Crunchyroll streaming infrastructure."""

    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        super().__init__((host, port), MockCrunchyrollRequestHandler)
        self.host, self.port = self.server_address
        self.base_url = f"http://{self.host}:{self.port}"
        self.series_title = "Test Anime Show"
        self.current_token = "mock_token_initial_123"
        self.renewed_token = "mock_token_renewed_456"
        self.manifest_segment_count = 5
        self.enable_drm = False

        # Simulation & fault injection settings
        self.simulated_latency = 0.0
        self.rate_limit_remaining = 0
        self.auth_fail_remaining = 0
        self.flaky_segment_error_rate = 0.0

        # State tracking & request inspection
        self.state_lock = threading.Lock()
        self.request_history: List[Dict[str, Any]] = []
        self.custom_routes: Dict[str, Callable[[MockCrunchyrollRequestHandler], None]] = {}
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "MockCrunchyrollServer":
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def reset_state(self) -> None:
        with self.state_lock:
            self.request_history.clear()
            self.rate_limit_remaining = 0
            self.auth_fail_remaining = 0
            self.flaky_segment_error_rate = 0.0
            self.simulated_latency = 0.0
            self.custom_routes.clear()

    def get_url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def __enter__(self) -> "MockCrunchyrollServer":
        return self.start()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
