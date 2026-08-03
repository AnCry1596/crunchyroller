import os
import subprocess
from typing import List
from .types import EpisodeInfo, MediaTrack
from .utils import LANGUAGE_CODES, track_title


def merge_everything(
    video_file: str,
    audio_tracks: List[MediaTrack],
    sub_tracks: List[MediaTrack],
    output_file: str,
    info: EpisodeInfo,
) -> None:
    """
    Merges video file, audio tracks, and subtitle tracks into a single MKV container
    using FFmpeg, setting language tags, track titles, dispositions, and global metadata.
    """
    args = ["ffmpeg", "-y", "-i", video_file]
    for audio in audio_tracks:
        args.extend(["-i", audio.file])
    for sub in sub_tracks:
        args.extend(["-i", sub.file])

    # Map video stream
    args.extend(["-map", "0:v:0"])

    # Map audio streams
    for i in range(len(audio_tracks)):
        args.extend(["-map", f"{1 + i}:a:0"])

    # Map subtitle streams
    for j in range(len(sub_tracks)):
        args.extend(["-map", f"{1 + len(audio_tracks) + j}"])

    args.extend(["-c:v", "copy", "-c:a", "copy"])
    if sub_tracks:
        args.extend(["-c:s", "copy"])

    # Audio metadata
    for i, audio in enumerate(audio_tracks):
        lang_code = LANGUAGE_CODES.get(audio.locale, audio.locale)
        title = track_title(audio.locale)
        args.extend([
            f"-metadata:s:a:{i}", f"language={lang_code}",
            f"-metadata:s:a:{i}", f"title={title}",
        ])

    # Subtitle metadata
    for j, sub in enumerate(sub_tracks):
        lang_code = LANGUAGE_CODES.get(sub.locale, sub.locale)
        title = track_title(sub.locale)
        args.extend([
            f"-metadata:s:s:{j}", f"language={lang_code}",
            f"-metadata:s:s:{j}", f"title={title}",
        ])

    # Dispositions
    for i in range(len(audio_tracks)):
        disposition = "default" if i == 0 else "0"
        args.extend([f"-disposition:a:{i}", disposition])

    for j in range(len(sub_tracks)):
        disposition = "default" if j == 0 else "0"
        args.extend([f"-disposition:s:{j}", disposition])

    # Global metadata
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

    # Remove temporary files
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
