import json
from typing import List, Optional, Tuple, Dict, Any
from urllib.parse import quote, urlparse

from .http_client import CrunchyrollHttpClient
from .types import (
    DubVersion,
    EpisodeInfo,
    EpisodeMetadata,
    PlaybackStream,
    Season,
    SeasonEpisode,
    Subtitle,
)


_SUBTITLE_LOCALE_ALIASES = {
    "en": "en-US",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-419",
    "pt": "pt-BR",
    "id": "id-ID",
    "vi": "vi-VN",
    "th": "th-TH",
    "english": "en-US",
    "bahasa indonesia": "id-ID",
    "tiếng việt": "vi-VN",
    "tieng viet": "vi-VN",
    "ไทย": "th-TH",
    "thai": "th-TH",
}


def _subtitle_locale(raw_key: object, raw_language: object) -> str:
    """Return a stable locale key from a subtitle API record."""
    key = str(raw_key or "").strip()
    language = str(raw_language or "").strip()
    key_lower = key.lower()
    language_lower = language.lower()

    if key_lower in {"none", "und", "unknown"} or not key:
        return _SUBTITLE_LOCALE_ALIASES.get(language_lower, "")
    if key_lower in _SUBTITLE_LOCALE_ALIASES:
        return _SUBTITLE_LOCALE_ALIASES[key_lower]
    return key



def parse_url_type(url: str) -> Tuple[str, str]:
    """figure out if the url is an episode, season, or series"""
    clean_url = url.split("?")[0].split("#")[0]
    parts = [p for p in clean_url.split("/") if p]

    for i, part in enumerate(parts):
        if part in ("watch", "episode") and i + 1 < len(parts):
            return ("episode", parts[i + 1])
        elif part == "series" and i + 1 < len(parts):
            return ("series", parts[i + 1])
        elif part == "season" and i + 1 < len(parts):
            return ("season", parts[i + 1])

    if len(parts) >= 4:
        c_type = parts[2]
        c_id = parts[3]
        if c_type == "watch":
            return ("episode", c_id)
        elif c_type == "series":
            return ("series", c_id)
        elif c_type == "season":
            return ("season", c_id)

    raise ValueError(f"Unable to parse Crunchyroll URL: {url}")


