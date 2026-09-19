# crunchyroll/logger.py — persistent file and console logging

import logging
import os
import re
import sys
from typing import Optional

_LOG_FILE_NAME = "crunchyroller.log"
_LOGGER_INITIALIZED = False
_RESOLVED_LOG_PATH: Optional[str] = None
_LOGGING_ENABLED: bool = True
_FILE_HANDLER: Optional[logging.FileHandler] = None


def is_logging_enabled() -> bool:
    """Returns True if persistent file logging is enabled."""
    return _LOGGING_ENABLED


def set_logging_enabled(enabled: bool) -> None:
    global _LOGGING_ENABLED, _FILE_HANDLER
    _LOGGING_ENABLED = bool(enabled)
    if _FILE_HANDLER is not None:
        _FILE_HANDLER.setLevel(logging.INFO if _LOGGING_ENABLED else (logging.CRITICAL + 100))


class SensitiveDataFilter(logging.Filter):
    # scrub tokens and passwords from logs
    PATTERNS = [
        (re.compile(r"(etp_rt=)[^&;\s\"']+", re.IGNORECASE), r"\1***"),
        (re.compile(r"(Authorization:\s*Bearer\s+)[^\s\"']+", re.IGNORECASE), r"\1***"),
        (re.compile(r"(Bearer\s+)[A-Za-z0-9\-_.~+/=]{20,}", re.IGNORECASE), r"\1***"),
        (re.compile(r'("password"\s*:\s*")[^"]*(")', re.IGNORECASE), r'\1***\2'),
        (re.compile(r'(password=)[^&\s]+', re.IGNORECASE), r'\1***'),
        (re.compile(r'("android_access_token"\s*:\s*")[^"]*(")', re.IGNORECASE), r'\1***\2'),
        (re.compile(r'(android_access_token=)[^&\s]+', re.IGNORECASE), r'\1***'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern, repl in self.PATTERNS:
                record.msg = pattern.sub(repl, record.msg)
        if record.args:
            if isinstance(record.args, dict):
                sanitized_dict = {}
                for k, v in record.args.items():
                    if isinstance(v, str):
                        for pattern, repl in self.PATTERNS:
                            v = pattern.sub(repl, v)
                    sanitized_dict[k] = v
                record.args = sanitized_dict
            elif isinstance(record.args, tuple):
                sanitized_list = []
                for a in record.args:
                    if isinstance(a, str):
                        for pattern, repl in self.PATTERNS:
                            a = pattern.sub(repl, a)
                    sanitized_list.append(a)
                record.args = tuple(sanitized_list)
        return True


def get_default_log_dir() -> str:
    # %LOCALAPPDATA% on windows, ~/.config on linux, ~/Library/Logs on mac
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            log_dir = os.path.join(base, "crunchyroller", "logs")
        else:
            log_dir = os.path.join(os.path.expanduser("~"), ".crunchyroller", "logs")
    elif sys.platform == "darwin":
        log_dir = os.path.join(os.path.expanduser("~"), "Library", "Logs", "crunchyroller")
    else:
        xdg_state = os.environ.get("XDG_STATE_HOME")
        if xdg_state:
            log_dir = os.path.join(xdg_state, "crunchyroller", "logs")
        else:
            xdg_config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
            log_dir = os.path.join(xdg_config, "crunchyroller", "logs")

    try:
        os.makedirs(log_dir, exist_ok=True)
        test_file = os.path.join(log_dir, ".perm_test")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return log_dir
    except (OSError, PermissionError):
        pass

    try:
        cwd = os.getcwd()
        test_file = os.path.join(cwd, ".perm_test")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return cwd
    except (OSError, PermissionError):
        pass

    import tempfile
    return tempfile.gettempdir()


def get_log_path() -> str:
    global _RESOLVED_LOG_PATH
    if _RESOLVED_LOG_PATH:
        return _RESOLVED_LOG_PATH
    log_dir = get_default_log_dir()
    _RESOLVED_LOG_PATH = os.path.join(log_dir, _LOG_FILE_NAME)
    return _RESOLVED_LOG_PATH


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    force: bool = False,
    enabled: Optional[bool] = None,
) -> str:
    global _LOGGER_INITIALIZED, _RESOLVED_LOG_PATH, _LOGGING_ENABLED, _FILE_HANDLER
    if enabled is not None:
        _LOGGING_ENABLED = bool(enabled)

    if _LOGGER_INITIALIZED and _RESOLVED_LOG_PATH and not force and log_file is None:
        if _FILE_HANDLER is not None:
            _FILE_HANDLER.setLevel(level if _LOGGING_ENABLED else (logging.CRITICAL + 100))
        return _RESOLVED_LOG_PATH

    path = log_file or get_log_path()
    _RESOLVED_LOG_PATH = path

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in list(root_logger.handlers):
        if getattr(handler, "_crunchyroller_handler", False):
            root_logger.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sensitive_filter = SensitiveDataFilter()

    try:
        file_handler = logging.FileHandler(path, mode="a", encoding="utf-8")
        file_handler.setLevel(level if _LOGGING_ENABLED else (logging.CRITICAL + 100))
        file_handler.setFormatter(formatter)
        file_handler.addFilter(sensitive_filter)
        file_handler._crunchyroller_handler = True
        root_logger.addHandler(file_handler)
        _FILE_HANDLER = file_handler
    except (OSError, PermissionError) as e:
        print(f"Warning: Failed to create file handler at {path}: {e}", file=sys.stderr)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sensitive_filter)
    console_handler._crunchyroller_handler = True
    root_logger.addHandler(console_handler)

    _LOGGER_INITIALIZED = True
    if _LOGGING_ENABLED:
        logging.getLogger("crunchyroll").info("Crunchyroller logger initialized. Log file: %s", path)
    return path
