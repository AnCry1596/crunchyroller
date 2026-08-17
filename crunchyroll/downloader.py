import os
import subprocess
import sys
import time
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import requests
from Crypto.Cipher import AES
from Crypto.Util import Counter

from .api import delete_stream, get_episode, get_season_episodes, get_series
from .drm import get_license
from .http_client import CrunchyrollHttpClient
from .merger import find_ffmpeg, merge_everything
from .mpd import expand_timeline, get_base_url, get_pssh, parse_manifest
from .types import (
    DubVersion,
    EpisodeInfo,
    EpisodeMetadata,
    MediaTrack,
    SeasonEpisode,
)
from .utils import sanitize_filename, track_title

MAX_WORKERS = 10
MAX_RETRIES = 5
BACKOFF_FACTOR = 1.5


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


def download_part(url: str, max_retries: int = MAX_RETRIES) -> bytes:
    """grab a segment. retry if cr gets mad."""
    headers = {
        "Origin": "https://static.crunchyroll.com",
        "Referer": "https://static.crunchyroll.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
    }
    attempt = 0
    while attempt < max_retries:
        if attempt > 0:
            time.sleep(attempt * 2)

        try:
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.status_code == 200:
                return resp.content
            print(
                f"\nSegment download returned status {resp.status_code}, retrying ({attempt + 1}/{max_retries})..."
            )
        except Exception as e:
            print(
                f"\nSegment download failed ({e}), retrying ({attempt + 1}/{max_retries})..."
            )

        attempt += 1

    raise RuntimeError(f"failed after {max_retries} retries")




