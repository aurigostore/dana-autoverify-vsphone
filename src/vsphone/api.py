"""
Client tipis untuk OpenAPI vsphone. Hanya endpoint yang dipakai bot ini.

Path di doc contoh: /vsphone/api/padApi/...
Path di openapi.yaml: /vcpcloud/api/padApi/...  (host api.vmoscloud.com)
Keduanya menunjuk platform yang sama. Default pakai /vsphone/... + api.vsphone.com;
kalau kena 404, coba ganti PATH_PREFIX.
"""
from __future__ import annotations

from typing import Any

import requests

from .signer import build_headers, serialize

PATH_PREFIX = "/vsphone/api/padApi"


class VsphoneAPI:
    def __init__(self, base_url: str, access_key: str, secret_key: str,
                 timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.ak = access_key
        self.sk = secret_key
        self.timeout = timeout

    def _post(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        path = f"{PATH_PREFIX}/{name}"
        raw = serialize(body)
        headers = build_headers(self.sk, self.ak, path, raw)
        resp = requests.post(self.base_url + path, data=raw.encode("utf-8"),
                             headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (200, 0, None):
            raise RuntimeError(f"{name} gagal: code={data.get('code')} msg={data.get('msg')}")
        return data

    # --- ADB ---------------------------------------------------------------
    def open_online_adb(self, pad_codes: list[str], enable: bool = True) -> dict:
        return self._post("openOnlineAdb", {
            "padCodes": pad_codes,
            "openStatus": 1 if enable else 0,
        })

    def get_adb(self, pad_code: str, enable: bool = True,
                expire_minutes: int = 1440) -> dict:
        """Return dict data: command, key, adb, expireTime, ..."""
        out = self._post("adb", {
            "padCode": pad_code,
            "enable": enable,
            "expireMinutes": expire_minutes,
        })
        return out.get("data", out)

    # --- Input (fallback kalau ADB langsung tidak dipakai) ----------------
    def simulate_touch(self, pad_codes: list[str], positions: list[str],
                       width: int, height: int, point_count: int = 1) -> dict:
        return self._post("simulateTouch", {
            "padCodes": pad_codes,
            "positions": positions,
            "width": width,
            "height": height,
            "pointCount": point_count,
        })

    def input_text(self, pad_codes: list[str], text: str) -> dict:
        return self._post("inputText", {"padCodes": pad_codes, "text": text})

    def start_app(self, pad_codes: list[str], pkg_name: str) -> dict:
        return self._post("startApp", {"padCodes": pad_codes, "pkgName": pkg_name})

    def get_preview_url(self, pad_codes: list[str], fmt: str = "jpg",
                        quality: int = 80, width: str | None = None,
                        height: str | None = None) -> dict:
        body: dict[str, Any] = {"padCodes": pad_codes, "format": fmt, "quality": quality}
        if width:
            body["width"] = width
        if height:
            body["height"] = height
        return self._post("getLongGenerateUrl", body)
