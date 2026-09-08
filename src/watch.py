r"""
watch.py - auto-approve verifikasi login/pembayaran DANA (CapCut) di device vsphone.

Alur yang ditangani (paket id.dana, Activity PushVerifyActivity):
  1. "Login Verification"      -> tap VERIFY   (JANGAN REJECT)
  2. "Beware of the scam..."   -> tap CONTINUE (JANGAN CANCEL)  [id: btnSubmit]
  3. "Login Request Approved"  -> tap GOT IT                     [id: btn_primary_vertical_action]
  4. "Verification Approved"   -> selesai, balik idle

Pemakaian:
  python src\watch.py --dry-run     # DETEKSI saja, tidak nge-tap (WAJIB dicoba dulu)
  python src\watch.py               # mode aktif (nge-tap beneran)
  python src\watch.py --once        # berhenti setelah 1 alur selesai

Tiap layar baru yang dilihat disimpan ke logs\screens\ (buat audit + nangkap
struktur layar 1 yang belum sempat kita recon).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vsphone.session import load_config, open_tunnel          # noqa: E402
from vsphone.uidump import (UiDumper, any_text, button,       # noqa: E402
                            has_rid, parse_xml)

LOGDIR = ROOT / "logs"
SCREENDIR = LOGDIR / "screens"


class TunnelDead(Exception):
    """Tunnel/adb tidak bisa dipakai lagi -> perlu rebuild dari luar."""

# label tombol yang HARAM disentuh, apa pun kondisinya
FORBIDDEN = {"reject", "cancel", "tolak", "batal"}


def log(msg: str) -> None:
    line = f"{dt.datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    LOGDIR.mkdir(exist_ok=True)
    with (LOGDIR / f"watch_{dt.date.today():%Y%m%d}.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


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
    def __init__(self, dev, cfg, dry_run: bool, once: bool):
        self.dev = dev
        self.ui = UiDumper(dev)
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

    # -- util ----------------------------------------------------------------
    def tap(self, node, what: str) -> None:
        if node is None:
            log(f"  ! tombol '{what}' tidak ketemu di UI - dump disimpan")
            return
        if node.label.strip().lower() in FORBIDDEN:
            log(f"  !! BATAL: '{what}' resolve ke tombol terlarang '{node.label}'")
            return
        x, y = node.center
        tag = f"'{node.label or what}' @ ({x},{y}) id={node.rid_short or '-'}"
        if self.dry_run:
            log(f"  [DRY] akan tap {tag}")
            return
        self.dev.adb("shell", "input", "tap", str(x), str(y))
        log(f"  TAP {tag}")

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
            fn = SCREENDIR / f"stuck_{dt.datetime.now():%H%M%S}.txt"
            fn.write_text("\n".join(parts), encoding="utf-8")
            log(f"  (kondisi window direkam -> logs/screens/{fn.name} - kirim file ini)")
        except Exception as e:
            log(f"  (_grab_stuck gagal: {e!r})")

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
        stamp = dt.datetime.now().strftime("%H%M%S")
        (SCREENDIR / f"{screen}_{stamp}.xml").write_text(xml, encoding="utf-8")
        lines = [str(n) for n in nodes if n.label or n.resource_id]
        (SCREENDIR / f"{screen}_{stamp}.txt").write_text("\n".join(lines), encoding="utf-8")
        log(f"  (layar '{screen}' baru - struktur disimpan ke logs/screens/{screen}_{stamp}.txt)")

    # -- loop --------------------------------------------------------------
    def run(self) -> None:
        mode = "DRY-RUN (tidak nge-tap)" if self.dry_run else "AKTIF"
        log(f"watch mulai | mode={mode} | poll={self.poll}s | target pkg={self.pkg} "
            f"| alur selesai sejauh ini={self.flows_done}")
        try:
            self.ui.kill_uiautomator()  # bersihkan zombie dari run sebelumnya
        except Exception:
            pass
        log("Tunggu popup verifikasi DANA muncul...")
        consec_fail = 0
        last_beat = time.time()
        last_screen_log = None
        while True:
            time.sleep(self.poll)

            # heartbeat tiap 60 dtk supaya kelihatan bot masih hidup
            if time.time() - last_beat > 60:
                last_beat = time.time()
                log(f"heartbeat | alur={self.flows_done} | layar={last_screen_log} "
                    f"| act={self._last_act.split('/')[-1] or '?'} "
                    f"| tunnel={'ok' if self._tunnel_ok() else 'MATI'}")

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
                    log(f"dump gagal {consec_fail}x (act={act.split('/')[-1]}) :: {str(e)[:160]}")
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
                    log(f"  ~ layar={screen} act={act_short} pkg_ok={pkg_ok} nodes={len(nodes)}")
                last_screen_log = screen
                self._last_act_short = act_short

            # ================= JALUR PRESISI (uiautomator kasih layar valid) =====
            if screen in ("verify", "scam", "approved"):
                self._blind_n = 0
                s = sig(screen, nodes)
                if s == self.acted_sig and (time.time() - self.acted_at) < 8:
                    continue  # baru di-tap, tunggu transisi
                if not pkg_ok:
                    log(f"  skip: layar '{screen}' tapi foreground bukan {self.pkg}")
                    continue
                self.save_screen(screen, xml, nodes)
                if screen == "verify":
                    if self.require_text and not all(
                            any_text(nodes, t) for t in self.require_text):
                        log(f"  skip 'verify': teks {self.require_text} tak ada (bukan permintaan kita?)")
                        continue
                    log("layar 1: Login Verification -> VERIFY")
                    self.tap(resolve_verify(nodes), "VERIFY")
                elif screen == "scam":
                    log("layar 2: Beware of the scam -> CONTINUE")
                    self.tap(button(nodes, "CONTINUE", rid_suffix="btnSubmit"), "CONTINUE")
                else:
                    log("layar 3: Login Request Approved -> GOT IT")
                    self.tap(button(nodes, "GOT IT", rid_suffix="btn_primary_vertical_action"),
                             "GOT IT")
                self.acted_sig = s
                self.acted_at = time.time()
                continue

            # ================= SELESAI ==========================================
            if screen == "done":
                self._blind_n = 0
                if self.acted_sig is not None:
                    log("alur verifikasi SELESAI (Verification Approved). Nunggu lagi...")
                    self.flows_done += 1
                    self.acted_sig = None
                    if self.once:
                        log("--once: keluar.")
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
                        log("uiautomator tak bisa baca layar flow -> BLIND advance "
                            "(tap titik-lanjut 528,1204; REJECT/CANCEL tidak di situ)")
                    if self._blind_n % 4 == 0:
                        log(f"  blind advance x{self._blind_n} (act={act_short})")
                    if not self.dry_run:
                        try:
                            self.dev.adb("shell", "input", "tap", "528", "1204")
                        except RuntimeError:
                            pass
                    if self._blind_n == 20:
                        log("  !! blind advance 20x tanpa 'done' - mungkin macet, "
                            "tetap coba tapi pelan.")
                continue

            # ================= IDLE =============================================
            self._blind_n = 0
            if self.acted_sig is not None and (time.time() - self.acted_at) > 20:
                self.acted_sig = None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="deteksi + log saja, tidak nge-tap")
    ap.add_argument("--once", action="store_true",
                    help="keluar setelah satu alur verifikasi selesai")
    args = ap.parse_args()

    cfg = load_config()
    flows_done = 0
    backoff = 5
    while True:
        try:
            dev = open_tunnel(cfg)
        except KeyboardInterrupt:
            log("dihentikan (Ctrl+C).")
            return
        except Exception as e:
            log(f"gagal buka tunnel: {e!r} - coba lagi {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
            continue
        backoff = 5

        w = Watcher(dev, cfg, dry_run=args.dry_run, once=args.once)
        w.flows_done = flows_done
        try:
            w.run()
            return  # cuma balik normal kalau --once
        except KeyboardInterrupt:
            log("dihentikan (Ctrl+C).")
            return
        except TunnelDead as e:
            log(f"[supervisor] {e} - reconnect...")
        except Exception as e:
            log(f"[supervisor] error tak terduga: {e!r} - reconnect...")
        finally:
            flows_done = w.flows_done
            try:
                dev.stop()
            except Exception:
                pass
        time.sleep(3)


if __name__ == "__main__":
    main()
