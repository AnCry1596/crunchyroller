import inspect
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple, Union

import requests

from .api import delete_stream, get_episode, get_season_episodes, get_series
from .decryptor import decrypt_mp4, decrypt_stream
from .drm import get_license
from .http_client import CrunchyrollHttpClient
from .integrity import StreamValidator, atomic_finalize
from .merger import find_ffmpeg, merge_everything
from .mpd import expand_timeline, get_base_url, get_pssh, parse_manifest
from .session_pool import ConcurrencyConfig, SessionPool
from .stream_assembler import StreamAssembler
from .types import (
    DubVersion,
    EpisodeInfo,
    EpisodeMetadata,
    MediaTrack,
    SeasonEpisode,
)
from .utils import sanitize_filename, track_title

MAX_WORKERS = 16
MAX_RETRIES = 5
BACKOFF_FACTOR = 1.5

_GLOBAL_SESSION_POOL: Optional[SessionPool] = None
_GLOBAL_POOL_LOCK = threading.Lock()


def _get_global_session_pool() -> SessionPool:
    global _GLOBAL_SESSION_POOL
    with _GLOBAL_POOL_LOCK:
        if _GLOBAL_SESSION_POOL is None:
            _GLOBAL_SESSION_POOL = SessionPool(
                config=ConcurrencyConfig(
                    pool_size=64,
                    min_workers=8,
                    max_workers=48,
                    initial_workers=16,
                )
            )
        return _GLOBAL_SESSION_POOL


def _clean_tag(tag: str) -> str:
    """strip xml namespace prefix"""
    return tag.split("}")[-1] if "}" in tag else tag


def build_url(
    base_url: str, representation_id: str, pattern: str, number: Optional[int] = None
) -> str:
    """build the segment url like the go version does"""
    res = pattern
    if number is not None:
        formatted_num = f"{number:05d}"
        res = res.replace("$Number%05d$", formatted_num)
        res = res.replace("$Number$", formatted_num)
    res = res.replace("$RepresentationID$", representation_id)
    return base_url + res


def _invoke_progress_cb(
    cb: Optional[Callable],
    title: str,
    completed: int,
    total: int,
    speed_str: str,
    speed_mb_s: float,
    status: str,
) -> None:
    """Invoke progress callback safely supporting various callback signatures."""
    if not cb:
        return
    try:
        sig = inspect.signature(cb)
        num_params = len(sig.parameters)
        if num_params == 5:
            cb(title, completed, total, speed_str, status)
        elif num_params == 3:
            cb(completed, total, speed_mb_s)
        elif num_params == 4:
            cb(title, completed, total, speed_str)
        else:
            cb(title, completed, total, speed_str, status)
    except (TypeError, ValueError):
        try:
            cb(title, completed, total, speed_str, status)
        except Exception:
            try:
                cb(completed, total, speed_mb_s)
            except Exception:
                pass


def download_part(
    url: str,
    save_path: Optional[str] = None,
    max_retries: int = MAX_RETRIES,
    pool: Optional[SessionPool] = None,
) -> Union[bytes, int]:
    """grab a segment directly to disk or memory. retry if cr gets mad."""
    session_pool = pool or _get_global_session_pool()

    headers = {
        "Origin": "https://static.crunchyroll.com",
        "Referer": "https://static.crunchyroll.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
    }

    if save_path:
        written = 0
        with open(save_path, "wb") as f:
            for chunk in session_pool.download_segment_stream(url, headers=headers):
                f.write(chunk)
                written += len(chunk)
        return written
    else:
        return session_pool.download_segment(url, headers=headers)


# decrypt_mp4 and decrypt_stream are imported from crunchyroll.decryptor