def get_episode(
    client: CrunchyrollHttpClient, content_id: str, debug: bool = False
) -> PlaybackStream:
    """grab the stream url and widevine token for an episode"""
    url = f"https://www.crunchyroll.com/playback/v3/{content_id}/web/firefox/play"
    resp = client.do_request("GET", url)
    resp.raise_for_status()

    data = resp.json()
    if debug:
        print("\n--- DEBUG PLAYBACK STREAM JSON ---")
        print(json.dumps(data, indent=2))
    manifest_url = data.get("url", "")
    if not manifest_url:
        hardsubs = data.get("hardsubs", {})
        if "en-US" in hardsubs:
            manifest_url = hardsubs["en-US"].get("url", "")
        elif "" in hardsubs:
            manifest_url = hardsubs[""].get("url", "")
        elif hardsubs:
            first_key = next(iter(hardsubs))
            manifest_url = hardsubs[first_key].get("url", "")

    subtitles_raw = data.get("subtitles", {})
    subtitles = {}
    if isinstance(subtitles_raw, dict):
        for lang, s_info in subtitles_raw.items():
            if isinstance(s_info, dict):
                subtitle_url = str(s_info.get("url", "") or "").strip()
                parsed_url = urlparse(subtitle_url)
                if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                    # Crunchyroll may include placeholder entries such as
                    # "none" with no URL; they are not downloadable tracks.
                    continue
                locale = _subtitle_locale(lang, s_info.get("language", lang))
                if not locale:
                    continue
                subtitles[locale] = Subtitle(
                    language=s_info.get("language", locale),
                    url=subtitle_url,
                )
    elif isinstance(subtitles_raw, list):
        for s_info in subtitles_raw:
            if not isinstance(s_info, dict):
                continue
            subtitle_url = str(s_info.get("url", "") or "").strip()
            parsed_url = urlparse(subtitle_url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                continue
            locale = _subtitle_locale(
                s_info.get("locale") or s_info.get("lang") or s_info.get("language"),
                s_info.get("language"),
            )
            if locale:
                subtitles[locale] = Subtitle(
                    language=s_info.get("language", locale),
                    url=subtitle_url,
                )

    token = data.get("token", "")
    return PlaybackStream(manifest_url=manifest_url, subtitles=subtitles, token=token)



def get_episode_info(
    client: CrunchyrollHttpClient, content_id: str
) -> EpisodeInfo:
    """get metadata (title, dubs, etc) for an episode"""
    url = f"https://www.crunchyroll.com/content/v2/cms/objects/{content_id}"
    resp = client.do_request("GET", url)
    resp.raise_for_status()

    data = resp.json()
    items = data.get("data", [])
    if not items:
        raise RuntimeError(f"No object data returned for content_id {content_id}")

    obj = items[0]
    title = obj.get("title", "")
    ep_meta_raw = obj.get("episode_metadata", {})

    versions_raw = ep_meta_raw.get("versions", [])
    versions = [
        DubVersion(
            guid=v.get("guid", ""),
            media_guid=v.get("media_guid", ""),
            season_guid=v.get("season_guid", ""),
            audio_locale=v.get("audio_locale", ""),
            locale=v.get("locale", ""),
        )
        for v in versions_raw
    ]

    ep_meta = EpisodeMetadata(
        series_title=ep_meta_raw.get("series_title", ""),
        season_number=ep_meta_raw.get("season_number", 0),
        episode_number=ep_meta_raw.get("episode_number", 0),
        audio_locale=ep_meta_raw.get("audio_locale", ""),
        versions=versions,
        availability_starts=ep_meta_raw.get("availability_starts", ""),
    )

    subs = {}
    return EpisodeInfo(episode_metadata=ep_meta, title=title, subtitles=subs)


def get_seasons(
    client: CrunchyrollHttpClient,
    series_id: str,
    audio_locale: str = "ja-JP",
    sub_locale: str = "en-US",
) -> List[Season]:
    """list seasons for a series"""
    url = (
        f"https://www.crunchyroll.com/content/v2/cms/series/{series_id}/seasons"
        f"?preferred_audio_language={audio_locale}&locale={sub_locale}"
    )
    resp = client.do_request("GET", url)
    resp.raise_for_status()

    data = resp.json()
    items = data.get("data", [])

    seasons = []
    for item in items:
        seasons.append(
            Season(
                id=item.get("id", ""),
                season_number=item.get("season_number", 0),
                audio_locale=item.get("audio_locale", ""),
                title=item.get("title", ""),
            )
        )
    seasons.sort(key=lambda s: (s.season_number if s.season_number > 0 else 999, s.title))
    return seasons


def get_season_episodes(
    client: CrunchyrollHttpClient,
    season_id: str,
    audio_locale: str = "ja-JP",
    sub_locale: str = "en-US",
) -> List[SeasonEpisode]:
    """list all episodes in a season"""
    url = (
        f"https://www.crunchyroll.com/content/v2/cms/seasons/{season_id}/episodes"
        f"?preferred_audio_language={audio_locale}&locale={sub_locale}"
    )
    resp = client.do_request("GET", url)
    resp.raise_for_status()

    data = resp.json()
    items = data.get("data", [])

    episodes = []
    for seq_idx, item in enumerate(items, start=1):
        ep_meta_raw = item.get("episode_metadata", {})
        versions_raw = ep_meta_raw.get("versions", [])
        versions = [
            DubVersion(
                guid=v.get("guid", ""),
                media_guid=v.get("media_guid", ""),
                season_guid=v.get("season_guid", ""),
                audio_locale=v.get("audio_locale", ""),
                locale=v.get("locale", ""),
            )
            for v in versions_raw
        ]

        ep_num_val = (
            ep_meta_raw.get("episode_number")
            if ep_meta_raw.get("episode_number") is not None
            else item.get("episode_number")
        )
        if ep_num_val is None:
            ep_num_val = item.get("sequence_number", seq_idx)

        try:
            ep_num = int(float(ep_num_val))
        except Exception:
            ep_num = seq_idx

        season_num_val = (
            ep_meta_raw.get("season_number")
            if ep_meta_raw.get("season_number") is not None
            else item.get("season_number", 1)
        )
        try:
            season_num = int(float(season_num_val))
        except Exception:
            season_num = 1

        episodes.append(
            SeasonEpisode(
                id=item.get("id", ""),
                title=item.get("title", f"Episode {ep_num}"),
                season_number=season_num,
                episode_number=ep_num,
                series_title=ep_meta_raw.get("series_title", item.get("series_title", "")),
                audio_locale=ep_meta_raw.get("audio_locale", item.get("audio_locale", "")),
                versions=versions,
                availability_starts=ep_meta_raw.get("availability_starts", ""),
            )
        )

    episodes.sort(key=lambda e: (e.season_number, e.episode_number if e.episode_number > 0 else 9999))
    return episodes


def get_series(
    client: CrunchyrollHttpClient,
    series_id: str,
    audio_locale: str = "ja-JP",
    sub_locale: str = "en-US",
) -> Dict[str, Any]:
    """fetch series metadata, seasons, and episodes in one go"""
    url = (
        f"https://www.crunchyroll.com/content/v2/cms/series/{series_id}"
        f"?preferred_audio_language={audio_locale}&locale={sub_locale}"
    )
    resp = client.do_request("GET", url)
    series_meta = {}
    if resp.status_code == 200:
        data = resp.json()
        items = data.get("data", [])
        if items:
            series_meta = items[0]

    seasons = get_seasons(client, series_id, audio_locale, sub_locale)
    all_episodes: List[SeasonEpisode] = []

    for season in seasons:
        eps = get_season_episodes(client, season.id, audio_locale, sub_locale)
        all_episodes.extend(eps)

    return {
        "id": series_id,
        "title": series_meta.get("title", ""),
        "description": series_meta.get("description", ""),
        "seasons": seasons,
        "episodes": all_episodes,
    }


def delete_stream(
    client: CrunchyrollHttpClient, content_id: str, video_token: str
) -> bool:
    """Release a playback session using its content ID and playback token.

    Cleanup is intentionally idempotent: an expired or already-removed
    playback session is considered successfully cleaned up.
    """
    if not content_id or not video_token:
        return False

    url = (
        "https://www.crunchyroll.com/playback/v1/token/"
        f"{quote(content_id, safe='')}/{quote(video_token, safe='')}"
    )
    headers = {"Accept": "*/*"}
    try:
        resp = client.do_request("DELETE", url, headers=headers)
    except Exception:
        # Stream cleanup must never hide the original download failure.
        return False

    return 200 <= resp.status_code < 300 or resp.status_code in {401, 404, 410}
