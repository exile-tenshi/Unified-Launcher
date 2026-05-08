# ClamAV integration for uploaded hub assets (emoji/sticker blobs).
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile


def clamav_configured() -> bool:
    return bool(os.getenv("CLAMAV_CLAMD_SOCKET") or os.getenv("CLAMAV_CLAMD_HOST") or shutil.which("clamscan"))


def scan_bytes(blob: bytes) -> tuple[bool, str]:
    """
    Returns (clean, detail). When ClamAV is not configured, returns (True, "skipped").
    """
    sock = os.getenv("CLAMAV_CLAMD_SOCKET", "").strip()
    host = os.getenv("CLAMAV_CLAMD_HOST", "").strip()
    port = int(os.getenv("CLAMAV_CLAMD_PORT", "3310"))

    if sock or host:
        try:
            return _scan_clamd(blob, socket_path=sock or None, host=host or None, port=port)
        except Exception as e:
            return False, f"clamd error: {e}"

    clam = shutil.which("clamscan")
    if not clam:
        return True, "clamav not configured"

    tmp = tempfile.NamedTemporaryFile(delete=False)
    try:
        tmp.write(blob)
        tmp.close()
        r = subprocess.run(
            [clam, "--no-summary", "--infected", tmp.name],
            capture_output=True,
            text=True,
            timeout=min(120, int(os.getenv("CLAMAV_TIMEOUT_SEC", "60"))),
        )
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 1:
            return False, out.strip() or "infected"
        if r.returncode == 2:
            return False, out.strip() or "clamscan error"
        return True, "clean"
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _scan_clamd(blob: bytes, *, socket_path: str | None, host: str | None, port: int) -> tuple[bool, str]:
    import struct

    cmd = struct.pack(b"!4sL", b"zINSTREAM", len(blob)) + blob + struct.pack(b"!L", 0)
    if socket_path:
        import socket as _sock

        s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
        s.connect(socket_path)
    else:
        import socket as _sock

        s = _sock.create_connection((host, port), timeout=10)

    try:
        s.sendall(cmd)
        data = b""
        while b"\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()

    text = data.decode("utf-8", errors="replace").strip().lower()
    if "found" in text and "ok" not in text:
        return False, text
    if "found" not in text and "ok" in text:
        return True, text
    return False, text or "unexpected clamd response"
