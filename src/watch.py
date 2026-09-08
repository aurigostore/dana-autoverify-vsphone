r"""
watch.py - auto-approve verifikasi login/pembayaran DANA (CapCut) di device vsphone.

Alur yang ditangani (paket id.dana, Activity PushVerifyActivity):
  1. "Login Verification"      -> tap VERIFY   (JANGAN REJECT)
  2. "Beware of the scam..."   -> tap CONTINUE (JANGAN CANCEL)  [id: btnSubmit]
  3. "Login Request Approved"  -> tap GOT IT                     [id: btn_primary_vertical_action]
  4. "Verification Approved"   -> selesai, balik idle

Layar 1 ber-animasi permanen (LogoProgressView) -> uiautomator tak bisa dump-nya;
untuk itu ada jalur BLIND: tap titik (528,1204) yang di ketiga layar flow adalah
tombol "lanjut" (VERIFY/CONTINUE/GOT IT), bukan REJECT/CANCEL.

Pemakaian:
  python src\watch.py               # header + MENU (1 cek koneksi, 2 jalankan, 0 keluar)
  python src\watch.py --no-menu     # langsung jalan tanpa menu
  python src\watch.py --dry-run     # deteksi saja, tidak nge-tap
  python src\watch.py --once        # berhenti setelah 1 alur selesai
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vsphone.logger import header, log, menu, set_logfile     # noqa: E402
from vsphone.session import (count_devices, get_devices,      # noqa: E402
                             load_config, open_tunnel)
from vsphone.uidump import (UiDumper, any_text, button,       # noqa: E402
                            has_rid, parse_xml)

APP_NAME = "DANA AutoVerify"
AUTHOR = "aurigostore"
LOGDIR = ROOT / "logs"
SCREENDIR = LOGDIR / "screens"


class TunnelDead(Exception):
    """Tunnel/adb tidak bisa dipakai lagi -> perlu rebuild dari luar."""

# label tombol yang HARAM disentuh, apa pun kondisinya
FORBIDDEN = {"reject", "cancel", "tolak", "batal"}


# --- klasifikasi layar -------------------------------------------------------
# STRICT: sebuah layar aksi hanya dianggap valid kalau TOMBOL aksinya benar-benar
# ada di dump. `uiautomator` di image ini sering mengembalikan dump basi/parsial
# (teks judul flow lama masih nyangkut tapi tombolnya tidak ada) -> kalau cuma
# cek teks, bot salah aksi & loop. Dump basi -> None -> ditangani jalur blind.
def classify(nodes) -> str | None:
    if (has_rid(nodes, "bsFrictionWarning")
            and (has_rid(nodes, "btnSubmit") or any_text(nodes, "Beware of the scam"))):
        return "scam"
    if has_rid(nodes, "btn_primary_vertical_action") and any_text(nodes, "Login Request Approved"):
        return "approved"
    if _verify_button(nodes) is not None and (
            has_rid(nodes, "push_verify_standard_layout")
            or has_rid(nodes, "tv_push_verify_title")
            or any_text(nodes, "Login Verification")
            or any_text(nodes, "Request to login")):
        return "verify"
    if (has_rid(nodes, "tvSummaryTitle") or any_text(nodes, "Verification Approved")) \
            and not (has_rid(nodes, "tv_push_verify_title")
                     or has_rid(nodes, "bsFrictionWarning")
                     or has_rid(nodes, "btn_action")):
        return "done"
    return None


def _verify_button(nodes):
    """Cari FrameLayout clickable id=btn_action yang MEWAKILI VERIFY (bukan REJECT).
    REJECT & VERIFY dua-duanya id=btn_action -> pembeda tunggal: teksnya."""
    for n in nodes:
        if n.rid_short == "btn_action" and n.clickable:
            sub = (n.label + " " + " ".join(
                c.label for c in nodes if c.parent is n or
                (c.parent is not None and c.parent.parent is n))).lower()
            if "verify" in sub and "reject" not in sub:
                return n
    return None


def resolve_verify(nodes):
    return _verify_button(nodes) or button(nodes, "VERIFY")


import re as _re


def sig(screen: str, nodes) -> str:
    """Tanda pengenal 'instance' layar untuk debounce/dedup.
    Buang angka & timer (mis. 'Expired in 04:20') supaya tidak berubah tiap detik."""
    labels = set()
    for n in nodes:
        t = _re.sub(r"\d", "", n.label).strip()
        if len(t) > 3:
            labels.add(t)
    return f"{screen}:{hash('|'.join(sorted(labels))) & 0xffffff}"


class Watcher:
    def __init__(self, dev, cfg, dry_run: bool, once: bool,
                 worker_id: int | None = None, stop_event=None):
        self.dev = dev
        self.ui = UiDumper(dev)
        self.tag = cfg.get("pad_code") or "device"
        self.wid = worker_id
        self.stop_event = stop_event
        self.cfg = cfg.get("dana", {})
        self.pkg = self.cfg.get("package", "id.dana")
        self.poll = float(self.cfg.get("poll_interval_sec", 0.8))
        self.require_text = self.cfg.get("require_text", ["CapCut"])
        self.dry_run = dry_run
        self.once = once
        self.acted_sig = None
        self.acted_at = 0.0
        self.seen_sigs: set[str] = set()
        self.flows_done = 0
        self._last_act = ""
        self._blind_n = 0
        self._blind_at = 0.0

    def _log(self, level: str, message: str) -> None:
        log(level, self.tag, message, self.wid)

    def _stopping(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _screen_prefix(self) -> str:
        return f"W{self.wid}_" if self.wid else ""

    # -- util ----------------------------------------------------------------
    def tap(self, node, what: str) -> None:
        if node is None:
            self._log("WARNING", f"tombol '{what}' tak ditemukan di UI (dump disimpan)")
            return
        if node.label.strip().lower() in FORBIDDEN:
            self._log("ERROR", f"BATAL: '{what}' malah mengarah ke tombol terlarang "
                      f"'{node.label}'")
            return
        x, y = node.center
        info = f"{node.label or what} ({x},{y}) id={node.rid_short or '-'}"
        if self.dry_run:
            self._log("DEBUG", f"[dry-run] akan tap {info}")
            return
        self.dev.adb("shell", "input", "tap", str(x), str(y))
        self._log("PROCESS", f"tap {info}")

    def _grab_stuck(self, act: str) -> None:
        """uiautomator mentok di 'done' padahal mungkin layar verify sudah muncul.
        Rekam kondisi window sebenarnya (tanpa uiautomator) buat analisa."""
        try:
            SCREENDIR.mkdir(parents=True, exist_ok=True)
            parts = [f"activity(focus) = {act}", ""]
            for label, cmd in (
                ("dumpsys activity top", ["shell", "dumpsys", "activity", "top"]),
                ("dumpsys window windows", ["shell", "dumpsys", "window", "windows"]),
            ):
                try:
                    parts.append(f"===== {label} =====")
                    parts.append(self.dev.adb(*cmd, timeout=15))
                except RuntimeError as e:
                    parts.append(f"(gagal: {e})")
                parts.append("")
            fn = SCREENDIR / f"stuck_{self._screen_prefix()}{dt.datetime.now():%H%M%S}.txt"
            fn.write_text("\n".join(parts), encoding="utf-8")
            self._log("DEBUG", f"kondisi window direkam -> logs/screens/{fn.name}")
        except Exception as e:
            self._log("DEBUG", f"_grab_stuck gagal: {e!r}")

    def _tunnel_ok(self) -> bool:
        try:
            if hasattr(self.dev, "alive") and not self.dev.alive:
                return False
            self.dev.adb("get-state", timeout=8)
            return True
        except Exception:
            return False

    def save_screen(self, screen: str, xml: str, nodes) -> None:
        s = sig(screen, nodes)
        if s in self.seen_sigs:
            return
        self.seen_sigs.add(s)
        SCREENDIR.mkdir(parents=True, exist_ok=True)
        base = f"{screen}_{self._screen_prefix()}{dt.datetime.now():%H%M%S}"
        (SCREENDIR / f"{base}.xml").write_text(xml, encoding="utf-8")
        lines = [str(n) for n in nodes if n.label or n.resource_id]
        (SCREENDIR / f"{base}.txt").write_text("\n".join(lines), encoding="utf-8")
        self._log("DEBUG", f"layar '{screen}' baru, struktur disimpan (logs/screens/{base}.txt)")

    # -- loop --------------------------------------------------------------
    def run(self) -> None:
        mode = "dry-run" if self.dry_run else "aktif"
        self._log("INFO", f"watch mulai (mode {mode}, poll {self.poll}s, "
                  f"alur selesai: {self.flows_done})")
        try:
            self.ui.kill_uiautomator()  # bersihkan zombie dari run sebelumnya
        except Exception:
            pass
        self._log("INFO", "menunggu popup verifikasi DANA...")
        consec_fail = 0
        last_beat = time.time()
        last_screen_log = None
        while not self._stopping():
            time.sleep(self.poll)

            # heartbeat tiap 60 dtk supaya kelihatan bot masih hidup
            if time.time() - last_beat > 60:
                last_beat = time.time()
                self._log("DEBUG", f"heartbeat (alur {self.flows_done}, "
                          f"layar {last_screen_log}, "
                          f"act {self._last_act.split('/')[-1] or '?'}, "
                          f"tunnel {'ok' if self._tunnel_ok() else 'MATI'})")

            # activity SELALU fresh (dumpsys window), tidak nunggu idle
            act = ""
            try:
                act = self.ui.current_activity()
            except RuntimeError:
                pass
            self._last_act = act

            xml, nodes = "", []
            try:
                xml = self.ui.dump_xml()
                nodes = parse_xml(xml)
                consec_fail = 0
            except RuntimeError as e:
                consec_fail += 1
                if consec_fail <= 2 or consec_fail % 12 == 0:
                    self._log("WARNING", f"dump UI gagal {consec_fail}x "
                              f"(act {act.split('/')[-1]}): {str(e)[:150]}")
                if consec_fail >= 12 and not self._tunnel_ok():
                    raise TunnelDead("tunnel mati saat dump")
                if consec_fail >= 120:
                    raise TunnelDead("dump gagal terus walau tunnel 'hidup' - reconnect paksa")
                # JANGAN continue: biar jalur BLIND di bawah menangani kalau kita
                # sedang di activity flow push-verify.

            screen = classify(nodes)
            pkg_ok = (self.pkg in act) or (bool(nodes) and nodes[0].package == self.pkg)
            in_flow = "PushVerify" in act and "PushVerifyResultActivity" not in act
            # layar verify punya animasi terus-menerus -> uiautomator SELALU gagal
            # 'could not get idle state'. Kalau kita di activity flow tapi dump tak
            # bisa dipakai, anggap ada layar aksi yang tak terbaca.

            act_short = act.split("/")[-1]
            if screen != last_screen_log or getattr(self, "_last_act_short", None) != act_short:
                if screen or "PushVerify" in act:
                    self._log("DEBUG", f"layar={screen} act={act_short} "
                              f"pkg_ok={pkg_ok} nodes={len(nodes)}")
                last_screen_log = screen
                self._last_act_short = act_short

            # ================= JALUR PRESISI (uiautomator kasih layar valid) =====
            if screen in ("verify", "scam", "approved"):
                self._blind_n = 0
                s = sig(screen, nodes)
                if s == self.acted_sig and (time.time() - self.acted_at) < 8:
                    continue  # baru di-tap, tunggu transisi
                if not pkg_ok:
                    self._log("WARNING", f"layar '{screen}' tapi foreground bukan {self.pkg}")
                    continue
                self.save_screen(screen, xml, nodes)
                if screen == "verify":
                    if self.require_text and not all(
                            any_text(nodes, t) for t in self.require_text):
                        self._log("WARNING", f"lewati 'verify': teks {self.require_text} "
                                  f"tak ada (bukan permintaan kita?)")
                        continue
                    self._log("PROCESS", "layar Login Verification -> VERIFY")
                    self.tap(resolve_verify(nodes), "VERIFY")
                elif screen == "scam":
                    self._log("PROCESS", "layar Beware of the scam -> CONTINUE")
                    self.tap(button(nodes, "CONTINUE", rid_suffix="btnSubmit"), "CONTINUE")
                else:
                    self._log("PROCESS", "layar Login Request Approved -> GOT IT")
                    self.tap(button(nodes, "GOT IT", rid_suffix="btn_primary_vertical_action"),
                             "GOT IT")
                self.acted_sig = s
                self.acted_at = time.time()
                continue

            # ================= SELESAI ==========================================
            if screen == "done":
                self._blind_n = 0
                if self.acted_sig is not None:
                    self.flows_done += 1
                    self._log("SUCCESS", f"verifikasi #{self.flows_done} selesai "
                              "(Verification Approved)")
                    self.acted_sig = None
                    if self.once:
                        self._log("INFO", "mode --once: selesai")
                        return
                continue

            # ================= JALUR BLIND ======================================
            # dump tak menghasilkan layar valid. Kalau kita ada di activity flow
            # push-verify, hampir pasti ada layar verify/scam/approved yang tak
            # terbaca. Tombol "lanjut" (VERIFY / CONTINUE / GOT IT) ketiganya
            # menutupi titik (528,1204); REJECT & CANCEL TIDAK. Jadi tap di situ
            # aman menggerakkan alur, dan tap nyata ini juga membangunkan
            # uiautomator utk poll berikutnya.
            if in_flow and pkg_ok:
                now = time.time()
                gap = 3.0 if self._blind_n < 8 else 8.0
                if now - self._blind_at > gap:
                    self._blind_at = now
                    self._blind_n += 1
                    if self._blind_n == 1:
                        self._grab_stuck(act)
                        self._log("WARNING", "UI tak terbaca di layar flow -> BLIND "
                                  "advance (tap titik-lanjut 528,1204; REJECT/CANCEL aman)")
                    elif self._blind_n % 4 == 0:
                        self._log("WARNING", f"blind advance x{self._blind_n} (act {act_short})")
                    if not self.dry_run:
                        try:
                            self.dev.adb("shell", "input", "tap", "528", "1204")
                        except RuntimeError:
                            pass
                    if self._blind_n == 20:
                        self._log("ERROR", "blind advance 20x tanpa 'done' - "
                                  "kemungkinan macet, tetap coba tapi pelan")
                continue

            # ================= IDLE =============================================
            self._blind_n = 0
            if self.acted_sig is not None and (time.time() - self.acted_at) > 20:
                self.acted_sig = None

        self._log("INFO", "watch berhenti")


def check_connection(devices: list[dict]) -> None:
    """Menu 1: tes tunnel + info tiap device."""
    if not devices or not devices[0].get("pad_code"):
        log("ERROR", "-", "belum ada device aktif di config")
        return
    for dcfg in devices:
        wid = dcfg["worker_id"]
        tag = dcfg.get("pad_code") or f"device{wid}"
        if not dcfg.get("pad_code"):
            log("ERROR", tag, "pad_code kosong", wid)
            continue
        log("PROCESS", tag, "menghubungkan (SSH tunnel + adb)...", wid)
        try:
            dev = open_tunnel(dcfg)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log("ERROR", tag, f"gagal konek: {e}", wid)
            continue
        try:
            rel = dev.adb("shell", "getprop", "ro.build.version.release").strip()
            sdk = dev.adb("shell", "getprop", "ro.build.version.sdk").strip()
            model = dev.adb("shell", "getprop", "ro.product.model").strip()
            pkgs = dev.adb("shell", "pm", "list", "packages", "id.dana")
            dana = "terpasang" if "package:id.dana" in pkgs else "TIDAK ADA"
            try:
                w, h = UiDumper(dev).screen_size()
                res = f"{w}x{h}"
            except RuntimeError:
                res = w = h = "?"
            log("SUCCESS", tag, f"tunnel OK - adb {dev.adb_address}, "
                f"Android {rel} (SDK {sdk}), {model or '?'}, layar {res}", wid)
            log("INFO", tag, f"aplikasi DANA (id.dana): {dana}", wid)
            if (w, h) != (720, 1280):
                log("WARNING", tag, f"resolusi {res} != 720x1280 - koordinat BLIND "
                    "(528,1204) mungkin meleset di device ini", wid)
        except Exception as e:
            log("ERROR", tag, f"gagal baca info device: {e}", wid)
        finally:
            try:
                dev.stop()
            except Exception:
                pass


def _device_worker(dcfg: dict, *, dry_run: bool, once: bool, stop) -> None:
    """Supervisor 1 device: reconnect otomatis kalau tunnel putus."""
    wid = dcfg["worker_id"]
    tag = dcfg.get("pad_code") or f"device{wid}"
    flows_done = 0
    backoff = 5
    while not stop.is_set():
        try:
            dev = open_tunnel(dcfg)
        except Exception as e:
            log("RETRY", tag, f"gagal buka tunnel: {e!r} - coba lagi {backoff}s", wid)
            if stop.wait(backoff):
                return
            backoff = min(backoff * 2, 120)
            continue
        backoff = 5

        w = Watcher(dev, dcfg, dry_run=dry_run, once=once,
                    worker_id=wid, stop_event=stop)
        w.flows_done = flows_done
        normal_exit = False
        try:
            w.run()
            normal_exit = True  # --once selesai, atau stop diminta
        except TunnelDead as e:
            log("RETRY", tag, f"{e} - reconnect...", wid)
        except Exception as e:
            log("ERROR", tag, f"error tak terduga: {e!r} - reconnect...", wid)
        finally:
            flows_done = w.flows_done
            try:
                dev.stop()
            except Exception:
                pass
        if normal_exit or stop.wait(3):
            return


def run_bot(devices: list[dict], *, dry_run: bool, once: bool) -> None:
    """Menu 2: jalankan device terpilih, 1 thread per device."""
    if not devices or not devices[0].get("pad_code"):
        log("ERROR", "-", "belum ada device aktif di config")
        return
    stop = threading.Event()
    threads = []
    for dcfg in devices:
        t = threading.Thread(target=_device_worker, kwargs=dict(
            dcfg=dcfg, dry_run=dry_run, once=once, stop=stop),
            name=f"W{dcfg['worker_id']}", daemon=True)
        t.start()
        threads.append(t)
    log("INFO", "-", f"{len(threads)} device jalan"
        + ("" if not (dry_run or once) else f" (dry_run={dry_run}, once={once})")
        + ". Ctrl+C untuk berhenti.")
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        log("INFO", "-", "menghentikan semua device...")
        stop.set()
        raise
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=25)
    log("INFO", "-", "semua device selesai.")


def _print_menu(devices: list[dict], total: int) -> None:
    wids = "+".join(f"W{d['worker_id']}" for d in devices) or "-"
    aktif = f"{len(devices)}/{total} device ({wids})"
    menu("MENU UTAMA", [
        ("1", "Cek koneksi / config", f"tes {aktif}"),
        ("2", "Jalankan bot (auto-verify)", f"{aktif}, jalan terus"),
        ("0", "Keluar", ""),
    ])


def main() -> None:
    ap = argparse.ArgumentParser(description="Auto-verifikasi DANA (CapCut) di vsphone")
    ap.add_argument("--dry-run", action="store_true",
                    help="deteksi + log saja, tidak nge-tap; skip menu")
    ap.add_argument("--once", action="store_true",
                    help="keluar setelah 1 alur verifikasi selesai; skip menu")
    ap.add_argument("--no-menu", action="store_true",
                    help="langsung jalankan bot tanpa menu")
    ap.add_argument("--devices", metavar="LIST",
                    help="pilih device: nomor (mis. 1,3) atau pad_code, "
                         "dipisah koma. Menimpa flag 'enabled' di config.")
    args = ap.parse_args()

    set_logfile(LOGDIR / f"watch_{dt.date.today():%Y%m%d}.log")
    cfg = load_config()
    select = [s for s in args.devices.split(",") if s.strip()] if args.devices else None
    devices = get_devices(cfg, select)
    total = count_devices(cfg)
    dev_ids = ", ".join(f"W{d['worker_id']}:{d.get('pad_code') or '?'}" for d in devices)
    header(APP_NAME, AUTHOR, [
        ("Engine", "OpenAPI vsphone + ADB tunnel"),
        ("Device", f"{len(devices)}/{total} aktif  ({dev_ids})"),
        ("Poll", f"{cfg.get('dana', {}).get('poll_interval_sec', 0.8)}s"),
    ])

    if args.dry_run or args.once or args.no_menu:
        try:
            run_bot(devices, dry_run=args.dry_run, once=args.once)
        except KeyboardInterrupt:
            log("INFO", "-", "dihentikan (Ctrl+C)")
        return

    while True:
        _print_menu(devices, total)
        try:
            choice = input("Pilihan: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            log("INFO", "-", "keluar")
            return
        print()
        if choice == "1":
            try:
                check_connection(devices)
            except KeyboardInterrupt:
                print()
                log("INFO", "-", "dibatalkan")
        elif choice == "2":
            try:
                run_bot(devices, dry_run=False, once=False)
            except KeyboardInterrupt:
                print()
                log("INFO", "-", "bot dihentikan, kembali ke menu")
        elif choice in ("0", "q", "exit"):
            log("INFO", "-", "keluar")
            return
        else:
            log("WARNING", "-", f"pilihan '{choice}' tidak dikenal")
        print()


if __name__ == "__main__":
    main()
