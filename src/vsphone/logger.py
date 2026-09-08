"""
Format log gaya aurigostore (lihat D:\\BOT\\CONSOL LOG\\LOG_FORMAT.md).

Baris:  [HH:MM:SS][LEVEL][target] pesan
- tiap elemen kurung siku sendiri, tanpa spasi/'|' di antara kurung
- warna HANYA di prefix kurung; pesan selalu putih polos
- emoji cuma untuk SUCCESS (checklist) & ERROR (silang), masuk ke kurung level
- multi-thread: sisipkan [W#] setelah timestamp; penulisan dikunci
- file .log: sama tapi kode warna ANSI dibuang
"""
from __future__ import annotations

import re
import sys
import threading
from datetime import datetime
from pathlib import Path

try:
    from colorama import Back, Fore, Style, init
    init(autoreset=True)
except ModuleNotFoundError:  # jalan tanpa warna kalau colorama belum diinstal
    class _Blank:
        def __getattr__(self, _):  # noqa: D401
            return ""
    Back = Fore = Style = _Blank()

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LEVELS = {
    "SUCCESS": ("\u2705", "SUCCESS", Fore.GREEN),
    "ERROR":   ("\u274c", "ERROR", Fore.RED),
    "WARNING": ("", "WARNING", Fore.YELLOW),
    "PROCESS": ("", "PROCESS", Fore.CYAN),
    "INFO":    ("", "INFO", Fore.BLUE),
    "RETRY":   ("", "RETRY", Fore.MAGENTA),
    "DEBUG":   ("", "DEBUG", Style.DIM),
}

_lock = threading.Lock()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_logfile: Path | None = None


def set_logfile(path: str | Path) -> None:
    global _logfile
    _logfile = Path(path)
    try:
        _logfile.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        _logfile = None


def badge(text: str) -> str:
    return f"{Back.GREEN}{Fore.BLACK}{Style.BRIGHT} {text} {Style.RESET_ALL}"


def author_badge(author: str) -> str:
    return f"{Back.LIGHTBLACK_EX}{Fore.WHITE} by @{author} {Style.RESET_ALL}"


def header(bot_name: str, author: str, info: list[tuple[str, str]]) -> None:
    width = 46
    with _lock:
        print(f"{badge(bot_name)}{author_badge(author)}")
        print(Style.DIM + "\u2500" * width + Style.RESET_ALL)
        label_width = max(len(label) for label, _ in info)
        for label, value in info:
            print(f"{Fore.WHITE}{label.ljust(label_width)} : {value}{Style.RESET_ALL}")
        print()


def menu(title: str, items: list[tuple[str, str, str]]) -> None:
    with _lock:
        print(badge(title))
        print()
        for number, label, note in items:
            note_text = f"  {Style.DIM}{note}{Style.RESET_ALL}" if note else ""
            print(f"{Fore.CYAN}{number}{Style.RESET_ALL}  "
                  f"{Fore.WHITE}{label}{Style.RESET_ALL}{note_text}")
        print()


def log(level: str, target: str, message: str, worker_id: int | None = None) -> None:
    emoji, label, color = LEVELS[level]
    now = datetime.now().strftime("%H:%M:%S")
    worker_tag = f"[W{worker_id}]" if worker_id else ""
    level_tag = f"[{emoji} {label}]" if emoji else f"[{label}]"
    prefix = f"[{now}]{worker_tag}{level_tag}[{target or '-'}]"
    with _lock:
        print(f"{color}{prefix}{Style.RESET_ALL} {Fore.WHITE}{message}{Style.RESET_ALL}",
              flush=True)
        if _logfile is not None:
            try:
                with _logfile.open("a", encoding="utf-8") as f:
                    f.write(_ANSI.sub("", f"{prefix} {message}") + "\n")
            except OSError:
                pass