def decrypt_mp4(parts: bytes, keys: Dict[bytes, bytes], output_filename: str) -> None:
    """decrypt mp4 parts with ffmpeg cenc demuxer"""
    if not keys or (b"encv" not in parts and b"enca" not in parts):
        with open(output_filename, "wb") as f:
            f.write(parts)
        return

    raw_tmp = output_filename + ".raw.mp4"
    with open(raw_tmp, "wb") as f:
        f.write(parts)

    key_hex = list(keys.values())[0].hex()
    ffmpeg_bin = find_ffmpeg()
    ffmpeg_cmd = [
        ffmpeg_bin,
        "-y",
        "-decryption_key",
        key_hex,
        "-i",
        raw_tmp,
        "-c",
        "copy",
        output_filename,
    ]

    res = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if os.path.exists(raw_tmp):
        try:
            os.remove(raw_tmp)
        except Exception:
            pass

    if res.returncode == 0 and os.path.exists(output_filename) and os.path.getsize(output_filename) > 0:
        return

    # fallback to manual decryption if ffmpeg chokes
    print("Fallback to Python decrypt_mp4...")
    buf = bytearray(parts)

    def read_u32(b, pos):
        if pos + 4 > len(b):
            return 0
        return (b[pos] << 24) | (b[pos + 1] << 16) | (b[pos + 2] << 8) | b[pos + 3]

    def read_u16(b, pos):
        if pos + 2 > len(b):
            return 0
        return (b[pos] << 8) | b[pos + 1]

    def write_u32(b, pos, val):
        b[pos] = (val >> 24) & 0xFF
        b[pos + 1] = (val >> 16) & 0xFF
        b[pos + 2] = (val >> 8) & 0xFF
        b[pos + 3] = val & 0xFF

    key = next(iter(keys.values()))

    for enc_tag, clean_tag in ((b"encv", b"avc1"), (b"enca", b"mp4a")):
        idx_enc = buf.find(enc_tag)
        if idx_enc != -1:
            buf[idx_enc : idx_enc + 4] = clean_tag
            enc_size_pos = idx_enc - 4
            enc_size = read_u32(buf, enc_size_pos)
            idx_sinf = buf.find(b"sinf", idx_enc)
            if idx_sinf != -1:
                sinf_size = read_u32(buf, idx_sinf - 4)
                sinf_start = idx_sinf - 4
                del buf[sinf_start : sinf_start + sinf_size]
                write_u32(buf, enc_size_pos, enc_size - sinf_size)
                for parent_tag in (b"stsd", b"stbl", b"minf", b"mdia", b"trak", b"moov"):
                    p_idx = buf.find(parent_tag)
                    if p_idx != -1 and p_idx < sinf_start:
                        p_size = read_u32(buf, p_idx - 4)
                        write_u32(buf, p_idx - 4, p_size - sinf_size)

    pos = 0
    buf_len = len(buf)

    try:
        while pos + 8 <= buf_len:
            box_size = read_u32(buf, pos)
            if box_size == 1:
                if pos + 16 > buf_len:
                    break
                box_size = int.from_bytes(buf[pos + 8 : pos + 16], "big")
            elif box_size == 0 or box_size < 8:
                box_size = buf_len - pos

            box_type = bytes(buf[pos + 4 : pos + 8])

            if box_type == b"moof":
                moof_start = pos
                moof_end = min(pos + box_size, buf_len)

                subsample_flag = False
                sample_ivs = []
                subsamples_list = []
                sample_sizes = []
                default_sample_size = 0
                trun_data_offset = None

                cur = moof_start + 8
                while cur + 8 <= moof_end:
                    b_size = read_u32(buf, cur)
                    if b_size <= 0 or cur + b_size > moof_end:
                        break
                    b_type = bytes(buf[cur + 4 : cur + 8])

                    if b_type == b"traf":
                        t_cur = cur + 8
                        t_end = min(cur + b_size, moof_end)
                        while t_cur + 8 <= t_end:
                            tb_size = read_u32(buf, t_cur)
                            if tb_size <= 0 or t_cur + tb_size > t_end:
                                break
                            tb_type = bytes(buf[t_cur + 4 : t_cur + 8])

                            if tb_type == b"tfhd":
                                tf_flags = (buf[t_cur + 9] << 16) | (buf[t_cur + 10] << 8) | buf[t_cur + 11]
                                p_tf = t_cur + 16
                                if tf_flags & 0x000001:
                                    p_tf += 8
                                if tf_flags & 0x000002:
                                    p_tf += 4
                                if tf_flags & 0x000008:
                                    p_tf += 4
                                if tf_flags & 0x000010 and p_tf + 4 <= t_end:
                                    default_sample_size = read_u32(buf, p_tf)

                            elif tb_type == b"trun":
                                tr_flags = (buf[t_cur + 9] << 16) | (buf[t_cur + 10] << 8) | buf[t_cur + 11]
                                tr_count = read_u32(buf, t_cur + 12)
                                p_tr = t_cur + 16
                                if tr_flags & 0x000001:
                                    trun_data_offset = int.from_bytes(buf[p_tr : p_tr + 4], "big", signed=True)
                                    p_tr += 4
                                if tr_flags & 0x000004:
                                    p_tr += 4
                                for _ in range(tr_count):
                                    if p_tr > t_end:
                                        break
                                    if tr_flags & 0x000100:
                                        p_tr += 4
                                    s_size = read_u32(buf, p_tr) if (tr_flags & 0x000200) else default_sample_size
                                    if tr_flags & 0x000200:
                                        p_tr += 4
                                    if tr_flags & 0x000400:
                                        p_tr += 4
                                    if tr_flags & 0x000800:
                                        p_tr += 4
                                    sample_sizes.append(s_size)

                            elif tb_type in (b"senc", b"uuid"):
                                senc_flags = (buf[t_cur + 9] << 16) | (buf[t_cur + 10] << 8) | buf[t_cur + 11]
                                subsample_flag = bool(senc_flags & 0x000002)
                                senc_count = read_u32(buf, t_cur + 12)
                                p_senc = t_cur + 16
                                for _ in range(senc_count):
                                    if p_senc + 8 > t_end:
                                        break
                                    iv = bytes(buf[p_senc : p_senc + 8])
                                    p_senc += 8
                                    sample_ivs.append(iv)
                                    subs = []
                                    if subsample_flag:
                                        if p_senc + 2 > t_end:
                                            break
                                        sub_count = read_u16(buf, p_senc)
                                        p_senc += 2
                                        for _ in range(sub_count):
                                            if p_senc + 6 > t_end:
                                                break
                                            c_len = read_u16(buf, p_senc)
                                            e_len = read_u32(buf, p_senc + 2)
                                            p_senc += 6
                                            subs.append((c_len, e_len))
                                    subsamples_list.append(subs)

                            t_cur += tb_size
                        break
                    cur += b_size

                mdat_start = moof_end
                if mdat_start + 8 <= buf_len:
                    mdat_size = read_u32(buf, mdat_start)
                    mdat_hdr = 8
                    if mdat_size == 1:
                        mdat_size = int.from_bytes(buf[mdat_start + 8 : mdat_start + 16], "big")
                        mdat_hdr = 16
                    elif mdat_size == 0:
                        mdat_size = buf_len - mdat_start

                    mdat_type = bytes(buf[mdat_start + 4 : mdat_start + 8])
                    if mdat_type == b"mdat":
                        m_pos = (moof_start + trun_data_offset) if trun_data_offset is not None else (mdat_start + mdat_hdr)
                        for idx, iv in enumerate(sample_ivs):
                            iv_full = iv + b"\x00" * (16 - len(iv))
                            ctr = Counter.new(128, initial_value=int.from_bytes(iv_full, "big"))
                            cipher = AES.new(key, AES.MODE_CTR, counter=ctr)

                            subs = subsamples_list[idx] if idx < len(subsamples_list) else []
                            s_size = sample_sizes[idx] if idx < len(sample_sizes) else 0

                            if subs:
                                for clear_len, enc_len in subs:
                                    m_pos += clear_len
                                    if m_pos + enc_len <= buf_len:
                                        buf[m_pos : m_pos + enc_len] = cipher.decrypt(bytes(buf[m_pos : m_pos + enc_len]))
                                        m_pos += enc_len
                            else:
                                if s_size > 0 and m_pos + s_size <= buf_len:
                                    buf[m_pos : m_pos + s_size] = cipher.decrypt(bytes(buf[m_pos : m_pos + s_size]))
                                    m_pos += s_size

            pos += box_size
    except Exception as e:
        print(f"\nWarning during decrypt_mp4: {e}, writing assembled bytes...")

    with open(output_filename, "wb") as f:
        f.write(buf)


