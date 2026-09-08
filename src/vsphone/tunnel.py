"""
SSH tunnel ke instance vsphone (pakai paramiko, bukan ssh.exe) + adb connect.

Kenapa paramiko: OpenSSH di Windows tidak bisa disuapi password non-interaktif
dengan andal (SSH_ASKPASS ribet). paramiko menangani password auth + local
port-forward (-L) sepenuhnya di Python, dan lebih gampang di-keep-alive / paralel.

Input:
  connect_command : string dari panel / field `command`
                    "ssh ... s@1.2.3.4 -p 1824 -L 64669:localhost:1 -Nf"
  connect_key     : field `key`. Dicoba sebagai PASSWORD lalu sebagai PRIVATE KEY.
  adb_address     : "localhost:64669"

Pemakaian:
  with AdbTunnel(cmd, key, addr) as dev:
      dev.adb("shell", "echo", "ok")
"""
from __future__ import annotations

import io
import re
import select
import shlex
import socket
import socketserver
import subprocess
import sys
import threading
import time

import paramiko


def _log(msg: str) -> None:
    print(f"[tunnel] {msg}", file=sys.stderr, flush=True)


def parse_connect_command(cmd: str) -> dict:
    toks = shlex.split(cmd)
    host = port = forward = None
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "-p" and i + 1 < len(toks):
            port = toks[i + 1]; i += 2; continue
        if t == "-L" and i + 1 < len(toks):
            forward = toks[i + 1]; i += 2; continue
        if "@" in t and not t.startswith("-") and host is None:
            host = t
        i += 1
    if not (host and forward):
        raise ValueError(f"Gagal parse connect_command: {cmd!r}")
    user, hostname = host.split("@", 1)
    lp, rhost, rport = forward.rsplit(":", 2)
    return {
        "user": user, "hostname": hostname, "port": int(port or 22),
        "local_port": int(lp), "remote_host": rhost, "remote_port": int(rport),
    }


def _load_key(raw: str):
    raw = raw.strip()
    wrapped = raw if "-----BEGIN" in raw else (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        + "\n".join(re.findall(".{1,70}", re.sub(r"\s+", "", raw)))
        + "\n-----END OPENSSH PRIVATE KEY-----\n"
    )
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(wrapped))
        except Exception:
            continue
    return None


class _Handler(socketserver.BaseRequestHandler):
    transport = None
    dest = ("", 0)

    def handle(self):
        try:
            chan = self.transport.open_channel(
                "direct-tcpip", self.dest, self.request.getpeername())
        except Exception as e:
            _log(f"forward channel gagal: {e}")
            return
        if chan is None:
            return
        sock = self.request
        try:
            while True:
                r, _, _ = select.select([sock, chan], [], [], 1.0)
                if sock in r:
                    data = sock.recv(4096)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(4096)
                    if not data:
                        break
                    sock.sendall(data)
        except (OSError, EOFError):
            pass
        finally:
            chan.close()
            sock.close()


class _ForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class AdbTunnel:
    def __init__(self, connect_command: str, connect_key: str, adb_address: str,
                 adb_exe: str = "adb", **_ignore):
        self.info = parse_connect_command(connect_command)
        self.key_raw = connect_key.strip()
        self.adb_address = adb_address.replace("adb connect", "").strip()
        self.adb_exe = adb_exe
        self._client: paramiko.SSHClient | None = None
        self._fwd: _ForwardServer | None = None
        self._fwd_thread: threading.Thread | None = None

    def __enter__(self) -> "AdbTunnel":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- lifecycle --------------------------------------------------------
    def _connect_ssh(self) -> paramiko.SSHClient:
        i = self.info
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        errs = []

        # 1) password
        try:
            cli.connect(i["hostname"], port=i["port"], username=i["user"],
                        password=self.key_raw, allow_agent=False,
                        look_for_keys=False, timeout=15, banner_timeout=15,
                        auth_timeout=15)
            _log("auth OK via password")
            return cli
        except paramiko.AuthenticationException as e:
            errs.append(f"password: {e}")
        except Exception as e:
            errs.append(f"password: {type(e).__name__}: {e}")

        # 2) private key
        pkey = _load_key(self.key_raw)
        if pkey is not None:
            try:
                cli.connect(i["hostname"], port=i["port"], username=i["user"],
                            pkey=pkey, allow_agent=False, look_for_keys=False,
                            timeout=15)
                _log("auth OK via private key")
                return cli
            except Exception as e:
                errs.append(f"pkey: {type(e).__name__}: {e}")
        else:
            errs.append("pkey: connect_key bukan format private key yang dikenal")

        raise RuntimeError("SSH auth gagal:\n  " + "\n  ".join(errs))

    def start(self) -> None:
        if self.alive:
            return  # idempotent
        self._client = self._connect_ssh()
        transport = self._client.get_transport()
        transport.set_keepalive(20)

        _Handler.transport = transport
        _Handler.dest = (self.info["remote_host"], self.info["remote_port"])
        self._fwd = _ForwardServer(("127.0.0.1", self.info["local_port"]), _Handler)
        self._fwd_thread = threading.Thread(target=self._fwd.serve_forever, daemon=True)
        self._fwd_thread.start()
        _log(f"forward aktif 127.0.0.1:{self.info['local_port']} -> "
             f"{self.info['remote_host']}:{self.info['remote_port']}")

        self._adb_connect()

    def _adb_connect(self, retries: int = 12) -> None:
        subprocess.run([self.adb_exe, "start-server"], capture_output=True, text=True)
        for _ in range(retries):
            r = subprocess.run([self.adb_exe, "connect", self.adb_address],
                               capture_output=True, text=True)
            out = (r.stdout + r.stderr).lower()
            if "connected" in out or "already" in out:
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"adb connect {self.adb_address} gagal")
        subprocess.run([self.adb_exe, "-s", self.adb_address, "wait-for-device"],
                       capture_output=True, text=True, timeout=30)
        rel = self.adb("shell", "getprop", "ro.build.version.release").strip()
        sdk = self.adb("shell", "getprop", "ro.build.version.sdk").strip()
        _log(f"adb siap: {self.adb_address}  Android {rel} (SDK {sdk})")

    def stop(self) -> None:
        try:
            subprocess.run([self.adb_exe, "disconnect", self.adb_address],
                           capture_output=True, text=True)
        except Exception:
            pass
        if self._fwd:
            self._fwd.shutdown()
            self._fwd.server_close()
            self._fwd = None
        if self._client:
            self._client.close()
            self._client = None
        _log("tunnel ditutup")

    @property
    def alive(self) -> bool:
        t = self._client.get_transport() if self._client else None
        return bool(t and t.is_active())

    # -- adb helper -----------------------------------------------------------
    def adb(self, *args: str, binary: bool = False, timeout: int = 40):
        cmd = [self.adb_exe, "-s", self.adb_address, *args]
        try:
            r = subprocess.run(cmd, capture_output=True, text=not binary, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"adb {' '.join(args)} -> TIMEOUT {timeout}s")
        if r.returncode != 0:
            err = r.stderr if not binary else r.stderr.decode("utf-8", "replace")
            raise RuntimeError(f"adb {' '.join(args)} -> rc={r.returncode} {err.strip()}")
        return r.stdout
