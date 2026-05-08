# Per-mailbox at-rest encryption (AES-256-GCM) with passphrase-derived KEK (PBKDF2-SHA256).
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LEN = 12
_SALT_BYTES = 16
_PBKDF2_ITERS = 390_000
_DEK_LEN = 32

_session: Dict[str, dict] = {}
_session_lock = threading.Lock()


def new_salt_b64() -> str:
    return base64.b64encode(os.urandom(_SALT_BYTES)).decode()


def derive_kek(passphrase: str, salt_b64: str) -> bytes:
    salt = base64.b64decode(salt_b64.encode())
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, _PBKDF2_ITERS, dklen=32)


def random_dek() -> bytes:
    return os.urandom(_DEK_LEN)


def wrap_dek_with_kek(dek: bytes, kek: bytes) -> str:
    aes = AESGCM(kek)
    nonce = os.urandom(_NONCE_LEN)
    ct = aes.encrypt(nonce, dek, None)
    return base64.b64encode(nonce + ct).decode()


def unwrap_dek(wrapped_b64: str, kek: bytes) -> bytes:
    raw = base64.b64decode(wrapped_b64.encode())
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    return AESGCM(kek).decrypt(nonce, ct, None)


def encrypt_blob(dek: bytes, plaintext: bytes) -> str:
    aes = AESGCM(dek)
    nonce = os.urandom(_NONCE_LEN)
    ct = aes.encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ct).decode()


def decrypt_blob(dek: bytes, blob_b64: str) -> bytes:
    raw = base64.b64decode(blob_b64.encode())
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    return AESGCM(dek).decrypt(nonce, ct, None)


def mailbox_enabled(udata: dict) -> bool:
    mc = udata.get("mailbox_crypto") or {}
    return bool(mc.get("enabled")) and bool(mc.get("salt")) and bool(mc.get("dek_wrapped"))


def session_put(username: str, dek: bytes, ttl_sec: float = 900.0) -> None:
    with _session_lock:
        _session[username] = {"dek": dek, "exp": time.time() + ttl_sec}


def session_touch(username: str, ttl_sec: float = 900.0) -> None:
    with _session_lock:
        cur = _session.get(username)
        if cur:
            cur["exp"] = time.time() + ttl_sec


def session_get(username: str) -> Optional[bytes]:
    with _session_lock:
        ent = _session.get(username)
        if not ent:
            return None
        if time.time() > ent["exp"]:
            _session.pop(username, None)
            return None
        return ent["dek"]


def session_clear(username: str) -> None:
    with _session_lock:
        _session.pop(username, None)


def enable_mailbox_crypto(udata: dict, passphrase: str) -> None:
    if len(passphrase) < 10:
        raise ValueError("Passphrase too short (min 10 chars)")
    salt_b64 = new_salt_b64()
    kek = derive_kek(passphrase, salt_b64)
    dek = random_dek()
    wrapped = wrap_dek_with_kek(dek, kek)
    udata["mailbox_crypto"] = {"enabled": True, "salt": salt_b64, "dek_wrapped": wrapped, "v": 1}


def unlock_mailbox(udata: dict, username: str, passphrase: str) -> bytes:
    mc = udata.get("mailbox_crypto") or {}
    if not mc.get("enabled"):
        raise ValueError("Mailbox encryption not enabled")
    kek = derive_kek(passphrase, mc["salt"])
    dek = unwrap_dek(mc["dek_wrapped"], kek)
    session_put(username, dek)
    return dek


def verify_passphrase(udata: dict, passphrase: str) -> None:
    mc = udata.get("mailbox_crypto") or {}
    kek = derive_kek(passphrase, mc["salt"])
    unwrap_dek(mc["dek_wrapped"], kek)


def disable_mailbox(username: str, udata: dict, passphrase: str) -> None:
    if mailbox_enabled(udata):
        verify_passphrase(udata, passphrase)
        udata.pop("mailbox_crypto", None)
        session_clear(username)
        return
    udata.pop("mailbox_crypto", None)
    session_clear(username)


def plaintext_email_bundle(email: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "subject": email.get("subject", ""),
        "body": email.get("body", ""),
        "spam_reason": email.get("spam_reason", ""),
        "category": email.get("category", ""),
        "to": email.get("to", ""),
    }


def encrypt_email_if_possible(username: str, udata: dict, email: Dict[str, Any]) -> Dict[str, Any]:
    if not mailbox_enabled(udata) or email.get("enc_payload") or email.get("_encrypt_pending"):
        return email
    dek = session_get(username)
    if not dek:
        e2 = dict(email)
        e2["_encrypt_pending"] = True
        return e2
    bundle = plaintext_email_bundle(email)
    payload = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
    enc = encrypt_blob(dek, payload)
    return {
        "from": email.get("from"),
        "time": email.get("time", time.time()),
        "read": email.get("read", False),
        "mailbox_v": 1,
        "enc_payload": enc,
    }


def prepare_email_for_client(username: str, udata: dict, email: Dict[str, Any]) -> Dict[str, Any]:
    if not email.get("enc_payload"):
        return dict(email)
    dek = session_get(username)
    if not dek:
        return {
            "from": email.get("from"),
            "subject": "",
            "body": "🔒 Unlock your mailbox with your passphrase on this device to read encrypted mail.",
            "time": email.get("time"),
            "read": email.get("read", False),
            "locked": True,
        }
    try:
        raw = decrypt_blob(dek, email["enc_payload"])
        inner = json.loads(raw.decode("utf-8"))
        merged = dict(email)
        merged.update(inner)
        merged.pop("enc_payload", None)
        merged.pop("mailbox_v", None)
        merged.pop("locked", None)
        return merged
    except Exception:
        return {
            "from": email.get("from"),
            "subject": "",
            "body": "(Mailbox decrypt error)",
            "time": email.get("time"),
            "read": email.get("read", False),
            "locked": True,
        }


def decrypt_folder_emails(username: str, udata: dict, emails: List[dict]) -> List[dict]:
    return [prepare_email_for_client(username, udata, e) for e in emails]


def migrate_folder_emails(username: str, udata: dict, key: str) -> int:
    """Encrypt plaintext entries in USER_DB[username][key] list."""
    if not mailbox_enabled(udata):
        return 0
    dek = session_get(username)
    if not dek:
        return 0
    arr = udata.setdefault(key, [])
    n = 0
    for i, email in enumerate(arr):
        if not isinstance(email, dict) or email.get("enc_payload") or email.get("_encrypt_pending"):
            continue
        arr[i] = encrypt_email_if_possible(username, udata, email)
        n += 1
    return n
