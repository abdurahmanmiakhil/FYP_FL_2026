"""Optional ClamAV scan over the clamd INSTREAM protocol (enabled when CLAMAV_HOST is set)."""

from __future__ import annotations

import socket
import struct
from pathlib import Path

from ..core.config import get_settings


class VirusFound(Exception):
    pass


class ScannerUnavailable(Exception):
    pass


def scan_file(path: Path) -> None:
    s = get_settings()
    if not s.CLAMAV_HOST:
        return
    try:
        with socket.create_connection((s.CLAMAV_HOST, s.CLAMAV_PORT), timeout=120) as sock:
            sock.sendall(b"zINSTREAM\0")
            with open(path, "rb") as f:
                while chunk := f.read(1 << 20):
                    sock.sendall(struct.pack("!L", len(chunk)) + chunk)
            sock.sendall(struct.pack("!L", 0))
            reply = sock.recv(4096).decode(errors="replace").strip("\0\n ")
    except OSError as e:
        raise ScannerUnavailable(str(e)) from e
    if reply.endswith("FOUND"):
        raise VirusFound(reply.split(":", 1)[-1].replace("FOUND", "").strip())
    if not reply.endswith("OK"):
        raise ScannerUnavailable(reply)
