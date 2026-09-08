"""Helper dipakai bersama recon.py & watch.py: baca config + buka tunnel."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .api import VsphoneAPI
from .tunnel import AdbTunnel

ROOT = Path(__file__).resolve().parent.parent.parent


def load_config() -> dict:
    p = ROOT / "config.json"
    if not p.exists():
        raise SystemExit("config.json tidak ada. Salin config.example.json -> config.json lalu isi.")
    return json.loads(p.read_text(encoding="utf-8"))


def build_tunnel(cfg: dict) -> AdbTunnel:
    """Bikin objek tunnel (belum start)."""
    panel = cfg.get("adb_panel") or {}
    adb_exe = cfg.get("adb_exe", "adb")
    key = panel.get("connect_key", "")
    if key and "PASTE" not in key:
        print("[session] pakai adb_panel dari config (tanpa API)")
        return AdbTunnel(panel["connect_command"], key, panel["adb_address"],
                         adb_exe=adb_exe)

    print("[session] adb_panel kosong -> minta koneksi ADB via API")
    api = VsphoneAPI(cfg["api"]["base_url"], cfg["api"]["access_key"],
                     cfg["api"]["secret_key"])
    api.open_online_adb([cfg["pad_code"]], enable=True)
    time.sleep(3)
    exp_min = int(cfg.get("adb_expire_minutes", 10080))  # default 7 hari
    d = api.get_adb(cfg["pad_code"], enable=True, expire_minutes=exp_min)
    print(f"[session] ADB expireTime={d.get('expireTime')}")
    return AdbTunnel(d["command"], d["key"], d["adb"], adb_exe=adb_exe)


def open_tunnel(cfg: dict) -> AdbTunnel:
    """Bikin + start tunnel, siap dipakai."""
    dev = build_tunnel(cfg)
    dev.start()
    return dev