def download_parts(
    base_url: str,
    representation_id: str,
    adaptation_set: ET.Element,
    keys: Dict[bytes, bytes],
    ep_title: str = "",
    progress_cb: Optional[Callable] = None,
    pool: Optional[SessionPool] = None,
    concurrency_config: Optional[ConcurrencyConfig] = None,
) -> str:
    """Download all track segments using high-performance streaming assembly and session pool."""
    seg_template = None
    for child in adaptation_set:
        if _clean_tag(child.tag) == "SegmentTemplate":
            seg_template = child
            break

    init_file = seg_template.attrib.get("initialization", "") if seg_template is not None else ""
    media_file = seg_template.attrib.get("media", "") if seg_template is not None else ""

    timeline = expand_timeline(adaptation_set)
    total = len(timeline)

    # Use existing session pool or create a new dedicated instance
    own_pool = False
    if pool is None:
        cfg = concurrency_config or ConcurrencyConfig(
            min_workers=8,
            max_workers=48,
            initial_workers=16,
            aimd_enabled=True,
            hedging_enabled=True,
        )
        pool = SessionPool(config=cfg)
        own_pool = True

    try:
        # Create raw output path
        raw_tmp = tempfile.NamedTemporaryFile(suffix=".raw.mp4", delete=False)
        raw_path = raw_tmp.name
        raw_tmp.close()

        # Step 1: Download and write initialization segment directly
        init_url = build_url(base_url, representation_id, init_file)
        init_data = pool.download_segment(init_url)

        # Step 2: Initialize bounded streaming assembler (< 32 MB RAM)
        assembler = StreamAssembler(
            output_path=raw_path,
            total_segments=total,
            max_in_flight_mb=32,
            start_index=1,
        )
        assembler.write_init(init_data)

        # Step 3: Concurrent segment downloading with AIMD dynamic worker scaling
        job_queue: queue.Queue = queue.Queue()
        for i, item in enumerate(timeline, start=1):
            seg_url = build_url(base_url, representation_id, media_file, item)
            job_queue.put((i, seg_url))

        completed_count = 0
        downloaded_bytes = 0
        start_time = time.time()
        progress_lock = threading.Lock()
        worker_error: List[Exception] = []

        max_allowed_workers = pool.config.max_workers

        def _worker_loop():
            nonlocal completed_count, downloaded_bytes
            while not job_queue.empty() and not worker_error:
                try:
                    idx, url = job_queue.get_nowait()
                except queue.Empty:
                    break

                try:
                    seg_data = pool.download_segment_hedged(url)
                    assembler.add_segment(idx, seg_data)

                    with progress_lock:
                        completed_count += 1
                        downloaded_bytes += len(seg_data)
                        cur_completed = completed_count
                        cur_bytes = downloaded_bytes

                    elapsed = time.time() - start_time
                    speed_mb = (cur_bytes / elapsed / (1024 * 1024)) if elapsed > 0 else 0.0
                    speed_str = f"{speed_mb:.2f} MB/s"
                    percent = (100 * cur_completed) // total if total > 0 else 100

                    if sys.stdout is not None:
                        try:
                            sys.stdout.write(
                                f"\rDownloaded {cur_completed} of {total} segments ({percent}%) [{speed_str}]"
                            )
                            sys.stdout.flush()
                        except Exception:
                            pass

                    _invoke_progress_cb(
                        progress_cb,
                        ep_title,
                        cur_completed,
                        total,
                        speed_str,
                        speed_mb,
                        "downloading",
                    )
                except Exception as ex:
                    with progress_lock:
                        worker_error.append(ex)
                    assembler.abort(ex)
                    _invoke_progress_cb(
                        progress_cb,
                        ep_title,
                        completed_count,
                        total,
                        "0 MB/s",
                        0.0,
                        "failed",
                    )
                    break
                finally:
                    job_queue.task_done()

        # Spawn worker threads dynamically
        num_workers = min(total, max(pool.config.min_workers, pool.get_recommended_workers()))
        threads: List[threading.Thread] = []
        for _ in range(num_workers):
            t = threading.Thread(target=_worker_loop, daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        if worker_error:
            if sys.stdout is not None:
                try:
                    print()
                except Exception:
                    pass
            raise worker_error[0]

        try:
            if sys.stdout is not None:
                print("\nFinished downloading!")
        except Exception:
            pass

        # Finalize raw stream sequential assembly
        assembler.finish()

        # Step 4: Decrypt raw MP4 to decrypted output temp file
        decrypted_tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        decrypted_path = decrypted_tmp.name
        decrypted_tmp.close()

        decrypt_mp4(raw_path, keys, decrypted_path)
        if os.path.exists(raw_path):
            try:
                os.remove(raw_path)
            except Exception:
                pass

        return decrypted_path

    finally:
        if own_pool and pool:
            pool.close()


def download_parts_optimized(
    base_url: str,
    rep_id: str,
    timeline: List[int],
    keys: Optional[Dict[bytes, bytes]],
    output_filename: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
    concurrency_config: Optional[ConcurrencyConfig] = None,
    media_pattern: str = "$RepresentationID$_segment_$Number$.mp4",
    init_pattern: str = "$RepresentationID$_init.mp4",
) -> str:
    """Optimized download pipeline API interface matching PROJECT.md interface contract."""
    cfg = concurrency_config or ConcurrencyConfig(
        min_workers=8,
        max_workers=48,
        initial_workers=16,
        aimd_enabled=True,
        hedging_enabled=True,
    )
    pool = SessionPool(config=cfg)

    target_raw = output_filename + ".raw.mp4" if output_filename else tempfile.NamedTemporaryFile(suffix=".raw.mp4", delete=False).name
    target_out = output_filename or tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name

    total = len(timeline)
    try:
        # Step 1: Download init segment
        init_url = build_url(base_url, rep_id, init_pattern)
        init_data = pool.download_segment(init_url)

        # Step 2: StreamAssembler (<32MB RAM)
        assembler = StreamAssembler(
            output_path=target_raw,
            total_segments=total,
            max_in_flight_mb=32,
            start_index=1,
        )
        assembler.write_init(init_data)

        # Step 3: Concurrent download
        job_queue: queue.Queue = queue.Queue()
        for i, item in enumerate(timeline, start=1):
            seg_url = build_url(base_url, rep_id, media_pattern, item)
            job_queue.put((i, seg_url))

        completed_count = 0
        downloaded_bytes = 0
        start_time = time.time()
        progress_lock = threading.Lock()
        worker_error: List[Exception] = []

        def _worker():
            nonlocal completed_count, downloaded_bytes
            while not job_queue.empty() and not worker_error:
                try:
                    idx, url = job_queue.get_nowait()
                except queue.Empty:
                    break

                try:
                    seg_data = pool.download_segment_hedged(url)
                    assembler.add_segment(idx, seg_data)

                    with progress_lock:
                        completed_count += 1
                        downloaded_bytes += len(seg_data)
                        cur_completed = completed_count
                        cur_bytes = downloaded_bytes

                    elapsed = time.time() - start_time
                    speed_mb = (cur_bytes / elapsed / (1024 * 1024)) if elapsed > 0 else 0.0

                    if progress_callback:
                        progress_callback(cur_completed, total, speed_mb)
                except Exception as ex:
                    with progress_lock:
                        worker_error.append(ex)
                    assembler.abort(ex)
                    break
                finally:
                    job_queue.task_done()

        num_workers = min(total, max(cfg.min_workers, pool.get_recommended_workers()))
        threads = [threading.Thread(target=_worker, daemon=True) for _ in range(num_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if worker_error:
            raise worker_error[0]

        assembler.finish()

        # Step 4: Decrypt
        decrypt_mp4(target_raw, keys or {}, target_out)
        if os.path.exists(target_raw):
            try:
                os.remove(target_raw)
            except Exception:
                pass

        return target_out
    finally:
        pool.close()


def download_subs(url: str, pool: Optional[SessionPool] = None) -> str:
    """grab subs and stash in a temp file"""
    session_pool = pool or _get_global_session_pool()
    content = session_pool.download_segment(url)
    tmp_file = tempfile.NamedTemporaryFile(suffix=".ass", delete=False)
    tmp_path = tmp_file.name
    tmp_file.write(content)
    tmp_file.close()
    return tmp_path


def download_episode(
    client: CrunchyrollHttpClient,
    base_content_id: str,
    info: EpisodeInfo,
    audio_langs: List[str],
    subs_langs: List[str],
    video_quality: str,
    audio_quality: str,
    debug: bool = False,
    progress_cb: Optional[Callable] = None,
    concurrency_config: Optional[ConcurrencyConfig] = None,
) -> str:
    """download all streams for an episode and mux to mkv using shared session pooling"""
    versions: List[DubVersion] = []
    for loc in audio_langs:
        for version in info.episode_metadata.versions:
            if version.audio_locale == loc:
                versions.append(version)
                break

    if not versions:
        if info.episode_metadata.versions:
            versions.append(info.episode_metadata.versions[0])
        else:
            versions.append(
                DubVersion(
                    guid="",
                    media_guid="",
                    season_guid="",
                    audio_locale=info.episode_metadata.audio_locale,
                    locale="",
                )
            )

    active_streams: Dict[str, str] = {}
    print(
        f"Downloading: {info.title} (S{info.episode_metadata.season_number:02d}E{info.episode_metadata.episode_number:02d}) from {info.episode_metadata.series_title}"
    )

    # Initialize shared SessionPool across all tracks for connection reuse
    shared_pool = SessionPool(
        config=concurrency_config
        or ConcurrencyConfig(
            pool_size=64,
            min_workers=8,
            max_workers=48,
            initial_workers=24,
            aimd_enabled=True,
            hedging_enabled=True,
        )
    )

    try:
        first_episode = get_episode(client, base_content_id, debug=debug)
        active_streams[base_content_id] = first_episode.token

        if subs_langs and not first_episode.subtitles:
            print("Fetching subtitles from versions...")
            for version in info.episode_metadata.versions:
                if version.guid != base_content_id:
                    v_ep = get_episode(client, version.guid, debug=debug)
                    active_streams[version.guid] = v_ep.token
                    if v_ep.subtitles:
                        first_episode.subtitles = v_ep.subtitles
                        break

            if not first_episode.subtitles:
                print("Warning: Failed to fetch subtitles!")

        output_dir = sanitize_filename(info.episode_metadata.series_title)
        os.makedirs(output_dir, exist_ok=True)
        filename = (
            f"{sanitize_filename(info.episode_metadata.series_title)} "
            f"S{info.episode_metadata.season_number:02d}E{info.episode_metadata.episode_number:02d} - "
            f"{sanitize_filename(info.title)} [{video_quality}].mkv"
        )
        output_filename = os.path.join(output_dir, filename)

        if os.path.exists(output_filename):
            sz = os.path.getsize(output_filename)
            if sz > 10 * 1024 * 1024:
                print(f"Skipping (file already exists): {output_filename} ({sz / (1024*1024):.1f} MB)")
                return output_filename
            else:
                print(f"Existing file is corrupted/partial ({sz} bytes), re-downloading...")
                try:
                    os.remove(output_filename)
                except Exception:
                    pass

        sub_tracks: List[MediaTrack] = []
        for loc in subs_langs:
            if loc in first_episode.subtitles:
                print(f"Downloading subtitles for {track_title(loc)}...")
                sub_file = download_subs(first_episode.subtitles[loc].url, pool=shared_pool)
                sub_tracks.append(MediaTrack(file=sub_file, locale=loc))

        if sub_tracks:
            print("Downloaded subtitles!")

        video_file: Optional[str] = None
        audio_tracks: List[MediaTrack] = []

        # Prepare track metadata
        audio_descriptors = []
        for i, version in enumerate(versions):
            ep = first_episode
            content_id = base_content_id
            if i > 0:
                content_id = version.guid
                ep = get_episode(client, content_id, debug=debug)
                active_streams[content_id] = ep.token

            manifest = parse_manifest(client, ep.manifest_url, debug=debug)
            pssh = get_pssh(manifest)
            if not pssh:
                raise RuntimeError("PSSH not found in MPD manifest")

            keys = get_license(client, pssh, content_id, ep.token)

            periods = [e for e in manifest if _clean_tag(e.tag) == "Period"]
            period = periods[0] if periods else manifest
            adaptation_sets = [e for e in period if _clean_tag(e.tag) == "AdaptationSet"]

            video_set = None
            audio_set = None
            for aset in adaptation_sets:
                mime = aset.attrib.get("mimeType", "")
                ctype = aset.attrib.get("contentType", "")
                reps = [r for r in aset if _clean_tag(r.tag) == "Representation"]
                is_video = "video" in mime or "video" in ctype or any("height" in r.attrib for r in reps)
                is_audio = "audio" in mime or "audio" in ctype or any("audio" in r.attrib.get("id", "") for r in reps)
                if is_video and not video_set:
                    video_set = aset
                elif is_audio and not audio_set:
                    audio_set = aset

            if not audio_set and len(adaptation_sets) > 1:
                audio_set = adaptation_sets[1]
            if not video_set and len(adaptation_sets) > 0:
                video_set = adaptation_sets[0]

            print(f"Downloading {track_title(version.audio_locale)} audio...")
            audio_base_url, audio_rep_id = get_base_url(audio_set, False, audio_quality)
            if not audio_base_url or not audio_rep_id:
                raise RuntimeError(
                    f"failed to get the audio base URL for {version.audio_locale}, maybe the audio quality you entered is wrong?"
                )

            audio_file = download_parts(
                audio_base_url,
                audio_rep_id,
                audio_set,
                keys,
                ep_title=info.title,
                progress_cb=progress_cb,
                pool=shared_pool,
            )
            audio_tracks.append(MediaTrack(file=audio_file, locale=version.audio_locale))

            if i == 0:
                print("Downloading video...")
                video_base_url, video_rep_id = get_base_url(video_set, True, video_quality)
                if not video_base_url or not video_rep_id:
                    raise RuntimeError(
                        "failed to get the video base URL, maybe the video quality you entered is wrong?"
                    )
                video_file = download_parts(
                    video_base_url,
                    video_rep_id,
                    video_set,
                    keys,
                    ep_title=info.title,
                    progress_cb=progress_cb,
                    pool=shared_pool,
                )

            success = delete_stream(client, version.guid, ep.token)
            if not success:
                print("Failed to delete stream (session token might expired)")

        if not video_file:
            raise RuntimeError("No video file downloaded!")

        temp_output_filename = output_filename + ".tmp.mkv"
        merge_everything(
            video_file=video_file,
            audio_tracks=audio_tracks,
            sub_tracks=sub_tracks,
            output_file=temp_output_filename,
            info=info,
        )

        try:
            is_valid, msg, _ = StreamValidator.verify_mkv(
                temp_output_filename,
                expected_video=True,
                min_audio_tracks=len(audio_tracks),
                min_sub_tracks=len(sub_tracks),
            )
            if not is_valid:
                if os.path.exists(temp_output_filename):
                    try:
                        os.remove(temp_output_filename)
                    except OSError:
                        pass
                raise RuntimeError(f"Output MKV failed stream integrity verification: {msg}")
        except FileNotFoundError:
            pass

        atomic_finalize(temp_output_filename, output_filename)
        print(f"\nDownload finished! Output file: {output_filename}\n")
        return output_filename

    finally:
        shared_pool.close()
        print("Cleaning up...")
        for content_id, token in active_streams.items():
            delete_stream(client, content_id, token)


def download_season(
    client: CrunchyrollHttpClient,
    video_quality: str,
    audio_quality: str,
    audio_langs: List[str],
    subs_langs: List[str],
    episodes: List[SeasonEpisode],
    debug: bool = False,
    progress_cb: Optional[Callable] = None,
    concurrency_config: Optional[ConcurrencyConfig] = None,
) -> None:
    """download an entire season"""
    print(f"Found {len(episodes)} episodes in this season!\n")
    for i, ep in enumerate(episodes):
        print(f"=== [{i+1}/{len(episodes)}] {ep.title} ===")
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title=ep.series_title,
                season_number=ep.season_number,
                episode_number=ep.episode_number,
                audio_locale=ep.audio_locale,
                versions=ep.versions,
                availability_starts=ep.availability_starts,
            ),
            title=ep.title,
        )
        download_episode(
            client,
            ep.id,
            info,
            audio_langs,
            subs_langs,
            video_quality,
            audio_quality,
            debug=debug,
            progress_cb=progress_cb,
            concurrency_config=concurrency_config,
        )
        print()


def download_series(
    client: CrunchyrollHttpClient,
    series_id: str,
    audio_langs: List[str],
    subs_langs: List[str],
    video_quality: str,
    audio_quality: str,
    season_filter: int = 0,
    progress_cb: Optional[Callable] = None,
    debug: bool = False,
    concurrency_config: Optional[ConcurrencyConfig] = None,
) -> None:
    """grab everything for a series"""
    primary_audio = audio_langs[0] if audio_langs else "ja-JP"
    primary_subs = subs_langs[0] if subs_langs else "en-US"

    series_data = get_series(client, series_id, primary_audio, primary_subs)
    episodes = series_data.get("episodes", [])

    if season_filter > 0:
        episodes = [ep for ep in episodes if ep.season_number == season_filter]
        if not episodes:
            print(f"No episodes found for season {season_filter}.")
            return

    print(
        f"Downloading series '{series_data.get('title', series_id)}' "
        f"({len(episodes)} episodes across {len(series_data.get('seasons', []))} seasons)\n"
    )

    for i, ep in enumerate(episodes):
        print(f"=== [{i+1}/{len(episodes)}] {ep.series_title} S{ep.season_number:02d}E{ep.episode_number:02d} - {ep.title} ===")
        info = EpisodeInfo(
            episode_metadata=EpisodeMetadata(
                series_title=ep.series_title,
                season_number=ep.season_number,
                episode_number=ep.episode_number,
                audio_locale=ep.audio_locale,
                versions=ep.versions,
                availability_starts=ep.availability_starts,
            ),
            title=ep.title,
        )

        download_episode(
            client,
            ep.id,
            info,
            audio_langs,
            subs_langs,
            video_quality,
            audio_quality,
            debug=debug,
            progress_cb=progress_cb,
            concurrency_config=concurrency_config,
        )
        print()
