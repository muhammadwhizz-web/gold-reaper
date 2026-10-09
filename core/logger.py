"""
GOLD REAPER :: Logger — console blood-red, file silent.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"
BOLD = "\033[1m"


def _supports_color() -> bool:
    import sys
    if os_name := (__import__("os").name == "nt"):
        try:
            import colorama  # noqa: F401
            return True
        except ImportError:
            return False
    return sys.stdout.isatty()


def setup_logger(log_file: Path, name: str = "reaper") -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    log.addHandler(ch)
    log.propagate = False
    return log


def cprint(text: str, color: str = RESET) -> None:
    if _supports_color():
        print(f"{color}{text}{RESET}", flush=True)
    else:
        print(text, flush=True)


def banner() -> str:
    return f"""{RED}{BOLD}
   ▄████  ██▀███  ▓█████ ▄▄▄       ██ ▄█▀▓█████  ██▀███  ▄▄▄█████▓
  ██▒ ▀█▒▓██ ▒ ██▒▓█   ▀▒████▄     ██▄█▒ ▓█   ▀ ▓██ ▒ ██▒▓  ██▒ ▓▒
 ▒██░▄▄▄░▓██ ░▄█ ▒▒███  ▒██  ▀█▄  ▓███▄░ ▒███   ▓██ ░▄█ ▒▒ ▓██░ ▒░
 ░▓█  ██▓▒██▀▀█▄  ▒▓█  ▄░██▄▄▄▄██ ▓██ █▄ ▒▓█  ▄ ▒██▀▀█▄  ░ ▓██▓ ░
 ░▒▓███▀▒░██▓ ▒██▒░▒████▒▓█   ▓██▒▒██▒ █▄░▒████▒░██▓ ▒██▒  ▒██▒ ░
{RESET}{GRAY}   g o l d · r e a p e r :: xau/usd autonomous hunter :: v1.0{RESET}"""
