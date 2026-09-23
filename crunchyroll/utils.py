import re
import subprocess
import sys


def get_subprocess_kwargs() -> dict:
    """Return platform-specific kwargs for subprocess to prevent console window popup on Windows."""
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return kwargs


LANGUAGE_NAMES = {
    "ja-JP": "日本語",
    "en-US": "English",
    "en-IN": "English (India)",
    "id-ID": "Bahasa Indonesia",
    "ms-MY": "Bahasa Melayu",
    "ca-ES": "Català",
    "de-DE": "Deutsch",
    "es-419": "Español (América Latina)",
    "es-ES": "Español (España)",
    "fr-FR": "Français",
    "it-IT": "Italiano",
    "pl-PL": "Polski",
    "pt-BR": "Português (Brasil)",
    "pt-PT": "Português (Portugal)",
    "vi-VN": "Tiếng Việt",
    "tr-TR": "Türkçe",
    "ru-RU": "Русский",
    "ar-SA": "العربية",
    "hi-IN": "हिंदी",
    "ta-IN": "தமிழ்",
    "te-IN": "తెలుగు",
    "zh-CN": "中文 (普通话)",
    "zh-HK": "中文 (粵語)",
    "zh-TW": "中文 (國語)",
    "ko-KR": "한국어",
    "th-TH": "ไทย",
}

LANGUAGE_CODES = {
    "ja-JP": "jpn",
    "en-US": "eng",
    "en-IN": "eng",
    "id-ID": "ind",
    "ms-MY": "msa",
    "ca-ES": "cat",
    "de-DE": "deu",
    "es-419": "spa",
    "es-ES": "spa",
    "fr-FR": "fra",
    "it-IT": "ita",
    "pl-PL": "pol",
    "pt-BR": "por",
    "pt-PT": "por",
    "vi-VN": "vie",
    "tr-TR": "tur",
    "ru-RU": "rus",
    "ar-SA": "ara",
    "hi-IN": "hin",
    "ta-IN": "tam",
    "te-IN": "tel",
    "zh-CN": "zho",
    "zh-HK": "zho",
    "zh-TW": "zho",
    "ko-KR": "kor",
    "th-TH": "tha",
}


def track_title(locale: str) -> str:
    """get the language name from the locale code"""
    if locale.endswith("-cc"):
        base = locale[:-3]  # strip '-cc'
        base_name = LANGUAGE_NAMES.get(base, base)
        return f"{base_name} (CC)"
    return LANGUAGE_NAMES.get(locale, locale)


def locale_base(locale: str) -> str:
    """Return the base locale, stripping any '-cc' suffix."""
    return locale[:-3] if locale.endswith("-cc") else locale


def sanitize_filename(s: str) -> str:
    """clean up string so the OS doesn't complain about bad characters"""
    if not s:
        return "Unknown"

    # replace unicode dashes and quotes with clean ascii equivalents
    s = s.replace("—", "-").replace("–", "-").replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")

    # Replace colons with clean hyphen separators for Plex and Jellyfin title matching
    s = s.replace(":", " - ")

    # swap illegal chars with underscores
    res = re.sub(r'[\\/*?"<>|\'"`]', "_", s)
    # clean multiple whitespace, underscores, and hyphens
    res = re.sub(r"\s+", " ", res)
    res = re.sub(r"_{2,}", "_", res)
    res = re.sub(r"-\s*-+", "-", res)

    return res.strip(" ._-") or "Unknown"


def resolve_season_folder(
    series_title: str,
    season_number: int,
    season_title: str = "",
) -> str:
    """
    Resolves the folder name for a season or story arc.
    If Crunchyroll provides a distinct arc name (e.g. 'Mugen Train Arc', 'Entertainment District Arc', 'OADs'),
    uses that cleaned arc name. Otherwise falls back to standard 'Season XX'.
    """
    clean = (season_title or "").strip()
    if series_title and clean.lower().startswith(series_title.lower()):
        clean = clean[len(series_title):].strip(" :-–—")

    # Strip any trailing dub/audio tags like (English Dub), [English Dub], (Sub), etc.
    clean_no_dub = re.sub(
        r"\s*[\(\[](?:dub|audio|sub|simul|cut|russian|german|spanish|french|portuguese|hindi|arabic|castilian|italian|english)[^\)\]]*[\)\]]",
        "",
        clean,
        flags=re.I,
    ).strip(" :-–—")
    lower_no_dub = clean_no_dub.lower()

    # If formatted as "Season X: Arc Name" or "Season X - Arc Name", extract the Arc Name
    m = re.match(r"^season\s*\d+\s*[:\-–—]\s*(.+)$", clean_no_dub, flags=re.I)
    if m:
        clean_no_dub = m.group(1).strip(" :-–—")
        lower_no_dub = clean_no_dub.lower()

    # If it is empty, or merely a generic "Season X" / "S1" label, use formatted Season XX
    if not clean_no_dub or re.fullmatch(r"season\s*\d+", lower_no_dub) or re.fullmatch(r"s\d+", lower_no_dub):
        return f"Season {season_number:02d}"

    return sanitize_filename(clean_no_dub)



