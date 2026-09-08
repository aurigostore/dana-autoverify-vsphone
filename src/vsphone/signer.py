"""
Signing V2 (Simplified) untuk OpenAPI vsphone / vmoscloud.

Header:
  X-Access-Key : AccessKey (ak_...)
  X-Timestamp  : unix detik, 10 digit (window +-5 menit)
  X-Sign       : sha256_hex( SecretKey + X-Timestamp + path + rawBody )

PENTING: body yang dikirim harus byte-identik dengan yang dipakai saat sign.
Karena itu kita selalu serialisasi sekali (json.dumps) lalu kirim string mentah
itu (requests data=...), bukan json=...
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any


def build_headers(secret_key: str, access_key: str, path: str, raw_body: str,
                  ts: str | None = None) -> dict[str, str]:
    ts = ts or str(int(time.time()))
    sign = hashlib.sha256(
        (secret_key + ts + path + raw_body).encode("utf-8")
    ).hexdigest()
    return {
        "X-Access-Key": access_key,
        "X-Timestamp": ts,
        "X-Sign": sign,
        "Content-Type": "application/json",
    }


def serialize(body: dict[str, Any]) -> str:
    # compact, key order dipertahankan sesuai dict insertion order
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)
