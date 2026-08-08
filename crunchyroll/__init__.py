"""crunchyroll downloader logic"""
import sys

class _SafeStream:
    def __init__(self, target):
        self._target = target

    def write(self, s):
        if self._target is None:
            return
        try:
            self._target.write(s)
        except (AttributeError, UnicodeEncodeError):
            try:
                enc = getattr(self._target, "encoding", "utf-8") or "utf-8"
                safe_s = s.encode(enc, errors="replace").decode(enc, errors="replace")
                self._target.write(safe_s)
            except Exception:
                pass

    def flush(self):
        if self._target is not None and hasattr(self._target, "flush"):
            try:
                self._target.flush()
            except Exception:
                pass

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    if hasattr(sys.stderr, "reconfigure"):
        try: sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

sys.stdout = _SafeStream(sys.stdout)
sys.stderr = _SafeStream(sys.stderr)

from .downloader import download_episode, download_season
from .merger import merge_everything
from .http_client import CrunchyrollHttpClient

__all__ = [
    "download_episode",
    "download_season",
    "merge_everything",
    "CrunchyrollHttpClient",
]
