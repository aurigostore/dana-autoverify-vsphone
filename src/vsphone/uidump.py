"""
Ambil & parse UI hierarchy dari device via adb (uiautomator).

Node -> dict: text, resource-id, content-desc, class, package, clickable, bounds,
dan center (x, y) hasil hitung dari bounds.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass
class Node:
    text: str = ""
    resource_id: str = ""
    content_desc: str = ""
    cls: str = ""
    package: str = ""
    clickable: bool = False
    enabled: bool = False
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    parent: "Node | None" = field(default=None, repr=False, compare=False)

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def label(self) -> str:
        return (self.text or self.content_desc).strip()

    @property
    def rid_short(self) -> str:
        return self.resource_id.split("/", 1)[-1] if self.resource_id else ""

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bounds
        return max(0, x2 - x1) * max(0, y2 - y1)

    def clickable_target(self) -> "Node | None":
        """Node ini kalau clickable, kalau tidak naik ke ancestor clickable pertama."""
        n: Node | None = self
        while n is not None:
            if n.clickable and n.area > 0:
                return n
            n = n.parent
        return None

    def __str__(self) -> str:
        return (f"{self.label!r:<28} id={self.resource_id or '-'} "
                f"cls={self.cls.split('.')[-1]} clk={int(self.clickable)} "
                f"bounds={self.bounds} center={self.center}")


def _parse_bounds(s: str) -> tuple[int, int, int, int]:
    m = _BOUNDS_RE.search(s or "")
    return tuple(int(g) for g in m.groups()) if m else (0, 0, 0, 0)


def parse_xml(xml_text: str) -> list[Node]:
    """Flatten jadi list Node (pre-order), tiap Node punya .parent."""
    xml_text = xml_text.replace("\r", "").strip()
    if not xml_text.startswith("<"):
        xml_text = xml_text[xml_text.find("<"):]
    root_el = ET.fromstring(xml_text)
    out: list[Node] = []

    def rec(el, parent: Node | None) -> None:
        for child in el:
            if child.tag != "node":
                rec(child, parent)
                continue
            a = child.attrib
            n = Node(
                text=a.get("text", ""),
                resource_id=a.get("resource-id", ""),
                content_desc=a.get("content-desc", ""),
                cls=a.get("class", ""),
                package=a.get("package", ""),
                clickable=a.get("clickable") == "true",
                enabled=a.get("enabled") == "true",
                bounds=_parse_bounds(a.get("bounds", "")),
                parent=parent,
            )
            out.append(n)
            rec(child, n)

    rec(root_el, None)
    return out


class UiDumper:
    """Butuh objek yang punya .adb(*args) -> str (mis. AdbTunnel)."""

    DUMP_PATHS = ("/sdcard/window_dump.xml", "/data/local/tmp/window_dump.xml")

    def __init__(self, device):
        self.dev = device

    def kill_uiautomator(self) -> None:
        """Bunuh proses uiautomator yang nyangkut (dari percobaan dump yang
        timeout). Zombie ini mengunci instrumentation -> dump berikutnya kosong."""
        for cmd in ("pkill -9 uiautomator",
                    "pkill -9 -f uiautomator",
                    "kill -9 $(pidof uiautomator) 2>/dev/null"):
            try:
                self.dev.adb("shell", cmd, timeout=8)
            except RuntimeError:
                pass

    def dump_xml(self, retries: int = 2, delay: float = 0.4) -> str:
        """Ambil UI hierarchy yang FRESH.

        - hapus file lama dulu (anti stale-dump)
        - jalankan `uiautomator dump` dengan timeout DI SISI DEVICE supaya kalau
          macet tidak meninggalkan zombie
        - kalau gagal, bunuh zombie uiautomator lalu ulang
        - selalu coba baca file apa pun yang dilaporkan stdout
        """
        last = ""
        path = self.DUMP_PATHS[0]
        for attempt in range(retries):
            if attempt > 0:
                self.kill_uiautomator()
                time.sleep(delay)
            try:
                self.dev.adb("shell", "rm", "-f", path, timeout=8)
            except RuntimeError:
                pass
            try:
                # timeout DI SISI DEVICE (6 dtk): layar verify punya animasi terus
                # (LogoProgressView) -> 'could not get idle state' selamanya; jangan
                # buang waktu, biar jalur BLIND di watch.py yang ambil alih.
                out = self.dev.adb(
                    "shell", f"timeout 6 uiautomator dump {path} 2>&1", timeout=10)
            except RuntimeError as e:
                last = str(e)
                continue
            if "<hierarchy" in out and "</hierarchy>" in out:
                return out
            try:
                xml = self.dev.adb("shell", "cat", path, timeout=8)
            except RuntimeError as e:
                last = f"{e} | dump-stdout: {out.strip()[:140]!r}"
                continue
            if "<hierarchy" in xml and "</hierarchy>" in xml:
                return xml
            last = f"xml tak lengkap ({len(xml)}b) | dump-stdout: {out.strip()[:140]!r}"
        raise RuntimeError(f"uiautomator dump gagal {retries}x. Terakhir: {last}")

    def dump(self) -> list[Node]:
        return parse_xml(self.dump_xml())

    def current_focus(self) -> str:
        try:
            return self.dev.adb("shell", "dumpsys", "window", "windows")
        except RuntimeError:
            return self.dev.adb("shell", "dumpsys", "window")

    def current_activity(self) -> str:
        """Return 'pkg/activity' dari window yang sedang fokus, atau ''."""
        blob = self.current_focus()
        m = re.search(r"mCurrentFocus=Window\{[0-9a-f]+ u\d+ ([\w.]+/[\w.$]+)\}", blob)
        if m:
            return m.group(1)
        m = re.search(r"mFocusedApp=.*?ActivityRecord\{[0-9a-f]+ u\d+ ([\w.]+/[\w.$]+)", blob)
        if m:
            return m.group(1)
        # ambil Window terakhir yang punya bentuk pkg/activity
        cands = re.findall(r"Window\{[0-9a-f]+ u\d+ ([\w.]+/[\w.$]+)\}", blob)
        return cands[-1] if cands else ""

    def foreground_package(self) -> str:
        act = self.current_activity()
        return act.split("/", 1)[0] if act else ""

    def screen_size(self) -> tuple[int, int]:
        out = self.dev.adb("shell", "wm", "size")
        m = re.search(r"(\d+)\s*x\s*(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def find(nodes: list[Node], *, text=None, text_contains=None, rid=None,
         rid_contains=None, clickable=None) -> list[Node]:
    def ok(n: Node) -> bool:
        if text is not None and n.label.lower() != text.lower():
            return False
        if text_contains is not None and text_contains.lower() not in (
                n.text + " " + n.content_desc).lower():
            return False
        if rid is not None and n.resource_id != rid:
            return False
        if rid_contains is not None and rid_contains.lower() not in n.resource_id.lower():
            return False
        if clickable is not None and n.clickable != clickable:
            return False
        return True

    return [n for n in nodes if ok(n)]


def any_text(nodes: list[Node], needle: str) -> bool:
    n = needle.lower()
    return any(n in (x.text + " " + x.content_desc).lower() for x in nodes)


def has_rid(nodes: list[Node], rid_suffix: str) -> bool:
    s = rid_suffix.lower()
    return any(x.rid_short.lower() == s for x in nodes)


def button(nodes: list[Node], label: str, *, rid_suffix: str | None = None) -> Node | None:
    """Cari tombol berdasarkan resource-id (kalau dikasih) atau label teks persis,
    lalu kembalikan node clickable yang tepat untuk di-tap."""
    if rid_suffix:
        for n in nodes:
            if n.rid_short.lower() == rid_suffix.lower():
                return n.clickable_target() or n
    lo = label.strip().lower()
    for n in nodes:
        if n.label.lower() == lo:
            return n.clickable_target() or n
    # fallback: teks mengandung label (case tombol multi-kata dengan spasi aneh)
    for n in nodes:
        if lo in n.label.lower() and n.label.lower() != "":
            return n.clickable_target() or n
    return None