def download_parts(
    base_url: str,
    representation_id: str,
    adaptation_set: ET.Element,
    keys: Dict[bytes, bytes],
    ep_title: str = "",
    progress_cb=None,
) -> str:
    """download all track segments at once and decrypt to a temp file"""
    seg_template = None
    for child in adaptation_set:
        if _clean_tag(child.tag) == "SegmentTemplate":
            seg_template = child
            break

    init_file = seg_template.attrib.get("initialization", "") if seg_template is not None else ""
    media_file = seg_template.attrib.get("media", "") if seg_template is not None else ""

    init_data = requests.get(build_url(base_url, representation_id, init_file)).content
    timeline = expand_timeline(adaptation_set)
    total = len(timeline)

    jobs = [
        (i, build_url(base_url, representation_id, media_file, item))
        for i, item in enumerate(timeline, start=1)
    ]
    results: List[Optional[bytes]] = [None] * total

    completed_count = 0
    start_time = time.time()
    downloaded_bytes = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_idx = {executor.submit(download_part, url): idx for idx, url in jobs}

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                data = future.result()
                results[idx - 1] = data
                completed_count += 1
                if data:
                    downloaded_bytes += len(data)

                elapsed = time.time() - start_time
                speed = (downloaded_bytes / elapsed / (1024 * 1024)) if elapsed > 0 else 0
                speed_str = f"{speed:.2f} MB/s"
                percent = (100 * completed_count) // total if total > 0 else 100

                if sys.stdout is not None:
                    try:
                        sys.stdout.write(f"\rDownloaded {completed_count} of {total} segments ({percent}%)")
                        sys.stdout.flush()
                    except Exception:
                        pass

                if progress_cb:
                    progress_cb(ep_title, completed_count, total, speed_str, "downloading")
            except Exception as e:
                if progress_cb:
                    progress_cb(ep_title, completed_count, total, "0 MB/s", "failed")
                try:
                    if sys.stdout is not None:
                        print()
                except Exception:
                    pass
                raise e

    try:
        if sys.stdout is not None:
            print("\nFinished downloading!")
    except Exception:
        pass

    parts = bytearray(init_data)
    for res in results:
        if res:
            parts.extend(res)

    tmp_file = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp_path = tmp_file.name
    tmp_file.close()

    decrypt_mp4(bytes(parts), keys, tmp_path)
    return tmp_path


def download_subs(url: str) -> str:
    """grab subs and stash in a temp file"""
    content = requests.get(url).content
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
    progress_cb=None,
) -> None:
    """download all streams for an episode and mux to mkv"""
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
                sub_file = download_subs(first_episode.subtitles[loc].url)
                sub_tracks.append(MediaTrack(file=sub_file, locale=loc))

        if sub_tracks:
            print("Downloaded subtitles!")

        video_file: Optional[str] = None
        audio_tracks: List[MediaTrack] = []

        for i, version in enumerate(versions):
            ep = first_episode
            content_id = base_content_id  # use base episode id for the first run
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

            audio_file = download_parts(audio_base_url, audio_rep_id, audio_set, keys, ep_title=info.title, progress_cb=progress_cb)
            audio_tracks.append(MediaTrack(file=audio_file, locale=version.audio_locale))

            if i == 0:
                print("Downloading video...")
                video_base_url, video_rep_id = get_base_url(video_set, True, video_quality)
                if not video_base_url or not video_rep_id:
                    raise RuntimeError(
                        "failed to get the video base URL, maybe the video quality you entered is wrong?"
                    )
                video_file = download_parts(video_base_url, video_rep_id, video_set, keys, ep_title=info.title, progress_cb=progress_cb)

            success = delete_stream(client, version.guid, ep.token)
            if not success:
                print("Failed to delete stream (session token might expired)")

        if not video_file:
            raise RuntimeError("No video file downloaded!")

        merge_everything(
            video_file=video_file,
            audio_tracks=audio_tracks,
            sub_tracks=sub_tracks,
            output_file=output_filename,
            info=info,
        )

        print(f"\nDownload finished! Output file: {output_filename}\n")
        return output_filename

    finally:
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
    progress_cb=None,
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
    progress_cb=None,
    debug: bool = False,
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
        )
        print()
