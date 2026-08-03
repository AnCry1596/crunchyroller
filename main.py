import argparse
import sys
import os
from typing import List

# force utf-8 on windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from crunchyroll.api import get_episode_info, get_season_episodes, get_seasons, parse_url_type
from crunchyroll.auth import load_config, save_config
from crunchyroll.downloader import download_episode, download_season, download_series
from crunchyroll.http_client import CrunchyrollHttpClient


def parse_langs(s: str) -> List[str]:
    """split comma-separated locales"""
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def process_url(client: CrunchyrollHttpClient, url: str, args: argparse.Namespace) -> None:
    """handle a single url (episode, season, or series)"""
    try:
        content_type, content_id = parse_url_type(url)
    except ValueError as e:
        print(f"Invalid URL format: {e}")
        return

    audio_langs = parse_langs(args.audio_lang)
    if not audio_langs:
        audio_langs = ["ja-JP"]

    subs_langs = parse_langs(args.subs_lang)
    primary_audio = audio_langs[0]
    primary_subs = subs_langs[0] if subs_langs else "en-US"

    video_quality = getattr(args, "quality_video", None) or getattr(args, "video_quality", "1080p")
    audio_quality = getattr(args, "quality_audio", None) or getattr(args, "audio_quality", "192k")

    if content_type == "episode":
        info = get_episode_info(client, content_id)
        download_episode(
            client,
            content_id,
            info,
            audio_langs,
            subs_langs,
            video_quality,
            audio_quality,
            debug=args.debug_manifest,
        )
    elif content_type == "series":
        download_series(
            client,
            content_id,
            audio_langs,
            subs_langs,
            video_quality,
            audio_quality,
            season_filter=args.season or 0,
            debug=args.debug_manifest,
        )
    elif content_type == "season":
        episodes = get_season_episodes(client, content_id, primary_audio, primary_subs)
        download_season(
            client,
            video_quality,
            audio_quality,
            audio_langs,
            subs_langs,
            episodes,
            debug=args.debug_manifest,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Downloads anime from Crunchyroll and outputs them in an MKV file."
    )
    parser.add_argument("--gui", action="store_true", help="Launch Web GUI server in browser")
    parser.add_argument("--email", type=str, default="", help="User email address")
    parser.add_argument("--password", type=str, default="", help="User password")
    parser.add_argument("--url", type=str, default="", help="URL of the episode/season/series to download")
    parser.add_argument("--file", type=str, default="", help="Path to a text file with one URL per line")
    parser.add_argument(
        "--audio-lang",
        type=str,
        default="ja-JP",
        help='Audio language(s), comma-separated for multiple (e.g. "ja-JP,en-US"). First is default.',
    )
    parser.add_argument(
        "--subs-lang",
        type=str,
        default="en-US",
        help='Subtitle language(s), comma-separated for multiple (e.g. "en-US,es-419"). First is default.',
    )
    parser.add_argument("--video-quality", type=str, default="1080p", help="Video quality (1080p, 720p, 480p, 360p)")
    parser.add_argument("--audio-quality", type=str, default="192k", help="Audio quality (192k, 96k)")
    parser.add_argument("--quality-video", type=str, default="", help="Alias for --video-quality")
    parser.add_argument("--quality-audio", type=str, default="", help="Alias for --audio-quality")
    parser.add_argument(
        "--season",
        type=int,
        default=0,
        help="Season number. Not used if an episode link is entered",
    )
    parser.add_argument(
        "--etp-rt",
        type=str,
        default="",
        help='The "etp_rt" cookie value of your account',
    )
    parser.add_argument(
        "--debug-manifest",
        action="store_true",
        help="Log raw episode playback JSON and manifest XML",
    )

    args = parser.parse_args()

    # launch gui if asked or if we have no inputs
    if args.gui or len(sys.argv) == 1 or (not args.url and not args.file and not args.etp_rt and not args.email):
        from web_gui import start_server
        start_server(port=8000, open_browser=True)
        return

    from crunchyroll.auth import auto_detect_etp_rt, load_config, save_config

    etp_rt = ""
    if args.etp_rt:
        etp_rt = args.etp_rt
        save_config({"etp_rt": etp_rt})
    else:
        cfg = load_config()
        etp_rt = cfg.get("etp_rt", "")

    if not etp_rt:
        detected = auto_detect_etp_rt()
        if detected:
            print("Auto-detected active Crunchyroll session token from browser!")
            etp_rt = detected
            save_config({"etp_rt": etp_rt})

    if not etp_rt:
        print(
            "No active Crunchyroll session token found!\n"
            "1. Make sure you are logged into crunchyroll.com in Brave/Chrome/Firefox/Edge.\n"
            "2. Or launch the Web GUI using: python main.py --gui\n"
            "3. Or pass your etp_rt token with --etp-rt \"TOKEN\""
        )
        sys.exit(1)

    client = CrunchyrollHttpClient(etp_rt=etp_rt, username=args.email, password=args.password)



    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                urls = [
                    line.strip()
                    for line in f
                    if line.strip() and line.strip().startswith("http")
                ]
        except Exception as e:
            print(f"Failed to open URLs file: {e}")
            sys.exit(1)

        print(f"Found {len(urls)} URLs to download\n")
        for i, u in enumerate(urls):
            print(f"=== [{i+1}/{len(urls)}] {u} ===")
            process_url(client, u, args)
            print()
    else:
        process_url(client, args.url, args)


if __name__ == "__main__":
    main()
