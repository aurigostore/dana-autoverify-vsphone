"""Helper dipakai bersama recon.py & watch.py: baca config + buka tunnel."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .api import VsphoneAPI
from .logger import log
from .tunnel import AdbTunnel

ROOT = Path(__file__).resolve().parent.parent.parent


def load_config() -> dict:
    p = ROOT / "config.json"
    if not p.exists():
        raise SystemExit("config.json tidak ada. Salin config.example.json -> config.json lalu isi.")
    return json.loads(p.read_text(encoding="utf-8"))


# key yang dipakai bersama semua device
_SHARED = ("api", "adb_exe", "adb_expire_minutes", "dana")


def _all_devices(cfg: dict) -> list[dict]:
    """Semua device di config, ter-normalisasi, 'worker_id' 1..N ikut POSISI."""
    entries = cfg.get("devices")
    if not entries:
        entries = [{k: cfg[k] for k in ("pad_code", "adb_panel") if k in cfg}]
    out = []
    for i, e in enumerate(entries, start=1):
        d = {k: cfg[k] for k in _SHARED if k in cfg}
        d.update(e)
        d["worker_id"] = i
        out.append(d)
    return out


def get_devices(cfg: dict, select=None) -> list[dict]:
    """Device yang mau dipakai.

    - select=None  -> semua yang `enabled` (default True; `"enabled": false` di-skip)
    - select=list  -> persis yang disebut (nomor W# atau pad_code), abaikan `enabled`
    - select="all" -> semua entry apa adanya

    'worker_id' selalu ikut posisi di config (device ke-3 = W3 walau cuma dia jalan).
    """
    devs = _all_devices(cfg)
    if select == "all":
        return devs
    if select:
        want = {str(s).strip().lower() for s in select if str(s).strip()}
        chosen = [d for d in devs if str(d["worker_id"]) in want
                  or str(d.get("pad_code", "")).lower() in want]
        missing = want - {str(d["worker_id"]) for d in chosen} \
            - {str(d.get("pad_code", "")).lower() for d in chosen}
        if missing:
            raise SystemExit(f"device tidak ditemukan di config: {', '.join(sorted(missing))}")
        return chosen
    return [d for d in devs if d.get("enabled", True)]


def count_devices(cfg: dict) -> int:
    return len(_all_devices(cfg))


def build_tunnel(cfg: dict) -> AdbTunnel:
    """Bikin objek tunnel (belum start)."""
    panel = cfg.get("adb_panel") or {}
    adb_exe = cfg.get("adb_exe", "adb")
    key = panel.get("connect_key", "")
    tag = cfg.get("pad_code") or "device"
    if key and "PASTE" not in key:
        log("INFO", tag, "pakai adb_panel dari config (tanpa API)")
        return AdbTunnel(panel["connect_command"], key, panel["adb_address"],
                         adb_exe=adb_exe)

    log("INFO", tag, "adb_panel kosong -> minta koneksi ADB via API")
    api = VsphoneAPI(cfg["api"]["base_url"], cfg["api"]["access_key"],
                     cfg["api"]["secret_key"])
    api.open_online_adb([cfg["pad_code"]], enable=True)
    time.sleep(3)
    exp_min = int(cfg.get("adb_expire_minutes", 10080))  # default 7 hari
    d = api.get_adb(cfg["pad_code"], enable=True, expire_minutes=exp_min)
    log("INFO", tag, f"ADB link berlaku s/d {d.get('expireTime')}")
    return AdbTunnel(d["command"], d["key"], d["adb"], adb_exe=adb_exe)


def open_tunnel(cfg: dict) -> AdbTunnel:
    """Bikin + start tunnel, siap dipakai."""
    dev = build_tunnel(cfg)
    dev.start()
    return dev
