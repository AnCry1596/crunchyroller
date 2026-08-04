import os
import shutil
import subprocess
from typing import List
from .types import EpisodeInfo, MediaTrack
from .utils import LANGUAGE_CODES, track_title


def find_ffmpeg() -> str:
    """locates ffmpeg binary locally or in PATH"""
    local_binary = os.path.join(os.getcwd(), "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if os.path.exists(local_binary):
        return local_binary
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError(
        "FFmpeg is not installed or not in PATH! Please install FFmpeg or place 'ffmpeg.exe' in the project folder."
    )


def merge_everything(
    video_file: str,
    audio_tracks: List[MediaTrack],
    sub_tracks: List[MediaTrack],
    output_file: str,
    info: EpisodeInfo,
) -> None:
    """mux everything into a single mkv"""
    ffmpeg_bin = find_ffmpeg()
    args = [ffmpeg_bin, "-y", "-i", video_file]

    for audio in audio_tracks:
        args.extend(["-i", audio.file])
    for sub in sub_tracks:
        args.extend(["-i", sub.file])

    # map video
    args.extend(["-map", "0:v:0"])

    # map audio
    for i in range(len(audio_tracks)):
        args.extend(["-map", f"{1 + i}:a:0"])

    # map subs
    for j in range(len(sub_tracks)):
        args.extend(["-map", f"{1 + len(audio_tracks) + j}"])

    args.extend(["-c:v", "copy", "-c:a", "copy"])
    if sub_tracks:
        args.extend(["-c:s", "copy"])

    # audio metadata
    for i, audio in enumerate(audio_tracks):
        lang_code = LANGUAGE_CODES.get(audio.locale, audio.locale)
        title = track_title(audio.locale)
        args.extend([
            f"-metadata:s:a:{i}", f"language={lang_code}",
            f"-metadata:s:a:{i}", f"title={title}",
        ])

    # sub metadata
    for j, sub in enumerate(sub_tracks):
        lang_code = LANGUAGE_CODES.get(sub.locale, sub.locale)
        title = track_title(sub.locale)
        args.extend([
            f"-metadata:s:s:{j}", f"language={lang_code}",
            f"-metadata:s:s:{j}", f"title={title}",
        ])

    # track dispositions
    for i in range(len(audio_tracks)):
        disposition = "default" if i == 0 else "0"
        args.extend([f"-disposition:a:{i}", disposition])

    for j in range(len(sub_tracks)):
        disposition = "default" if j == 0 else "0"
        args.extend([f"-disposition:s:{j}", disposition])

    # global metadata
    meta_title = (
        f"S{info.episode_metadata.season_number:02d}E{info.episode_metadata.episode_number:02d} - {info.title}"
    )
    args.extend([
        "-metadata:g", f"title={meta_title}",
        "-metadata:g", f"show={info.episode_metadata.series_title}",
        "-metadata:g", f"track={info.episode_metadata.episode_number}",
        "-metadata:g", f"season_number={info.episode_metadata.episode_number}",
        output_file,
    ])

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
            except OSError:
                pass
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")

    # cleanup temp files
    if os.path.exists(video_file):
        try:
            os.remove(video_file)
        except OSError:
            pass

    for audio in audio_tracks:
        if os.path.exists(audio.file):
            try:
                os.remove(audio.file)
            except OSError:
                pass

    for sub in sub_tracks:
        if os.path.exists(sub.file):
            try:
                os.remove(sub.file)
            except OSError:
                pass

    print(f"\nDownload finished! Output file: {output_file}\n")
