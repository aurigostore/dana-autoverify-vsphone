"""
Recon: buktikan apakah tombol verifikasi DANA bisa dideteksi lewat uiautomator.

Jalankan SAAT layar verifikasi DANA sedang terbuka di device:

    python src/recon.py                 # sekali jepret
    python src/recon.py --watch         # ulang tiap 2 dtk (Ctrl+C berhenti)

Output ke recon/<timestamp>/:
    ui_dump.xml     hierarki UI mentah
    ui_nodes.txt    daftar node ter-flatten (text | id | class | bounds | center)
    focus.txt       dumpsys window (cek FLAG_SECURE / foreground app)
    screen.png      screencap (hitam = FLAG_SECURE aktif utk screenshot)
    info.txt        ringkasan + verdict

Baca verdict di akhir: apakah teks VERIFY / CONTINUE / GOT IT kelihatan di node.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vsphone.session import (get_devices, load_config,        # noqa: E402,F401
                             open_tunnel as make_tunnel)
from vsphone.tunnel import AdbTunnel        # noqa: E402
from vsphone.uidump import UiDumper, any_text, find  # noqa: E402

INTEREST = ["VERIFY", "REJECT", "CONTINUE", "CANCEL", "GOT IT",
            "Login Verification", "Beware of the scam", "Login Request Approved",
            "Request to login to CapCut"]


def snapshot(dev: AdbTunnel, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    ui = UiDumper(dev)

    # focus / secure
    focus = ui.current_focus()
    (outdir / "focus.txt").write_text(focus, encoding="utf-8")
    try:
        fg = ui.current_activity() or ui.foreground_package()
    except RuntimeError:
        fg = ""
    secure = bool(re.search(r"\bisSecure=true|FLAG_SECURE", focus))

    # screencap
    png_ok = False
    try:
        dev.adb("shell", "screencap", "-p", "/sdcard/_recon.png")
        raw = dev.adb("pull", "/sdcard/_recon.png", str(outdir / "screen.png"))
        png = outdir / "screen.png"
        if png.exists() and png.stat().st_size > 0:
            data = png.read_bytes()
            # deteksi kasar layar hitam: PNG kecil / seragam
            png_ok = len(data) > 2000
    except RuntimeError as e:
        (outdir / "screen_error.txt").write_text(str(e), encoding="utf-8")

    # ui dump
    nodes = []
    dump_err = ""
    try:
        xml = ui.dump_xml()
        (outdir / "ui_dump.xml").write_text(xml, encoding="utf-8")
        from vsphone.uidump import parse_xml
        nodes = parse_xml(xml)
    except RuntimeError as e:
        dump_err = str(e)
        (outdir / "ui_dump_error.txt").write_text(dump_err, encoding="utf-8")

    lines = [str(n) for n in nodes if n.label or n.resource_id]
    (outdir / "ui_nodes.txt").write_text("\n".join(lines), encoding="utf-8")

    try:
        w, h = ui.screen_size()
    except RuntimeError:
        w = h = 0

    # verdict
    hits = {kw: any_text(nodes, kw) for kw in INTEREST}
    buttons = {kw: [f"{n.center} id={n.resource_id or '-'} clk={int(n.clickable)}"
                    for n in find(nodes, text_contains=kw)]
               for kw in ("VERIFY", "REJECT", "CONTINUE", "CANCEL", "GOT IT")}

    info = [
        f"waktu           : {dt.datetime.now().isoformat(timespec='seconds')}",
        f"foreground pkg  : {fg or '(tidak terdeteksi)'}",
        f"screen size     : {w}x{h}",
        f"FLAG_SECURE     : {'YA (terlihat di dumpsys)' if secure else 'tidak terlihat'}",
        f"screencap       : {'OK, ada isi' if png_ok else 'GAGAL / kemungkinan hitam'}",
        f"jumlah node UI  : {len(nodes)}",
        f"dump error      : {dump_err or '-'}",
        "",
        "== teks penting terlihat di node UI? ==",
        *[f"  {'YES' if v else ' no'}  {k}" for k, v in hits.items()],
        "",
        "== kandidat tombol (text -> center / id / clickable) ==",
        *[f"  {k:<9}: {v or '(tidak ada)'}" for k, v in buttons.items()],
        "",
        "== VERDICT ==",
    ]

    detectable = any(hits[k] for k in ("VERIFY", "CONTINUE", "GOT IT"))
    if detectable:
        info.append("  OK - minimal satu tombol target terbaca sebagai node teks.")
        info.append("  -> Jalur utama (uiautomator + input tap) LAYAK. Lanjut bikin watch.py.")
    elif nodes:
        info.append("  UI tree kebaca tapi teks tombol tidak ada.")
        info.append("  -> Cek ui_nodes.txt: mungkin tombol punya resource-id tanpa text,")
        info.append("     atau isi dialog di dalam WebView. Kirim ui_dump.xml untuk dianalisa.")
    else:
        info.append("  uiautomator TIDAK menghasilkan node di layar ini.")
        info.append("  -> Kemungkinan diblok. Perlu fallback (accessibility app / OCR / koordinat).")

    text = "\n".join(info)
    (outdir / "info.txt").write_text(text, encoding="utf-8")
    print("\n" + text + f"\n\n[recon] hasil -> {outdir}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="ulang terus tiap --interval detik")
    ap.add_argument("--step", action="store_true",
                    help="motret tiap kali tekan ENTER (kontrol manual, paling enak)")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--device", type=int, default=1,
                    help="nomor device (1..N) kalau config multi-device")
    args = ap.parse_args()

    devices = get_devices(load_config())
    if not 1 <= args.device <= len(devices):
        raise SystemExit(f"--device {args.device} di luar jangkauan (ada {len(devices)})")
    cfg = devices[args.device - 1]
    base = ROOT / "recon"

    def stamp() -> Path:
        p = base / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        i = 1
        while p.exists():
            p = base / (dt.datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{i}")
            i += 1
        return p

    with make_tunnel(cfg) as dev:
        if args.step:
            print("\n[recon] MODE STEP.")
            print("  1) Buka layar verifikasi DANA (Login Verification).")
            print("  2) Tekan ENTER di sini -> motret layar itu.")
            print("  3) Di device klik VERIFY, lalu tekan ENTER lagi -> motret layar 'Beware...'.")
            print("  4) Klik CONTINUE, tunggu, tekan ENTER -> motret 'Login Request Approved'.")
            print("  5) Selesai: ketik 'q' lalu ENTER.\n")
            n = 0
            while True:
                cmd = input(f"[{n+1}] ENTER=motret / q=keluar > ").strip().lower()
                if cmd == "q":
                    break
                n += 1
                snapshot(dev, stamp())
            print("[recon] selesai.")
            return

        if not args.watch:
            snapshot(dev, stamp())
            return

        print("[recon] mode watch. Buka layar verifikasi DANA. Ctrl+C untuk stop.")
        try:
            while True:
                snapshot(dev, stamp())
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[recon] stop.")


if __name__ == "__main__":
    main()
