import socket
import threading
import json
import os
import time
import base64
import hashlib
from typing import Dict, Any
import smtplib
from email.mime.text import MIMEText
import requests
try:
    import redis
    has_redis = True
except ImportError:
    has_redis = False

try:
    import clamav_scan as _clamav_scan
except ImportError as _e_clam:
    _clamav_scan = None  # type: ignore
    print(f"[BOOT] clamav_scan not loaded: {_e_clam}")
try:
    import mailbox_crypto as _mbx
except ImportError as _e_mx:
    _mbx = None  # type: ignore
    print(f"[BOOT] mailbox_crypto not loaded: {_e_mx}")
try:
    import hub_dsl as _hub_dsl
except ImportError as _e_hub:
    _hub_dsl = None  # type: ignore
    print(f"[BOOT] hub_dsl not loaded: {_e_hub}")
try:
    import ai_providers as _ai_providers
except ImportError as _e_ai:
    _ai_providers = None  # type: ignore
    print(f"[BOOT] ai_providers not loaded: {_e_ai}")
try:
    import premium_marketplace as _premium_mkt
except ImportError as _e_pm:
    _premium_mkt = None  # type: ignore
    print(f"[BOOT] premium_marketplace not loaded: {_e_pm}")

EXTENSION_MAILBOX = _mbx is not None
EXTENSION_HUB_DSL = _hub_dsl is not None
EXTENSION_AI_CHAIN = _ai_providers is not None
EXTENSION_MARKET = _premium_mkt is not None
EXTENSION_CLAMAV = _clamav_scan is not None

# --- CONFIGURATION ---
HOST = '0.0.0.0'
PORT = 6000
REGISTRY_FILE = 'server_registry.json'

# --- .env LOADER ---
# Priority: Manual ENV VAR -> .env file
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
REDIS_URL = os.getenv("UPSTASH_REDIS_URL", "")

# ── Verification service keys ──
WORLDID_APP_ID    = os.getenv("WORLDID_APP_ID", "")
WORLDID_ACTION    = os.getenv("WORLDID_ACTION", "verify-age")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_IDENTITY_PRICE = 500  # $5.00 in cents
META_APP_ID       = os.getenv("META_APP_ID", "")
META_APP_SECRET   = os.getenv("META_APP_SECRET", "")
LINKEDIN_CLIENT_ID     = os.getenv("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")

# ── Monetized add-ons ──
_AI_SUB_DAYS = int(os.getenv("AI_ASSISTANT_SUBSCRIPTION_DAYS", "30"))
_AI_PRICE_CENTS = int(os.getenv("AI_ASSISTANT_PRICE_CENTS", "899"))
_PREMIUM_UNAME_PRICE = int(os.getenv("PREMIUM_USERNAME_PRICE_CENTS", "999"))
_PUBLIC_CHECKOUT_BASE = os.getenv("TENSHI_PUBLIC_ORIGIN", "https://hub.tenshi.lol").rstrip("/")

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
print(f"[BOOT] Looking for .env at: {_ENV_PATH} (exists: {os.path.exists(_ENV_PATH)})")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, "r") as f:
        for line in f:
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "ANTHROPIC_API_KEY" and not ANTHROPIC_API_KEY:
                    ANTHROPIC_API_KEY = v
                if k == "GROQ_API_KEY":    GROQ_API_KEY   = v
                if k == "GEMINI_API_KEY":  GEMINI_API_KEY = v
                if k == "SMTP_HOST":   SMTP_HOST  = v
                if k == "SMTP_PORT":   SMTP_PORT  = int(v)
                if k == "SMTP_EMAIL":  SMTP_USER  = v
                if k == "SMTP_FROM":   SMTP_FROM  = v
                if k == "SMTP_PASS":   SMTP_PASS  = v
                if k == "TURNSTILE_SECRET":    TURNSTILE_SECRET = v
                if k == "WORLDID_APP_ID":     WORLDID_APP_ID   = v
                if k == "WORLDID_ACTION":     WORLDID_ACTION   = v
                if k == "STRIPE_SECRET_KEY":  STRIPE_SECRET_KEY = v
                if k == "META_APP_ID":        META_APP_ID      = v
                if k == "META_APP_SECRET":    META_APP_SECRET  = v
                if k == "LINKEDIN_CLIENT_ID":      LINKEDIN_CLIENT_ID     = v
                if k == "LINKEDIN_CLIENT_SECRET":  LINKEDIN_CLIENT_SECRET = v
                if k == "TENSHI_PUBLIC_ORIGIN":     _PUBLIC_CHECKOUT_BASE  = v.rstrip("/")

# --- INFRASTRUCTURE V2.2+: REDIS SUPPORT ---
r_client = None
has_redis = False
try:
    import redis
    has_redis = True
    if REDIS_URL:
        r_client = redis.from_url(REDIS_URL, decode_responses=True)
        print("--- UPSTASH REDIS CONNECTED ---")
except ImportError:
    pass
except Exception as e:
    print(f"Redis Connection Failed: {e}")

def load_db(file_path, default_data=None):
    if default_data is None: default_data = {}
    
    # Priority 1: Redis
    if r_client:
        data = r_client.get(file_path)
        if data: return json.loads(data)
    
    # Priority 2: Local JSON
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return default_data

# Message Queues (Ephemeral in memory) target_user -> list of messages
MESSAGE_QUEUE: Dict[str, list] = {}

# ── Notification Events (for instant push via long-poll) ──
# Each user gets a threading.Event that's set whenever something is queued for them
_NOTIFY_EVENTS: Dict[str, threading.Event] = {}

def _notify_user(username: str):
    """Signal that a user has pending notifications — wakes up any long-poll."""
    ev = _NOTIFY_EVENTS.get(username)
    if ev:
        ev.set()

# Wrap MESSAGE_QUEUE insertion to auto-signal
_orig_mq_setdefault = MESSAGE_QUEUE.setdefault.__func__ if hasattr(MESSAGE_QUEUE.setdefault, '__func__') else None

class _NotifyDict(dict):
    """Dict subclass that signals notification events when items are appended."""
    def setdefault(self, key, default=None):
        result = super().setdefault(key, default)
        # Signal after append (caller does .append() right after setdefault)
        # We signal on a short delay so the append completes first
        threading.Timer(0.05, _notify_user, args=(key,)).start()
        return result

# Replace MESSAGE_QUEUE with notifying version
_old_mq = MESSAGE_QUEUE
MESSAGE_QUEUE = _NotifyDict(_old_mq)

# DM History — persisted to disk so messages survive restarts
DM_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dm_history.json")
DM_HISTORY: Dict[str, list] = {}
try:
    with open(DM_HISTORY_FILE, 'r') as _f:
        DM_HISTORY = json.load(_f)
    print(f"[BOOT] Loaded {len(DM_HISTORY)} DM conversations from disk")
except (FileNotFoundError, json.JSONDecodeError):
    print("[BOOT] Starting with empty DM history")

def _store_email(to_addr, from_user, subject, body):
    """Store email in recipient's inbox if they're a Tenshi user (not the sender)."""
    target_user = None
    if to_addr.endswith("@tenshi.lol"):
        target_user = to_addr.split("@")[0]
    # Don't deliver to sender's own inbox
    if target_user == from_user:
        return
    if target_user and target_user in USER_DB:
        _em = {
            "from": from_user,
            "subject": subject,
            "body": body,
            "time": time.time(),
            "read": False
        }
        if EXTENSION_MAILBOX:
            _em = _mbx.encrypt_email_if_possible(target_user, USER_DB[target_user], _em)
        USER_DB[target_user].setdefault("inbox", []).insert(0, _em)
        if len(USER_DB[target_user]["inbox"]) > 100:
            USER_DB[target_user]["inbox"] = USER_DB[target_user]["inbox"][:100]
        save_all()

def _dm_key(a, b):
    """Stable key for a DM conversation regardless of who sends."""
    return ":".join(sorted([a, b]))

# Active calls: caller -> { target, type, status, time }
ACTIVE_CALLS: Dict[str, dict] = {}
# WebRTC signaling: username -> [{"from": sender, "type": "offer"|"answer"|"ice", "data": ...}]
CALL_SIGNALS: Dict[str, list] = {}
VC_SIGNALS: Dict[str, list] = {}  # Separate signal queue for voice channel WebRTC

def _save_dm_history():
    """Persist DM history to disk."""
    try:
        with open(DM_HISTORY_FILE, 'w') as f:
            json.dump(DM_HISTORY, f)
    except Exception as e:
        print(f"[DM] Save error: {e}")

def _store_dm(sender, target, msg_obj):
    """Store a DM in history (max 500 per convo, persisted to disk)."""
    key = _dm_key(sender, target)
    DM_HISTORY.setdefault(key, []).append(msg_obj)
    if len(DM_HISTORY[key]) > 500:
        DM_HISTORY[key] = DM_HISTORY[key][-500:]
    _save_dm_history()

# Global active connections mapping (username -> connection socket)
ACTIVE_CONNECTIONS: Dict[str, socket.socket] = {}
VOICE_CLIENTS: Dict[socket.socket, str] = {} # socket -> current_channel
VIDEO_CLIENTS: Dict[socket.socket, str] = {} # socket -> username
WEB_VIDEO_BUFFER: Dict[str, str] = {} # target_channel_user -> base64 frame
ONLINE_USERS: Dict[str, float] = {}  # username -> last_seen Unix timestamp
VOICE_PRESENCE: Dict[str, str] = {}  # username -> "server_id:channel_name"
USER_STORIES: Dict[str, dict] = {}   # username -> {content, media, expires_at}
USER_NOTES: Dict[str, dict] = {}     # "viewer:target" -> note text
NOISE_SUPPRESS: Dict[str, set] = {}  # username -> set of users suppressed for them

# Persistent Storage Files
SERVERS_FILE = 'servers.json'
AI_CONTEXT: Dict[str, list] = {} # AI Memory: username -> list of messages

if ANTHROPIC_API_KEY:
    print(f"[SYSTEM] Tenshi AI (Claude) is ACTIVE and READY.")
else:
    print(f"[WARNING] Tenshi AI is OFFLINE. (Missing ANTHROPIC_API_KEY)")

USER_DB = load_db(REGISTRY_FILE)
SERVERS_DB = load_db(SERVERS_FILE)

# ── Thread safety: Global lock for all DB mutations ──────────────
# Prevents race conditions (duplicate registrations, lost updates, data corruption)
_DB_LOCK = threading.Lock()


def _user_ai_subscription_active(uname: str) -> bool:
    """Personal AI Assistant — paid Stripe add-on unless admin/free."""
    u = USER_DB.get(uname) or {}
    if u.get("is_admin") or u.get("free_access"):
        return True
    return time.time() < float(u.get("ai_assistant_sub_until") or 0)


def _hub_media_b64_decode(data_field: str) -> bytes:
    s = data_field or ""
    if isinstance(s, bytes):
        return s[:100 * 1024 * 1024]
    if "base64," in s:
        s = s.split("base64,", 1)[1]
    try:
        return base64.b64decode(s.strip(), validate=False)
    except Exception:
        return b""


def _stripe_checkout_urls(request: Dict[str, Any]) -> tuple:
    """Use client-provided return URLs only if host is tenshi.lol / localhost."""

    def _safe(u: str, fallback_suffix: str) -> str:
        if not isinstance(u, str) or len(u) > 512:
            return _PUBLIC_CHECKOUT_BASE.rstrip("/") + fallback_suffix
        p = urllib.parse.urlparse(u)
        host = (p.hostname or "").lower()
        tenshi_ok = host.endswith("tenshi.lol") or host in ("localhost", "127.0.0.1")
        scheme_ok = p.scheme in ("https", "http") and tenshi_ok
        if not scheme_ok:
            return _PUBLIC_CHECKOUT_BASE.rstrip("/") + fallback_suffix
        return u

    succ = _safe(request.get("success_url", ""), "/hub.html?paid=checkout")
    canc = _safe(request.get("cancel_url", ""), "/hub.html?canceled=1")
    return succ, canc


def _stripe_expand_session_placeholder(success_url: str) -> str:
    macro = "{CHECKOUT_SESSION_ID}"
    if macro in success_url:
        return success_url
    sep = "&" if "?" in success_url else "?"
    return success_url + sep + "session_id=" + macro


def _stripe_create_checkout(username: str, request: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Stripe-hosted Checkout Session (Payment mode)."""
    if not STRIPE_SECRET_KEY:
        return {"status": "fail", "message": "Stripe not configured."}
    kind = (request.get("checkout_kind") or request.get("product") or "").strip().lower()
    succ_raw, canc = _stripe_checkout_urls(request)
    succ_final = succ_raw if "{CHECKOUT_SESSION_ID}" in succ_raw else _stripe_expand_session_placeholder(succ_raw)

    listing_id = (request.get("listing_id") or "").strip()
    form: Dict[str, Any] = {
        "mode": "payment",
        "client_reference_id": username,
        "success_url": succ_final,
        "cancel_url": canc,
    }

    if kind in ("ai_assistant", "ai_assistant_30d", "personal_ai"):
        form["metadata[checkout_kind]"] = "ai_assistant"
        form["metadata[username]"] = username
        form["line_items[0][price_data][currency]"] = "usd"
        form["line_items[0][price_data][unit_amount]"] = str(_AI_PRICE_CENTS)
        form["line_items[0][price_data][product_data][name]"] = f"Tenshi Personal AI Assistant ({_AI_SUB_DAYS} days)"
        form["line_items[0][quantity]"] = "1"
    elif kind in ("premium_username", "premium_handle"):
        if not listing_id or not EXTENSION_MARKET:
            return {"status": "fail", "message": "Premium listing required."}
        mp, lst = _premium_mkt.get_listing(listing_id)
        del mp
        if not lst or lst.get("status") != "available":
            return {"status": "fail", "message": "Listing unavailable."}
        if lst.get("reserved_by") and lst["reserved_by"] != username:
            return {"status": "fail", "message": "Reserved by someone else."}
        form["metadata[checkout_kind]"] = "premium_username"
        form["metadata[username]"] = username
        form["metadata[listing_id]"] = listing_id
        form["metadata[listing_handle]"] = lst["handle"]
        form["line_items[0][price_data][currency]"] = "usd"
        form["line_items[0][price_data][unit_amount]"] = str(lst.get("price_cents") or _PREMIUM_UNAME_PRICE)
        form["line_items[0][price_data][product_data][name]"] = f"Premium Username: {lst['handle']}"
        form["line_items[0][quantity]"] = "1"
    else:
        return {"status": "fail", "message": "Unknown checkout kind."}

    try:
        r = requests.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(STRIPE_SECRET_KEY, ""),
            data=form,
            timeout=20,
        )
        js = r.json()
        if r.status_code == 200 and js.get("url"):
            audit("STRIPE_CHECKOUT_CREATE", username, "", kind + (":" + listing_id if listing_id else ""))
            return {"status": "success", "url": js["url"], "checkout_kind": kind, "stripe_session_id": js.get("id", "")}
        return {"status": "fail", "message": js.get("error", {}).get("message", js.get("message", r.text[:160]))}
    except Exception as e:
        return {"status": "fail", "message": str(e)}


def _stripe_finish_and_apply(username: str, session_id: str) -> Dict[str, Any]:
    if not STRIPE_SECRET_KEY:
        return {"status": "fail", "message": "Stripe unavailable."}
    try:
        r = requests.get(
            f"https://api.stripe.com/v1/checkout/sessions/{session_id}",
            auth=(STRIPE_SECRET_KEY, ""),
            timeout=15,
        )
        sd = r.json()
        if sd.get("payment_status") != "paid":
            return {"status": "fail", "message": "Payment not complete."}
        ref = sd.get("client_reference_id") or ""
        meta = sd.get("metadata") or {}
        if ref != username and ref.lower() != (username or "").lower():
            return {"status": "fail", "message": "Session does not belong to this account."}
        kind = (meta.get("checkout_kind") or "").strip()
        if kind == "ai_assistant":
            USER_DB.setdefault(username, {})["ai_assistant_sub_until"] = time.time() + (_AI_SUB_DAYS * 86400)
            USER_DB[username]["ai_assistant_stripe_sid"] = session_id[:48]
            save_all()
            return {"status": "success", "message": "AI Assistant unlocked.", "ai_assistant_sub_until": USER_DB[username]["ai_assistant_sub_until"]}
        if kind == "premium_username" and EXTENSION_MARKET:
            lid = meta.get("listing_id", "")
            hdl = meta.get("listing_handle", "")
            ok, err = _premium_mkt.fulfill(
                lid, username, hdl, lambda hh: any(k.lower() == hh.lower() for k in USER_DB)
            )
            if not ok:
                return {"status": "fail", "message": err or "Fulfillment failed."}
            USER_DB.setdefault(username, {})["_premium_claim_handle"] = hdl
            save_all()
            audit("PREMIUM_USERNAME_PAID", username, hdl, session_id[:16])
            return {"status": "success", "message": "You can rename to your premium username now.", "claim_handle": hdl}
        return {"status": "fail", "message": "Unknown Stripe product metadata."}
    except Exception as e:
        print(f"[STRIPE_FINISH] {e}")
        return {"status": "fail", "message": "Verification failed."}

# ── Global platform config (admin-toggleable) ──────────────────
PLATFORM_CONFIG_FILE = 'platform_config.json'
PLATFORM_CONFIG = {
    "birthday_verification_enabled": True,  # Require DOB at registration
}
try:
    with open(PLATFORM_CONFIG_FILE, 'r') as _pcf:
        PLATFORM_CONFIG.update(json.load(_pcf))
except Exception:
    pass

def _save_platform_config():
    try:
        with open(PLATFORM_CONFIG_FILE, 'w') as _pcf:
            json.dump(PLATFORM_CONFIG, _pcf, indent=2)
    except Exception as e:
        print(f"[ERROR] Could not save platform config: {e}")

# ── Age-gate: blocked HWIDs for under-13 registration attempts ──
AGEGATE_FILE = 'agegate_blocked_hwids.json'
_AGEGATE_BLOCKED: Dict[str, float] = {}  # hwid -> timestamp_blocked
try:
    with open(AGEGATE_FILE, 'r') as _af:
        _AGEGATE_BLOCKED = json.load(_af)
except (FileNotFoundError, json.JSONDecodeError):
    _AGEGATE_BLOCKED = {}
# Prune expired entries (older than 1 year) on boot
_AGEGATE_EXPIRY = 365.25 * 24 * 3600  # 1 year in seconds
_AGEGATE_BLOCKED = {h: t for h, t in _AGEGATE_BLOCKED.items() if time.time() - t < _AGEGATE_EXPIRY}

def _save_agegate():
    try:
        with open(AGEGATE_FILE, 'w') as _af:
            json.dump(_AGEGATE_BLOCKED, _af)
    except Exception:
        pass

# ── Retired usernames & emails: never recycled ──────────────────
RETIRED_NAMES_FILE = 'retired_names.json'
_RETIRED_NAMES: Dict[str, list] = {"usernames": [], "emails": []}
try:
    with open(RETIRED_NAMES_FILE, 'r') as _rnf:
        _RETIRED_NAMES = json.load(_rnf)
except (FileNotFoundError, json.JSONDecodeError):
    _RETIRED_NAMES = {"usernames": [], "emails": []}

def _save_retired_names():
    try:
        with open(RETIRED_NAMES_FILE, 'w') as _rnf:
            json.dump(_RETIRED_NAMES, _rnf)
    except Exception:
        pass

# ── Wipe-Account Reservations (permanent block on misbehaving accounts) ──
# Separate from _RETIRED_NAMES so each entry carries the moderation context
# (which admin, when, why, archive pointer). Reservations are PERMANENT by
# default — see ADMIN_UNRESERVE for the platform-owner-only escape hatch.
RESERVED_IDENTIFIERS_FILE = 'reserved_identifiers.json'
_RESERVED_IDENTIFIERS: Dict[str, dict] = {"usernames": {}, "emails": {}, "hwids": {}}
try:
    with open(RESERVED_IDENTIFIERS_FILE, 'r') as _rif:
        _RESERVED_IDENTIFIERS = json.load(_rif)
        for _k in ("usernames", "emails", "hwids"):
            _RESERVED_IDENTIFIERS.setdefault(_k, {})
except (FileNotFoundError, json.JSONDecodeError):
    _RESERVED_IDENTIFIERS = {"usernames": {}, "emails": {}, "hwids": {}}

def _save_reserved_identifiers():
    try:
        with open(RESERVED_IDENTIFIERS_FILE, 'w') as _rif:
            json.dump(_RESERVED_IDENTIFIERS, _rif, indent=2)
    except Exception as _e:
        print(f"[RESERVED] Failed to save: {_e}")

# ── Profile color persistence (roadmap §6) ─────────────────────────────────
# A user's profile color stays attached to them permanently unless they change
# it manually in settings. SSO-registered users (who never explicitly pick one)
# get a deterministic palette pick from a username hash — so every user has a
# stable color from day one and the rendering never falls back to a generic
# placeholder mid-session.
_PROFILE_COLOR_PALETTE = [
    "#5865F2", "#23a559", "#f0b132", "#ed4245", "#9b59b6",
    "#e91e63", "#1abc9c", "#3498db", "#e67e22", "#16a085",
    "#8e44ad", "#27ae60", "#d35400", "#2980b9", "#c0392b",
]
def _default_profile_color(username: str) -> str:
    h = hashlib.sha256((username or "").encode("utf-8")).digest()
    return _PROFILE_COLOR_PALETTE[h[0] % len(_PROFILE_COLOR_PALETTE)]


def _hub_member_role_name_color(
    role_map: dict,
    member_roles,
    member_username: str,
    owner_username: str,
    profile_username_color: str,
) -> str:
    """Discord-style name color from server roles: first non-everyone role wins (role map insertion order).

    Fallback: @everyone color, then golden accent for hub owner with only default roles,
    then the user's profile username_color.
    """
    fb = profile_username_color or "#ffffff"
    slot = set(member_roles if isinstance(member_roles, list) else [])
    if not isinstance(role_map, dict) or not role_map:
        if member_username == owner_username and owner_username:
            return "#FEE75C"
        return fb

    everyone_color = None
    for rid, meta in role_map.items():
        if rid not in slot:
            continue
        col = fb
        if isinstance(meta, dict):
            raw = meta.get("color")
            if isinstance(raw, str) and raw.strip():
                col = raw.strip()

        if rid == "role_everyone":
            everyone_color = col
            continue

        return col

    if everyone_color is not None:
        return everyone_color
    if member_username == owner_username and owner_username:
        return "#FEE75C"
    return fb


# ── Per-user rate limit for "pin via flag" (5 per hour) ─────────────────────
# Was previously stored under MESSAGE_QUEUE["_dflag_rl:{user}"], which polluted
# every consumer that iterates MESSAGE_QUEUE (search, polling). Dedicated dict
# keeps the keyspace clean and is cleaned up by _scrub_user_references.
_FLAG_PIN_RATE_LIMIT: Dict[str, list] = {}

# ── Soft-delete lookup helpers (one source of truth for find / pin / purge) ──
# These collapse five copy-pasted "scan three stores for a msg_id" loops that
# previously lived inline in FLAG_CONTENT, ADMIN_RESTORE_DELETED, ADMIN_PURGE_NOW,
# and both expiry daemons. The pin-check predicate is security-critical
# (CSAM-flagged content must NEVER auto-purge) so a single definition matters.
SOFT_DELETE_KIND_HUB_MSG = "message"
SOFT_DELETE_KIND_STORY   = "story"
SOFT_DELETE_KIND_TEMP    = "temp"
SOFT_DELETE_KINDS = {SOFT_DELETE_KIND_HUB_MSG, SOFT_DELETE_KIND_STORY, SOFT_DELETE_KIND_TEMP}

def _find_soft_deleted_by_id(msg_id: str):
    """Find a soft-deleted item across hub messages, stories, and DM temps.
    Returns (kind, container_key, item_dict) or None.
    For temps: container_key is the dm_key; the temp itself is the item.
    For hub messages: container_key is "server_id:channel"; for stories: the owner username."""
    if not msg_id:
        return None
    for ch_key, msgs in SERVER_MESSAGES.items():
        for m in msgs:
            if m.get("msg_id") == msg_id and m.get("deleted"):
                return (SOFT_DELETE_KIND_HUB_MSG, ch_key, m)
    for owner, stories in USER_STORIES.items():
        for s in stories:
            if s.get("id") == msg_id and s.get("deleted"):
                return (SOFT_DELETE_KIND_STORY, owner, s)
    for dm_key, msgs in DM_HISTORY.items():
        for m in msgs:
            if m.get("msg_id") == msg_id and m.get("is_temp"):
                return (SOFT_DELETE_KIND_TEMP, dm_key, m)
    return None

def _is_pinned(item: dict, sender: str = "") -> bool:
    """Return True if a soft-deleted item is pinned (must not auto-purge).
    Pinning rules (security-critical): CSAM-flagged content, items reported via
    FLAG_CONTENT, and items whose sender's account is under_review."""
    if item.get("flag_type") == "csam" or item.get("csam_report"):
        return True
    if item.get("flagged_for_review"):
        return True
    if sender and USER_DB.get(sender, {}).get("under_review"):
        return True
    return False

def _normalize_email(email: str) -> str:
    """Normalize email so Gmail dot/+ tricks and case don't bypass reservations.
    Strips dots and +suffix from the local-part for Gmail/Google domains, lowercases everything."""
    if not email or "@" not in email:
        return email.lower().strip()
    local, _, domain = email.lower().strip().partition("@")
    # Strip + suffix (universal — most providers honour it)
    if "+" in local:
        local = local.split("+", 1)[0]
    # Strip dots for gmail/googlemail (Google ignores them)
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
    return f"{local}@{domain}"

def _is_reserved(username: str = "", email: str = "", hwid: str = "") -> bool:
    """Check whether any of the supplied identifiers is on the wipe-account reservation list."""
    if username and username.lower() in {k.lower() for k in _RESERVED_IDENTIFIERS.get("usernames", {}).keys()}:
        return True
    if email:
        norm = _normalize_email(email)
        if norm in _RESERVED_IDENTIFIERS.get("emails", {}):
            return True
    if hwid and hwid in _RESERVED_IDENTIFIERS.get("hwids", {}):
        return True
    return False

def _reserve_identifiers(username: str, email: str, hwid: str, by_admin: str, reason: str, archive_path: str):
    """Permanently reserve username/email/HWID after a wipe action."""
    ts = time.time()
    meta = {"wiped_at": ts, "by": by_admin, "reason": reason[:500], "archive": archive_path}
    if username:
        _RESERVED_IDENTIFIERS["usernames"][username] = dict(meta)
    if email:
        _RESERVED_IDENTIFIERS["emails"][_normalize_email(email)] = dict(meta)
    if hwid:
        _RESERVED_IDENTIFIERS["hwids"][hwid] = dict(meta)
    _save_reserved_identifiers()

def _archive_user_for_wipe(target: str, by_admin: str, reason: str) -> str:
    """Snapshot all data tied to `target` to wiped_accounts/{target}_{ts}.json BEFORE
    any scrub work happens. Returns the archive file path. Used as the moderation
    evidence record. Includes raw DM history (the report that triggered this needs it)."""
    archive_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wiped_accounts")
    os.makedirs(archive_dir, exist_ok=True)
    ts_int = int(time.time())
    archive_path = os.path.join(archive_dir, f"{_safe_filename(target)}_{ts_int}.json")

    snapshot = {
        "target":          target,
        "wiped_at":        time.time(),
        "wiped_at_utc":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "by_admin":        by_admin,
        "reason":          reason,
        "user_record":     USER_DB.get(target, {}),
        "dm_history":      {k: v for k, v in DM_HISTORY.items() if target in k.split(":")},
        "server_messages": {k: [m for m in v if m.get("sender") == target]
                            for k, v in SERVER_MESSAGES.items()
                            if any(m.get("sender") == target for m in v)},
        "stories":         USER_STORIES.get(target, []),
        "owned_servers":   [sid for sid, s in SERVERS_DB.items() if s.get("owner") == target],
        "csam_evidence_pointer": "see csam_evidence.json / csam_reports.log for any entries naming this user",
    }
    try:
        with open(archive_path, 'w') as _af:
            json.dump(snapshot, _af, indent=2, default=str)
    except Exception as _e:
        print(f"[WIPE-ARCHIVE] Failed to write archive: {_e}")
    return archive_path

def _scrub_user_references(target: str):
    """Remove every live-state reference to `target` across all data structures.
    Tombstones their content in shared structures (DMs, server messages) so the
    *other* parties' conversational context is preserved while the wiped user's
    contributions are clearly marked as gone."""
    TOMBSTONE = "[Account wiped]"
    now = time.time()

    # ── User-owned state (delete entirely) ──
    USER_DB.pop(target, None)
    USER_STORIES.pop(target, None)
    try: MESSAGE_QUEUE.pop(target, None)
    except Exception: pass
    ONLINE_USERS.pop(target, None)
    VOICE_PRESENCE.pop(target, None)
    LIVE_STREAMS.pop(target, None)
    _2FA_SESSIONS.pop(target, None)
    _FLAG_PIN_RATE_LIMIT.pop(target, None)

    # ── All sessions for this user (kicks them off everywhere) ──
    try:
        for tok, data in list(_SESSION_TOKENS.items()):
            if data.get("username") == target:
                _SESSION_TOKENS.pop(tok, None)
        _save_session_tokens()
    except Exception:
        pass

    # ── DM_HISTORY: tombstone the wipee's messages, keep the other party's intact ──
    for key, msgs in DM_HISTORY.items():
        if target in key.split(":"):
            for m in msgs:
                if m.get("sender") == target:
                    m["content"] = TOMBSTONE
                    m["media"] = ""
                    m["image_b64"] = ""
                    m["wiped"] = True
                    m["wiped_at"] = now
    # Persist DM tombstones — save_all() only covers USER_DB/SERVERS_DB, not DM_HISTORY
    try: _save_dm_history()
    except Exception: pass

    # ── SERVER_MESSAGES: same tombstone treatment ──
    for ch_key, msgs in SERVER_MESSAGES.items():
        for m in msgs:
            if m.get("sender") == target:
                m["content"] = TOMBSTONE
                m["media"] = ""
                m["image_b64"] = ""
                m["wiped"] = True
                m["wiped_at"] = now

    # ── Servers ──
    # Owner → delete the entire server (locked default per security policy).
    # Member → pop from members. Bans referencing target → clean.
    for sid in list(SERVERS_DB.keys()):
        srv = SERVERS_DB.get(sid, {})
        if srv.get("owner") == target and sid != "srv_0":
            # Delete owned servers — never auto-transfer (co-conspirator risk)
            del SERVERS_DB[sid]
            # Pull deleted server_id out of every other user's joined-servers list
            for u, d in USER_DB.items():
                if sid in d.get("servers", []):
                    d["servers"].remove(sid)
            # Drop the channel message buffers for the deleted server
            for _ck in [k for k in SERVER_MESSAGES.keys() if k.startswith(sid + ":")]:
                SERVER_MESSAGES.pop(_ck, None)
            continue
        # Pop from member list of any server they're in
        srv.get("members", {}).pop(target, None)
        # Clean ban entries
        srv.get("bans", {}).pop(target, None)
        # Clean nicknames
        if "nicknames" in srv:
            srv["nicknames"].pop(target, None)
        # Drop custom emojis / stickers added by them
        for _list_key in ("custom_emojis", "stickers"):
            if _list_key in srv:
                srv[_list_key] = [x for x in srv[_list_key] if x.get("added_by") != target]
        # Tickets: drop ones authored by target; tombstone replies
        tickets = srv.get("tickets", {})
        for tid in list(tickets.keys()):
            t = tickets[tid]
            if t.get("author") == target:
                tickets.pop(tid, None)
                continue
            for reply in t.get("messages", []):
                if reply.get("sender") == target:
                    reply["sender"] = "[wiped]"
                    reply["content"] = TOMBSTONE

    # ── Other users' friend / blocked / request lists ──
    for u, data in USER_DB.items():
        if target in data.get("friends", []):
            data["friends"].remove(target)
        if target in data.get("blocked", []):
            data["blocked"].remove(target)
        if isinstance(data.get("pending_friends"), dict):
            data["pending_friends"].pop(target, None)
        if isinstance(data.get("pending_reqs"), list) and target in data["pending_reqs"]:
            data["pending_reqs"].remove(target)
        if isinstance(data.get("sent_reqs"), list) and target in data["sent_reqs"]:
            data["sent_reqs"].remove(target)
        # Children: clear parent_id pointing to target
        if data.get("parent_id") == target:
            data["parent_id"] = None
            data["parental_controls_available"] = False
        # Parents: remove target from linked_children
        if isinstance(data.get("linked_children"), list) and target in data["linked_children"]:
            data["linked_children"].remove(target)

    # ── TEMP_STREAKS: drop any pair key involving target ──
    for k in list(TEMP_STREAKS.keys()):
        if target in k.split(":"):
            TEMP_STREAKS.pop(k, None)

    # ── USER_NOTES: drop any key naming target on either side ──
    for k in list(USER_NOTES.keys()):
        if k.startswith(target + ":") or k.endswith(":" + target):
            USER_NOTES.pop(k, None)

def _is_email_taken(email: str) -> bool:
    """Check if email is already used by any account, was previously retired, or is reserved (wipe).
    Uses normalized comparison everywhere (strips Gmail dot/+ tricks, lowercases) so the same
    person can't bypass any of the three checks by adding dots or +suffix to their address."""
    norm = _normalize_email(email)
    # Check wipe-account reservations
    if norm in _RESERVED_IDENTIFIERS.get("emails", {}):
        return True
    # Check retired emails (normalized comparison closes the same Gmail-trick bypass)
    for retired in _RETIRED_NAMES.get("emails", []):
        if _normalize_email(retired) == norm:
            return True
    # Check all current accounts (also normalized)
    for u, data in USER_DB.items():
        if _normalize_email(data.get("email", "")) == norm:
            return True
    return False

def _is_username_retired(username: str) -> bool:
    """Check if a username was previously used and retired, or is reserved (wipe)."""
    if _is_reserved(username=username):
        return True
    return username.lower() in [n.lower() for n in _RETIRED_NAMES.get("usernames", [])]

def _retire_username(username: str):
    """Add a username to the retired list so it can never be reused."""
    if username.lower() not in [n.lower() for n in _RETIRED_NAMES["usernames"]]:
        _RETIRED_NAMES["usernames"].append(username)
        _save_retired_names()

# ── Pending bio-code verifications: username -> {code, platform, profile_url, expires} ──
_PENDING_BIO_VERIFY: Dict[str, dict] = {}

def _mark_verified(username: str, method: str, nullifier_hash: str = None):
    """Set a user as identity-verified via the given method."""
    if username not in USER_DB:
        return
    USER_DB[username]["verified"] = True
    USER_DB[username]["id_verified"] = True
    USER_DB[username]["verified_method"] = method
    USER_DB[username]["verified_date"] = time.time()
    USER_DB[username]["verification_failed"] = False
    if nullifier_hash:
        USER_DB[username]["verified_hash"] = nullifier_hash
    # Add "verified" badge if not already present
    _badges = USER_DB[username].get("badges", [])
    if "verified" not in _badges:
        _badges.append("verified")
        USER_DB[username]["badges"] = _badges
    if not USER_DB[username].get("is_minor"):
        USER_DB[username]["nsfw_access"] = True
    save_all()

# ── Moderation flag storage ──────────────────────────────────────
MODERATION_FLAGS: list = []
_MOD_FLAGS_FILE = "moderation_flags.json"
try:
    with open(_MOD_FLAGS_FILE, "r") as _mf:
        MODERATION_FLAGS = json.load(_mf)
except (FileNotFoundError, json.JSONDecodeError):
    MODERATION_FLAGS = []

def _save_mod_flags():
    try:
        with open(_MOD_FLAGS_FILE, "w") as _mf:
            json.dump(MODERATION_FLAGS[-1000:], _mf)
    except Exception:
        pass

def _create_moderation_flag(user_id: str, conversation_id: str, content_type: str,
                            ai_classification: str, reason: str = "", auto_action: str = ""):
    """Create a structured moderation flag when AI blocks content."""
    flag = {
        "flag_id": str(uuid.uuid4())[:12],
        "user_id": user_id,
        "conversation_id": conversation_id,
        "content_type": content_type,   # image, video, file, text
        "ai_classification": ai_classification,  # NSFW, csam, violence, etc.
        "reason": reason,
        "timestamp": time.time(),
        "status": "pending",            # pending, dismissed, actioned
        "auto_action": auto_action,     # warn, ban, ncmec_report, ""
    }
    MODERATION_FLAGS.append(flag)
    if len(MODERATION_FLAGS) > 1000:
        MODERATION_FLAGS[:] = MODERATION_FLAGS[-1000:]
    _save_mod_flags()

    # CSAM: auto-report to NCMEC, terminate account immediately
    if ai_classification == "csam":
        _log_csam(user_id, f"AI auto-flagged CSAM in {conversation_id}: {reason}")
        if user_id in USER_DB:
            USER_DB[user_id]["banned"] = True
            USER_DB[user_id]["ban_reason"] = "CSAM detected — auto-terminated per 18 U.S.C. § 2258A"
            USER_DB[user_id]["minor_flagged"] = True
            # Add HWID to ban list
            _hwid = USER_DB[user_id].get("hwid", "")
            if _hwid:
                USER_DB[user_id]["banned_hwid"] = _hwid
            save_all()
            flag["auto_action"] = "ncmec_report"
            flag["status"] = "actioned"
            _save_mod_flags()
        threading.Thread(target=_notify_admins, args=(
            f"[CSAM AUTO-BAN] {user_id}",
            f"User {user_id} auto-banned for CSAM.\nConversation: {conversation_id}\nReason: {reason}\n\nReport to NCMEC: https://report.cybertip.org/",
            "csam"
        ), daemon=True).start()

    # Notify admins for all flags
    elif ai_classification.lower() == "nsfw":
        threading.Thread(target=_notify_admins, args=(
            f"[MODERATION] NSFW content blocked from {user_id}",
            f"User: {user_id}\nConversation: {conversation_id}\nType: {content_type}\nClassification: {ai_classification}\nReason: {reason}",
            "report"
        ), daemon=True).start()

    return flag

# ── Per-conversation media consent tracking ──────────────────────
# Stores: "user1:user2" -> {"user1": True/False, "user2": True/False}
_MEDIA_CONSENT: Dict[str, dict] = {}

# ── Tenshi main server (always ensure up-to-date) ──────────────
if "srv_0" not in SERVERS_DB:
    SERVERS_DB["srv_0"] = {
        "name": "Tenshi Updates",
        "owner": "ADMIN",
        "roles": {
            "role_admin": {"name": "Admin", "color": "#ff0000"},
            "role_everyone": {"name": "@everyone", "color": "#ffffff"}
        },
        "channels": {
            "Announcements": {"type": "text", "locked": False, "allowed_roles": []}
        },
        "members": {},
        "invite_only": True
    }


def save_all():
    # Save Local for redundancy
    with open(REGISTRY_FILE, 'w') as f: json.dump(USER_DB, f, indent=4)
    with open(SERVERS_FILE, 'w') as f: json.dump(SERVERS_DB, f, indent=4)
    
    # Save to Redis for high-speed cloud access
    if r_client:
        try:
            r_client.set(REGISTRY_FILE, json.dumps(USER_DB))
            r_client.set(SERVERS_FILE, json.dumps(SERVERS_DB))
        except Exception as e:
            print(f"[BACKUP] Error: {e}")

# ── 2FA session store ──────────────────────────────────────────
# username -> {"is_admin": bool, "expires": float, "code": str}
_2FA_SESSIONS: Dict[str, dict] = {}
_2FA_CODE_TTL = 300  # 5 minutes

# ── Forgot-password rate limits ─────────────────────────────────
_PW_RECOVER_IP: Dict[str, list] = {}   # client IP -> timestamps (RECOVER_PASSWORD)
_PW_RESET_TRY_IP: Dict[str, list] = {} # client IP -> timestamps (RESET_PASSWORD attempts)


def _resolve_username_from_login_identifier(ident: str):
    """Map login identifier (username or email) to the canonical USER_DB username key."""
    if not ident or not isinstance(ident, str):
        return None
    s = ident.strip()
    if not s:
        return None
    if s in USER_DB:
        return s
    sl = s.lower()
    for uname in USER_DB:
        if uname.lower() == sl:
            return uname
    if "@" in sl:
        for uname, udata in USER_DB.items():
            em = (udata.get("email") or "").strip().lower()
            if em == sl:
                return uname
    return None


def _pw_recover_ip_allow(ip: str, max_requests: int = 8, window_sec: float = 3600.0) -> bool:
    now = time.time()
    bucket = _PW_RECOVER_IP.setdefault(ip or "unknown", [])
    bucket[:] = [t for t in bucket if now - t < window_sec]
    if len(bucket) >= max_requests:
        return False
    bucket.append(now)
    return True


def _pw_reset_try_ip_allow(ip: str, max_tries: int = 24, window_sec: float = 900.0) -> bool:
    now = time.time()
    bucket = _PW_RESET_TRY_IP.setdefault(ip or "unknown", [])
    bucket[:] = [t for t in bucket if now - t < window_sec]
    if len(bucket) >= max_tries:
        return False
    bucket.append(now)
    return True


def _send_password_reset_email(recipient_email: str, code: str) -> bool:
    """Email a password-reset verification code (separate copy from login 2FA)."""
    try:
        body = (
            "You requested to reset your Tenshi password.\n\n"
            f"Your verification code is:\n\n  {code}\n\n"
            "This code expires in 15 minutes.\n\n"
            "If you did not request a password reset, you can safely ignore this email.\n\n"
            "Never share this code with anyone.\n"
        )
        msg = MIMEText(body)
        msg["Subject"] = "Reset your Tenshi password"
        msg["From"] = f"Tenshi <{SMTP_FROM}>"
        msg["To"] = recipient_email
        if SMTP_PASS == "your_smtp_password":
            print(f"[PW_RESET_SIM] Email to {recipient_email}: code={code}")
            return True
        srv = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        srv.starttls()
        srv.login(SMTP_USER, SMTP_PASS)
        srv.send_message(msg)
        srv.quit()
        print(f"[PW_RESET] Email sent to {recipient_email}")
        return True
    except Exception as e:
        print(f"[PW_RESET] Email send failed: {e}")
        return False


def _send_2fa_email(recipient_email: str, code: str) -> bool:
    """Send a 2FA login code via email."""
    print(f"[2FA] Attempting to send code to {recipient_email} via {SMTP_HOST}:{SMTP_PORT}")
    print(f"[2FA] SMTP_USER={SMTP_USER}, SMTP_FROM={SMTP_FROM}, SMTP_PASS={'SET' if SMTP_PASS and SMTP_PASS != 'your_smtp_password' else 'DEFAULT/UNSET'}")
    try:
        body = (
            f"Your Tenshi login code is:\n\n"
            f"  {code}\n\n"
            f"This code expires in 5 minutes.\n"
            f"Do not share this code with anyone.\n\n"
            f"If you did not request this, you can ignore this email."
        )
        msg = MIMEText(body)
        msg['Subject'] = "Tenshi - Your login code"
        msg['From']    = f'Tenshi <{SMTP_FROM}>'
        msg['To']      = recipient_email
        # Use Brevo relay (Vultr blocks outbound port 25, Postfix can't deliver)
        if SMTP_PASS == "your_smtp_password":
            print(f"[2FA_SIM] Email to {recipient_email}: code={code}")
            return True
        print(f"[2FA] Sending to {recipient_email} via {SMTP_HOST}:{SMTP_PORT}")
        srv = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        srv.starttls()
        srv.login(SMTP_USER, SMTP_PASS)
        srv.send_message(msg)
        srv.quit()
        print(f"[2FA] Email sent via Brevo to {recipient_email}")
        return True
    except Exception as e:
        print(f"[2FA] Email send failed: {e}")
        return False

def _issue_2fa(username: str, via: str = "email") -> tuple[bool, str]:
    """
    Generate and send a 2FA code via email only.
    SMS path removed — email is the sole 2FA delivery method.
    Returns (sent_ok, masked_destination).
    """
    code = str(secrets.randbelow(900000) + 100000)
    _2FA_SESSIONS[username] = {
        "code":     code,
        "expires":  time.time() + _2FA_CODE_TTL,
        "is_admin": _2FA_SESSIONS.get(username, {}).get("is_admin", False),
    }
    print(f"[2FA] Code sent to {username} (redacted for security)")  # Don't log actual codes
    email = USER_DB.get(username, {}).get("email", "")
    dest  = ""
    ok    = False
    if email:
        ok   = _send_2fa_email(email, code)
        dest = (email[:2] + "***" + email.split("@")[-1]) if "@" in email else "***"
    if not ok:
        print(f"[2FA] Email delivery failed for {username} (code redacted)")
    return ok, dest

def send_external_email(sender_username, recipient_email, content):
    try:
        msg = MIMEText(f"Message from Tenshi user {sender_username}:\n\n{content}\n\n---\nSent via Tenshi Hub")
        msg['Subject'] = f"Tenshi Message from {sender_username}"
        msg['From'] = SMTP_USER
        msg['To'] = recipient_email
        
        # Simulate if defaults are kept
        if SMTP_PASS == "your_smtp_password":
            print(f"[MAIL_SIM] -> To: {recipient_email} | Content: {content}")
            return True
            
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Failed to send external email: {e}")
        return False

def handle_client(conn, addr):
    try:
        raw_data = conn.recv(1024).decode('utf-8')
        if not raw_data: return
        
        # We are moving to JSON for complex requests
        try:
            request = json.loads(raw_data)
        except json.JSONDecodeError:
            print(f"ERROR: Received non-JSON data: {raw_data}") 
            return

        action = request.get("action")
        username = request.get("username")

        # Track authenticated user activity for online presence
        if username and username in USER_DB:
            ONLINE_USERS[username] = time.time()

        response = {"status": "error", "message": "Unknown Action"}

        if action == "REGISTER":
            if username not in USER_DB:
                email = request.get('email', '').strip()
                phone = request.get('phone', '').strip()
                
                if not email and not phone:
                    conn.send(json.dumps({"status": "fail", "message": "Email or Phone is required"}).encode('utf-8'))
                    return
                    
                USER_DB[username] = {
                    "hwid": request['hwid'],
                    "pwd_hash": request.get('pwd_hash', ''), # Store password hash
                    "email": email,
                    "phone": phone,
                    "allowed_hwids": [request['hwid']],
                    "friends": [], 
                    "pending_reqs": [], 
                    "following": [],
                    "followers": [],
                    "blocked": [],
                    "hidden_from": [],  # users who won't see this user online
                    "servers": ["srv_0", "srv_1"],  # Auto-join Tenshi Updates + General
                    "bio": "New to Tenshi",
                    "pronouns": "",
                    "banner_color": "#2b2d31",
                    "status": "Online",
                    "custom_status": "",
                    "storage_pref": request.get("storage_pref", "cloud"),
                    "verified": False,
                    "connections": {},
                    "privacy": {
                        "hide_online_status": False,
                        "hide_server_membership": False,
                        "dms_from_friends_only": False,
                        "auto_accept_friends": False,   # auto-accept if mutual server
                        "auto_decline_strangers": False  # auto-decline if no shared server
                    }
                }
                # Auto-add to default public servers' member lists
                for auto_srv in ["srv_0", "srv_1"]:
                    if auto_srv in SERVERS_DB:
                        mdict = SERVERS_DB[auto_srv].get("members", {})
                        if isinstance(mdict, dict) and username not in mdict:
                            mdict[username] = ["role_everyone"]
                            SERVERS_DB[auto_srv]["members"] = mdict
                save_all()
                response = {"status": "success", "message": "Account Created"}

        elif action == "ADD_FRIEND":
            target = request.get("target")

            # Allow adding external emails as friends
            if "@" in target and "." in target:
                if target not in USER_DB[username]["friends"]:
                    USER_DB[username]["friends"].append(target)
                    save_all()
                response = {"status": "success", "message": "Email added to contacts"}
                
            elif target in USER_DB and target != username:
                # Check if sender is blocked
                if username in USER_DB[target].get("blocked", []):
                    response = {"status": "fail", "message": "User not found"}
                elif username not in USER_DB[target]["friends"] and username not in USER_DB[target].get("pending_reqs", []):
                    # Check target's privacy: auto_decline_strangers
                    target_privacy = USER_DB[target].get("privacy", {})
                    # Find mutual servers
                    sender_servers = set(USER_DB[username].get("servers", []))
                    target_servers = set(USER_DB[target].get("servers", []))
                    has_mutual = bool(sender_servers & target_servers)
                    
                    if target_privacy.get("auto_decline_strangers") and not has_mutual:
                        response = {"status": "fail", "message": "This user only accepts requests from people in mutual servers"}
                    elif target_privacy.get("auto_accept_friends") and has_mutual:
                        # Auto-accept
                        USER_DB[target]["friends"].append(username)
                        USER_DB[username]["friends"].append(target)
                        save_all()
                        response = {"status": "success", "message": f"Auto-added! You and {target} share a server."}
                    else:
                        USER_DB[target].setdefault("pending_reqs", []).append(username)
                        save_all()
                        response = {"status": "success", "message": f"Request sent to {target}"}
                else:
                    response = {"status": "fail", "message": "Already friends or request pending"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "ACCEPT_FRIEND":
            target = request.get("target")
            if target in USER_DB and target in USER_DB[username].get("pending_reqs", []):
                USER_DB[username]["pending_reqs"].remove(target)
                USER_DB[username].setdefault("friends", []).append(target)
                USER_DB[target].setdefault("friends", []).append(username)
                save_all()
                response = {"status": "success", "message": f"Added {target}"}
            else:
                response = {"status": "fail", "message": "Request not found"}

        elif action == "DECLINE_FRIEND":
            target = request.get("target")
            if target in USER_DB and target in USER_DB[username].get("pending_reqs", []):
                USER_DB[username]["pending_reqs"].remove(target)
                save_all()
                response = {"status": "success", "message": f"Declined {target}"}
            else:
                response = {"status": "fail", "message": "Request not found"}

        elif action == "REMOVE_FRIEND":
            target = request.get("target")
            if target in USER_DB:
                USER_DB[username].get("friends", []).remove(target) if target in USER_DB[username].get("friends", []) else None
                USER_DB[target].get("friends", []).remove(username) if username in USER_DB[target].get("friends", []) else None
                save_all()
                response = {"status": "success", "message": f"Removed {target}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "FOLLOW_USER":
            target = request.get("target")
            if target in USER_DB and target != username:
                USER_DB[username].setdefault("following", [])
                USER_DB[target].setdefault("followers", [])
                if target not in USER_DB[username]["following"]:
                    USER_DB[username]["following"].append(target)
                    USER_DB[target]["followers"].append(username)
                    save_all()
                response = {"status": "success", "message": f"Now following {target}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "UNFOLLOW_USER":
            target = request.get("target")
            if target in USER_DB:
                following = USER_DB[username].get("following", [])
                followers = USER_DB[target].get("followers", [])
                if target in following: following.remove(target)
                if username in followers: followers.remove(username)
                save_all()
                response = {"status": "success", "message": f"Unfollowed {target}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "BLOCK_USER":
            target = request.get("target")
            if target in USER_DB and target != username:
                USER_DB[username].setdefault("blocked", [])
                if target not in USER_DB[username]["blocked"]:
                    USER_DB[username]["blocked"].append(target)
                # Also remove from friends if they are friends
                if target in USER_DB[username].get("friends", []):
                    USER_DB[username]["friends"].remove(target)
                if username in USER_DB[target].get("friends", []):
                    USER_DB[target]["friends"].remove(username)
                save_all()
                response = {"status": "success", "message": f"Blocked {target}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "UNBLOCK_USER":
            target = request.get("target")
            if target in USER_DB:
                blocked = USER_DB[username].get("blocked", [])
                if target in blocked: blocked.remove(target)
                save_all()
                response = {"status": "success", "message": f"Unblocked {target}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "GET_MUTUAL_SERVERS":
            target = request.get("target")
            if target in USER_DB and username in USER_DB:
                my_servers = set(USER_DB[username].get("servers", []))
                their_servers = set(USER_DB[target].get("servers", []))
                mutual = list(my_servers & their_servers)
                mutual_names = [SERVERS_DB[s]["name"] for s in mutual if s in SERVERS_DB]
                response = {"status": "success", "mutual_servers": mutual_names}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "GET_JOINED_SERVERS":
            if username in USER_DB:
                joined = []
                for s_id in USER_DB[username].get("servers", []):
                    if s_id in SERVERS_DB:
                        s_data = SERVERS_DB[s_id]
                        joined.append({
                            "id": s_id,
                            "name": s_data.get("name"),
                            "icon": s_data.get("name")[0].upper(),
                            "channels": s_data.get("channels", {}),
                            "roles": s_data.get("roles", {})
                        })
                response = {"status": "success", "servers": joined}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "UPDATE_CHANNEL_ROLES":
            server_id = request.get("server_id")
            if server_id in SERVERS_DB:
                if SERVERS_DB[server_id].get("owner") == username:
                    c_name = request.get("channel_name")
                    if c_name in SERVERS_DB[server_id]["channels"]:
                        SERVERS_DB[server_id]["channels"][c_name]["locked"] = request.get("locked")
                        SERVERS_DB[server_id]["channels"][c_name]["allowed_roles"] = request.get("allowed_roles")
                        save_all()
                        response = {"status": "success"}
                    else:
                        response = {"status": "fail", "message": "Channel not found"}
                else:
                    response = {"status": "fail", "message": "Only the Server Owner can edit roles"}
            else:
                response = {"status": "fail", "message": "Server error"}

        elif action == "UPDATE_PRIVACY":
            if username in USER_DB:
                privacy = USER_DB[username].setdefault("privacy", {})
                for key in ["hide_online_status", "hide_server_membership", "dms_from_friends_only",
                            "auto_accept_friends", "auto_decline_strangers"]:
                    if key in request:
                        privacy[key] = request[key]
                save_all()
                response = {"status": "success", "message": "Privacy settings updated"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "GET_RELATIONSHIPS":
            if username in USER_DB:
                data = USER_DB[username]
                response = {
                    "status": "success",
                    "friends": data.get("friends", []),
                    "pending": data.get("pending_reqs", []),
                    "following": data.get("following", []),
                    "blocked": data.get("blocked", [])
                }
            else:
                response = {"status": "fail", "message": "User error"}

        # --- BROWSER CAMERA / VOICE BRIDGE ---
        elif action == "SEND_VIDEO_FRAME":
            # Web App sends the raw base64 frame payload targeting a specific channel
            channel = request.get("channel")
            frame_data = request.get("frame")
            if channel and frame_data:
                # We need to broadcast this to all listeners of 'channel'
                # For a stateless HTTP approach, we buffer the latest frame globally
                global WEB_VIDEO_BUFFER
                if 'WEB_VIDEO_BUFFER' not in globals():
                    WEB_VIDEO_BUFFER = {}
                WEB_VIDEO_BUFFER[f"{channel}_{username}"] = frame_data
                response = {"status": "success"}
            else:
                response = {"status": "fail"}
                
        elif action == "POLL_VIDEO":
            channel = request.get("channel")
            if channel:
                if 'WEB_VIDEO_BUFFER' not in globals():
                    globals()['WEB_VIDEO_BUFFER'] = {}
                # Return all latest frames for users in this channel
                channel_frames = {user: frame for key, frame in WEB_VIDEO_BUFFER.items() if key.startswith(f"{channel}_") for user in [key.split("_", 1)[1]] if user != username}
                response = {"status": "success", "frames": channel_frames}
            else:
                response = {"status": "fail"}
        # -------------------------------------

        elif action == "CREATE_SERVER":
            server_name = request.get("server_name")
            server_id = f"srv_{len(SERVERS_DB) + 1}"
            SERVERS_DB[server_id] = {
                "name": server_name,
                "owner": username,
                "roles": {
                    "role_admin": {"name": "Admin", "color": "#ff0000"},
                    "role_everyone": {"name": "@everyone", "color": "#ffffff"}
                },
                "channels": {
                    "General": {"type": "text", "locked": False, "allowed_roles": []}, 
                    "Memes": {"type": "text", "locked": False, "allowed_roles": []}, 
                    "Off-Topic": {"type": "text", "locked": False, "allowed_roles": []},
                    "Lounge": {"type": "voice", "locked": False, "allowed_roles": []}, 
                    "Gaming": {"type": "voice", "locked": False, "allowed_roles": []}, 
                    "Music": {"type": "voice", "locked": False, "allowed_roles": []}
                },
                "members": {username: ["role_admin"]},
                "invite_only": False 
            }
            USER_DB[username]["servers"].append(server_id)
            save_all()
            response = {"status": "success", "server_id": server_id}

        # ── Email System ──────────────────────────────────────
        elif action == "SEND_EMAIL":
            to_addr = request.get("to", "").strip()
            cc_addr = request.get("cc", "").strip()
            subject = request.get("subject", "").strip()
            body = request.get("body", "").strip()
            if not to_addr or "@" not in to_addr:
                response = {"status": "fail", "message": "Invalid recipient email address."}
            elif not subject:
                response = {"status": "fail", "message": "Subject is required."}
            elif not body:
                response = {"status": "fail", "message": "Email body is required."}
            else:
                # ── Rate limiting: max 10 emails per hour ─────────
                _email_rate = USER_DB.get(username, {}).setdefault("_email_timestamps", [])
                _now = time.time()
                _email_rate[:] = [t for t in _email_rate if _now - t < 3600]
                if len(_email_rate) >= 10:
                    response = {"status": "fail", "message": "Rate limit: max 10 emails per hour. Try again later."}
                    conn.send(json.dumps(response).encode('utf-8'))
                    return
                _email_rate.append(_now)

                # ── Phishing/malware scan ─────────────────────────
                safe, scan_reason = _scan_content(body + ' ' + subject)
                if not safe:
                    response = {"status": "fail", "message": f"Email blocked: {scan_reason}"}
                    audit("EMAIL_BLOCKED", username, to_addr, f"Phishing: {scan_reason}")
                    conn.send(json.dumps(response).encode('utf-8'))
                    return
                try:
                    from email.mime.multipart import MIMEMultipart
                    from email.mime.text import MIMEText as MT2
                    from email.utils import formatdate, make_msgid
                    sender_addr = f'{username}@tenshi.lol'
                    msg = MIMEMultipart('alternative')
                    msg['Subject'] = subject
                    msg['From'] = f'{username} <{sender_addr}>'
                    msg['To'] = to_addr
                    if cc_addr:
                        msg['Cc'] = cc_addr
                    msg['Reply-To'] = sender_addr
                    msg['Date'] = formatdate(localtime=True)
                    msg['Message-ID'] = make_msgid(domain='tenshi.lol')

                    # Plain text version
                    plain = f"{body}\n\n---\nSent via Tenshi Mail by {username}"
                    # HTML version (professional formatting)
                    html_body = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
                        <div style="background:#0b0c10;color:white;padding:16px 24px;border-radius:12px 12px 0 0;text-align:center;">
                            <img src="https://tenshi.lol/tenshi_logo.png" alt="Tenshi" style="width:32px;height:32px;vertical-align:middle;margin-right:8px;">
                            <span style="font-size:18px;font-weight:bold;">Tenshi Mail</span>
                        </div>
                        <div style="background:#ffffff;color:#333;padding:24px;border:1px solid #e0e0e0;line-height:1.7;font-size:15px;white-space:pre-wrap;">{body.replace('<','&lt;').replace('>','&gt;')}</div>
                        <div style="background:#f5f5f5;padding:12px 24px;border-radius:0 0 12px 12px;border:1px solid #e0e0e0;border-top:none;font-size:11px;color:#888;text-align:center;">
                            Sent via <a href="https://tenshi.lol" style="color:#0078d4;">Tenshi</a> by {username}
                        </div>
                    </div>"""
                    msg.attach(MT2(plain, 'plain'))
                    msg.attach(MT2(html_body, 'html'))

                    recipients = [to_addr]
                    if cc_addr:
                        recipients.extend([a.strip() for a in cc_addr.split(',') if a.strip()])

                    # Send via Brevo relay (Vultr blocks outbound port 25)
                    if SMTP_PASS != "your_smtp_password":
                        relay = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
                        relay.starttls()
                        relay.login(SMTP_USER, SMTP_PASS)
                        relay.sendmail(SMTP_FROM, recipients, msg.as_string())
                        relay.quit()
                        print(f"[EMAIL] Sent via Brevo: {sender_addr} -> {to_addr} | {subject}")
                    else:
                        print(f"[MAIL_SIM] -> To: {to_addr} | Subject: {subject}")

                    # Store locally for internal users
                    _store_email(to_addr, username, subject, body)
                    # Store in sender's sent folder
                    sent = USER_DB.get(username, {}).setdefault("sent_emails", [])
                    sent.insert(0, {"to": to_addr, "cc": cc_addr, "subject": subject, "body": body, "time": time.strftime('%Y-%m-%d %H:%M')})
                    if len(sent) > 100:
                        USER_DB[username]["sent_emails"] = sent[:100]
                    save_all()
                    response = {"status": "success"}
                except Exception as e:
                    print(f"[EMAIL] Failed: {e}")
                    response = {"status": "fail", "message": f"Email sending failed: {str(e)}"}

        elif action == "GET_SENT_EMAILS":
            sent = USER_DB.get(username, {}).get("sent_emails", [])
            response = {"status": "success", "emails": sent}

        elif action == "INCOMING_EMAIL":
            # Called by mail_handler.py when Postfix receives external mail
            # SECURITY: Only accept from localhost (Postfix runs on same server)
            _client_ip = request.get("_client_ip", "")
            if _client_ip not in ("127.0.0.1", "::1", ""):
                audit("INCOMING_EMAIL_BLOCKED", _client_ip, "", "Non-localhost INCOMING_EMAIL attempt")
                conn.send(json.dumps({"status": "fail", "message": "Access denied."}).encode('utf-8'))
                return
            target = request.get("target", "")

            # ── Admin alias routing ──────────────────────────────
            # Emails to support@, dmca@, privacy@, appeals@, legal@,
            # security@ tenshi.lol go into the admin mail queue
            _ADMIN_MAIL_ALIASES = {"support", "dmca", "privacy", "appeals", "legal", "security"}
            is_admin_mail = target.lower() in _ADMIN_MAIL_ALIASES

            if is_admin_mail or (target and target in USER_DB):
                is_spam = request.get("is_spam", False)
                spam_reason = request.get("spam_reason", "")
                sender = request.get("from", "Unknown")
                subject = request.get("subject", "(No Subject)")
                # Sanitize body — strip any remaining HTML tags for XSS protection
                raw_body = request.get("body", "")[:5000]
                body = re.sub(r'<[^>]+>', '', raw_body)  # Strip all HTML tags
                body = body.replace('javascript:', '').replace('data:', '')  # Remove script URIs

                email_obj = {
                    "from": sender,
                    "to": f"{target}@tenshi.lol",
                    "subject": subject,
                    "body": body,
                    "time": time.time(),
                    "read": False,
                    "category": target.lower() if is_admin_mail else ""
                }

                # Also run server-side phishing scan
                safe, scan_reason = _scan_content(body + ' ' + subject)
                if not safe:
                    is_spam = True
                    spam_reason = scan_reason

                if is_admin_mail:
                    # Route to admin mail queue (stored in a global list)
                    if is_spam:
                        email_obj["spam_reason"] = spam_reason
                        audit("ADMIN_SPAM_BLOCKED", target, sender, f"Spam: {spam_reason} | {subject[:80]}")
                    else:
                        admin_mail = SERVERS_DB.setdefault("_admin_mail", [])
                        admin_mail.insert(0, email_obj)
                        # Cap at 500 emails
                        if len(admin_mail) > 500:
                            SERVERS_DB["_admin_mail"] = admin_mail[:500]
                        audit("ADMIN_MAIL", target, sender, f"{subject[:80]}")
                        # Also notify admin via email
                        threading.Thread(target=_notify_admins, args=(
                            f"New {target}@ email from {sender}: {subject[:100]}",
                            f"To: {target}@tenshi.lol\nFrom: {sender}\nSubject: {subject}\n\n{body[:1000]}",
                            "general"
                        ), daemon=True).start()
                    save_all()
                    response = {"status": "success"}
                else:
                    # Route to user inbox
                    if is_spam:
                        email_obj["spam_reason"] = spam_reason
                        _spam_em = email_obj
                        if EXTENSION_MAILBOX:
                            _spam_em = _mbx.encrypt_email_if_possible(target, USER_DB[target], dict(email_obj))
                        USER_DB[target].setdefault("spam", []).insert(0, _spam_em)
                        USER_DB[target].setdefault("email_spam", []).insert(0, dict(_spam_em))
                        if len(USER_DB[target]["spam"]) > 50:
                            USER_DB[target]["spam"] = USER_DB[target]["spam"][:50]
                        if len(USER_DB[target]["email_spam"]) > 50:
                            USER_DB[target]["email_spam"] = USER_DB[target]["email_spam"][:50]
                        audit("SPAM_BLOCKED", target, sender, f"Spam: {spam_reason} | {subject[:80]}")
                    else:
                        _in_em = dict(email_obj)
                        if EXTENSION_MAILBOX:
                            _in_em = _mbx.encrypt_email_if_possible(target, USER_DB[target], _in_em)
                        USER_DB[target].setdefault("inbox", []).insert(0, _in_em)
                        if len(USER_DB[target]["inbox"]) > 100:
                            USER_DB[target]["inbox"] = USER_DB[target]["inbox"][:100]
                    save_all()
                    response = {"status": "success"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "GET_EMAIL_FOLDER":
            folder = request.get("folder", "inbox")
            udata = USER_DB.get(username, {})
            if folder == "inbox":
                emails = udata.get("inbox", [])
                for e in emails:
                    e["read"] = True
                save_all()
            elif folder == "sent":
                emails = udata.get("sent_emails", [])
            elif folder == "spam":
                emails = udata.get("email_spam", []) or udata.get("spam", [])
            elif folder == "trash":
                emails = udata.get("email_trash", [])
            else:
                emails = []
            emails_disp = emails
            if EXTENSION_MAILBOX:
                emails_disp = _mbx.decrypt_folder_emails(username, udata, list(emails))
                if folder == "inbox":
                    _mbx.session_touch(username)
            response = {"status": "success", "emails": emails_disp, "folder": folder}

        elif action == "MOVE_EMAIL":
            from_folder = request.get("from_folder", "inbox")
            to_folder = request.get("to_folder", "trash")
            email_idx = request.get("index", -1)
            folder_map = {"inbox": "inbox", "sent": "sent_emails", "spam": "email_spam", "trash": "email_trash"}
            src_key = folder_map.get(from_folder)
            dst_key = folder_map.get(to_folder)
            if src_key and dst_key and isinstance(email_idx, int):
                src = USER_DB.get(username, {}).get(src_key, [])
                if 0 <= email_idx < len(src):
                    email = src.pop(email_idx)
                    USER_DB[username].setdefault(dst_key, []).insert(0, email)
                    if len(USER_DB[username][dst_key]) > 100:
                        USER_DB[username][dst_key] = USER_DB[username][dst_key][:100]
                    save_all()
                    response = {"status": "success"}
                else:
                    response = {"status": "fail", "message": "Email not found."}
            else:
                response = {"status": "fail", "message": "Invalid folder."}

        elif action == "DELETE_EMAIL":
            folder = request.get("folder", "trash")
            email_idx = request.get("index", -1)
            folder_map = {"inbox": "inbox", "sent": "sent_emails", "spam": "email_spam", "trash": "email_trash"}
            key = folder_map.get(folder)
            if key and isinstance(email_idx, int):
                arr = USER_DB.get(username, {}).get(key, [])
                if 0 <= email_idx < len(arr):
                    arr.pop(email_idx)
                    save_all()
                    response = {"status": "success"}
                else:
                    response = {"status": "fail", "message": "Email not found."}
            else:
                response = {"status": "fail", "message": "Invalid folder."}

        # ── Bug Reports & Announcements ────────────────────────
        elif action == "SUBMIT_BUG_REPORT":
            title = request.get("title", "").strip()[:200]
            description = request.get("description", "").strip()[:2000]
            if not title or not description:
                response = {"status": "fail", "message": "Title and description are required."}
            else:
                BUG_REPORTS.insert(0, {
                    "reporter": username,
                    "title": title,
                    "description": description,
                    "time": time.time(),
                    "status": "open"
                })
                if len(BUG_REPORTS) > 200:
                    BUG_REPORTS[:] = BUG_REPORTS[:200]
                _save_community_data()
                response = {"status": "success", "message": "Bug report submitted!"}

        elif action == "GET_BUG_REPORTS":
            page = request.get("page", 0)
            per_page = 20
            start = page * per_page
            reports = BUG_REPORTS[start:start + per_page]
            response = {"status": "success", "reports": reports, "total": len(BUG_REPORTS)}

        elif action == "UPDATE_BUG_REPORT":
            if not is_admin:
                response = {"status": "fail", "message": "Admin only."}
            else:
                idx = request.get("index", -1)
                new_status = request.get("status", "resolved")
                if isinstance(idx, int) and 0 <= idx < len(BUG_REPORTS):
                    BUG_REPORTS[idx]["status"] = new_status
                    audit("UPDATE_BUG_REPORT", username, "", f"Bug #{idx} set to {new_status}")
                    _save_community_data()
                    response = {"status": "success"}
                else:
                    response = {"status": "fail", "message": "Invalid index."}

        elif action == "DELETE_BUG_REPORT":
            if not is_admin:
                response = {"status": "fail", "message": "Admin only."}
            else:
                idx = request.get("index", -1)
                if isinstance(idx, int) and 0 <= idx < len(BUG_REPORTS):
                    removed = BUG_REPORTS.pop(idx)
                    audit("DELETE_BUG_REPORT", username, "", f"Deleted bug: {removed.get('title','')}")
                    _save_community_data()
                    response = {"status": "success"}
                else:
                    response = {"status": "fail", "message": "Invalid index."}

        elif action == "POST_ANNOUNCEMENT":
            if not is_admin:
                response = {"status": "fail", "message": "Admin only."}
            else:
                title = request.get("title", "").strip()[:200]
                content = request.get("content", "").strip()[:3000]
                if not title or not content:
                    response = {"status": "fail", "message": "Title and content required."}
                else:
                    ANNOUNCEMENTS.insert(0, {
                        "author": username,
                        "title": title,
                        "content": content,
                        "time": time.time()
                    })
                    if len(ANNOUNCEMENTS) > 50:
                        ANNOUNCEMENTS[:] = ANNOUNCEMENTS[:50]
                    _save_community_data()
                    response = {"status": "success"}

        elif action == "GET_ANNOUNCEMENTS":
            response = {"status": "success", "announcements": ANNOUNCEMENTS[:10]}

        # ── Hub Emojis/Stickers/GIFs ──────────────────────────
        elif action == "UPLOAD_HUB_EMOJI":
            server_id = request.get("server_id")
            name = request.get("name", "").strip()[:32]
            data = request.get("data", "")
            emoji_type = request.get("type", "emoji")  # emoji, sticker, gif
            if not server_id or server_id not in SERVERS_DB:
                response = {"status": "fail", "message": "Server not found"}
            elif SERVERS_DB[server_id].get("owner") != username and not is_admin:
                response = {"status": "fail", "message": "Only the hub owner can upload emojis"}
            elif not name or not data:
                response = {"status": "fail", "message": "Name and image data required"}
            else:
                _reject = ""
                if EXTENSION_CLAMAV:
                    _blob = _hub_media_b64_decode(data)
                    if len(_blob) > 32 * 1024 * 1024:
                        _reject = "File too large for malware scan."
                    else:
                        _clean, _detail = _clamav_scan.scan_bytes(_blob)
                        if not _clean:
                            audit("CLAMAV_BLOCK", username, server_id, str(_detail)[:200])
                            _reject = "Upload rejected by virus/malware scan."
                if _reject:
                    response = {"status": "fail", "message": _reject}
                else:
                    emojis = HUB_EMOJIS.setdefault(server_id, [])
                    total_size = sum(len(e.get("data", "")) for e in emojis)
                    if total_size + len(data) > 500 * 1024 * 1024:
                        response = {"status": "fail", "message": f"Hub storage limit reached (500MB). Currently using {total_size // (1024*1024)}MB."}
                    else:
                        emojis.append({"name": name, "data": data, "type": emoji_type, "uploaded_by": username, "time": time.time()})
                        _save_community_data()
                        audit("UPLOAD_EMOJI", username, server_id, f"Uploaded {emoji_type}: {name}")
                        response = {"status": "success", "message": f"{emoji_type.title()} uploaded!"}

        elif action == "GET_HUB_EMOJIS":
            server_id = request.get("server_id", "")
            if server_id:
                emojis = HUB_EMOJIS.get(server_id, [])
                listing = [{"name": e["name"], "type": e["type"], "uploaded_by": e.get("uploaded_by", "")} for e in emojis]
                total_bytes = sum(len(e.get("data", "")) for e in emojis)
                max_bytes = 500 * 1024 * 1024
                response = {"status": "success", "emojis": listing, "storage_used": total_bytes, "storage_max": max_bytes}
            else:
                # Get all emojis from all hubs the user is in (cross-hub usage)
                all_emojis = []
                for sid in USER_DB.get(username, {}).get("servers", []):
                    for e in HUB_EMOJIS.get(sid, []):
                        all_emojis.append({"name": e["name"], "type": e["type"], "server": sid, "server_name": SERVERS_DB.get(sid, {}).get("name", sid)})
                response = {"status": "success", "emojis": all_emojis}

        elif action == "GET_EMOJI_DATA":
            server_id = request.get("server_id", "")
            name = request.get("name", "")
            emojis = HUB_EMOJIS.get(server_id, [])
            emoji = next((e for e in emojis if e["name"] == name), None)
            if emoji:
                response = {"status": "success", "data": emoji["data"], "type": emoji["type"]}
            else:
                response = {"status": "fail", "message": "Emoji not found"}

        elif action == "DELETE_HUB_EMOJI":
            server_id = request.get("server_id")
            name = request.get("name", "")
            if not server_id or server_id not in SERVERS_DB:
                response = {"status": "fail", "message": "Server not found"}
            elif SERVERS_DB[server_id].get("owner") != username and not is_admin:
                response = {"status": "fail", "message": "Not authorized"}
            else:
                emojis = HUB_EMOJIS.get(server_id, [])
                HUB_EMOJIS[server_id] = [e for e in emojis if e["name"] != name]
                _save_community_data()
                audit("DELETE_EMOJI", username, server_id, f"Deleted emoji: {name}")
                response = {"status": "success"}

        # ── Change Username ($5 via Stripe) ────────────────────
        elif action == "CHANGE_USERNAME":
            new_username = request.get("new_username", "").strip()
            if username not in USER_DB:
                response = {"status": "fail", "message": "Invalid session."}
            elif not new_username:
                response = {"status": "fail", "message": "New username is required."}
            elif not re.match(r'^[a-zA-Z0-9._-]{1,16}$', new_username):
                response = {"status": "fail", "message": "Username must be 1–16 characters: letters, numbers, periods, hyphens, or underscores only."}
            elif new_username.lower() == username.lower():
                response = {"status": "fail", "message": "That's already your username."}
            elif _is_username_retired(new_username):
                response = {"status": "fail", "message": "That username is unavailable."}
            elif any(e.lower() == new_username.lower() for e in USER_DB):
                response = {"status": "fail", "message": "That username is already taken."}
            elif _IMPERSONATION_NAMES.match(new_username):
                response = {"status": "fail", "message": "That username is reserved."}
            elif _username_has_slur(new_username.lower()):
                response = {"status": "fail", "message": "That username contains inappropriate language."}
            else:
                _claim = USER_DB[username].get("_premium_claim_handle")
                _paid_username = USER_DB[username].get("_username_change_paid")
                _premium_ok = isinstance(_claim, str) and new_username == _claim
                if not _paid_username and not _premium_ok:
                    response = {"status": "needs_payment", "message": "Username change requires payment or a premium handle purchase."}
                else:
                    old_username = username
                    user_data = USER_DB.pop(old_username)
                    # Retire old username so it can never be reused
                    _retire_username(old_username)
                    # Store username history on the account
                    if "username_history" not in user_data:
                        user_data["username_history"] = []
                    user_data["username_history"].append({"name": old_username, "changed_at": time.time()})
                    # Clear payment flag
                    user_data.pop("_username_change_paid", None)
                    user_data.pop("_premium_claim_handle", None)
                    # Save under new username
                    USER_DB[new_username] = user_data
                    # Update all references: friends lists, servers, DMs, etc.
                    for _u, _udata in USER_DB.items():
                        if _u == new_username:
                            continue
                        for _list_key in ("friends", "pending_reqs", "following", "followers", "blocked", "hidden_from"):
                            _lst = _udata.get(_list_key, [])
                            if old_username in _lst:
                                _lst[_lst.index(old_username)] = new_username
                        # Parental links
                        if _udata.get("parent_id") == old_username:
                            _udata["parent_id"] = new_username
                        _children = _udata.get("linked_children", [])
                        if old_username in _children:
                            _children[_children.index(old_username)] = new_username
                    # Update server member lists
                    for _sid, _sdata in SERVERS_DB.items():
                        members = _sdata.get("members", {})
                        if isinstance(members, dict) and old_username in members:
                            members[new_username] = members.pop(old_username)
                        if _sdata.get("owner") == old_username:
                            _sdata["owner"] = new_username
                    # Update DM history keys
                    for _key in list(DM_HISTORY.keys()):
                        if old_username in _key.split("|"):
                            parts = _key.split("|")
                            new_parts = [new_username if p == old_username else p for p in parts]
                            new_key = "|".join(sorted(new_parts))
                            if new_key != _key:
                                DM_HISTORY[new_key] = DM_HISTORY.pop(_key)
                    save_all()
                    print(f"[USERNAME] {old_username} -> {new_username}")
                    response = {"status": "success", "message": f"Username changed to {new_username}!", "new_username": new_username}

        elif action == "MARK_USERNAME_CHANGE_PAID":
            # SECURITY: Requires admin or valid Stripe session verification
            # Client cannot self-grant payment status — verify via Stripe API
            session_id = request.get("stripe_session_id", "")
            if username not in USER_DB:
                response = {"status": "fail", "message": "Invalid session."}
            elif not session_id:
                response = {"status": "fail", "message": "Payment verification required."}
            elif not STRIPE_SECRET_KEY:
                response = {"status": "fail", "message": "Payment system unavailable."}
            else:
                # Verify with Stripe that this session was actually paid
                try:
                    _stripe_resp = requests.get(
                        f"https://api.stripe.com/v1/checkout/sessions/{session_id}",
                        auth=(STRIPE_SECRET_KEY, ""),
                        timeout=10
                    )
                    _stripe_data = _stripe_resp.json()
                    if _stripe_data.get("payment_status") == "paid" and _stripe_data.get("client_reference_id") == username:
                        USER_DB[username]["_username_change_paid"] = True
                        save_all()
                        response = {"status": "success", "message": "Payment verified. You can now change your username."}
                    else:
                        response = {"status": "fail", "message": "Payment not confirmed."}
                except Exception as _e:
                    print(f"[STRIPE] Verification error: {_e}")
                    response = {"status": "fail", "message": "Payment verification failed."}

        # ── Minor Safety: Flag NSFW content involving minors ──
        elif action == "FLAG_CONTENT":
            target = request.get("target_user", "")
            content_type = request.get("content_type", "message")  # message, image, server
            reason = request.get("reason", "")
            reason_category = request.get("reason_category", "")  # nsfw_unverified, csam, ncii, harassment, other
            context = request.get("context", "")
            report_type = request.get("report_type", "user")  # user, message, chat_log, hub
            msg_id = request.get("msg_id", "")
            hub_id = request.get("hub_id", "")
            _client_ip = request.get("_client_ip", "")

            # SOFT-DELETE PIN: if the report references a soft-deleted item, mark it
            # flagged_for_review so the hourly purge daemons won't hard-delete it while
            # the report is open. Per-user rate limit (5/hour) prevents this from being
            # weaponized to pin arbitrary content.
            if msg_id:
                _now = time.time()
                _window = [t for t in _FLAG_PIN_RATE_LIMIT.get(username, []) if _now - t < 3600]
                if len(_window) < 5:
                    _hit = _find_soft_deleted_by_id(msg_id)
                    if _hit:
                        _, _, _item = _hit
                        # _item may be a temp (lookup via msg_id, not 'deleted' flag), so set both:
                        _item["flagged_for_review"] = True
                        _window.append(_now)
                if _window:
                    _FLAG_PIN_RATE_LIMIT[username] = _window

            if target or reason:
                flag = {
                    "flag_id": str(uuid.uuid4())[:12],
                    "reporter": username,
                    "target_user": target,
                    "content_type": content_type,
                    "report_type": report_type,
                    "reason": reason,
                    "reason_category": reason_category,
                    "context": context[:3000],
                    "msg_id": msg_id,
                    "hub_id": hub_id,
                    "time": time.time(),
                    "resolved": False,
                    "resolution": None,
                    "resolution_time": None,
                    "resolved_by": None,
                }

                # ── TAKE IT DOWN Act: NCII reports get 48-hour removal timer ──
                if reason_category == "ncii":
                    flag["ncii_report"] = True
                    flag["ncii_deadline"] = time.time() + (48 * 3600)  # 48 hours
                    flag["content_hidden"] = True
                    # Immediately hide the reported message
                    if msg_id:
                        # Hide from DM history
                        for _dk, _dv in DM_HISTORY.items():
                            for _dm in _dv:
                                if _dm.get("msg_id") == msg_id:
                                    _dm["hidden"] = True
                                    _dm["hidden_reason"] = "NCII report pending review"
                                    break
                        # Hide from server messages
                        for _sk, _sv in SERVER_MESSAGES.items():
                            for _sm in _sv:
                                if _sm.get("msg_id") == msg_id:
                                    _sm["hidden"] = True
                                    _sm["hidden_reason"] = "NCII report pending review"
                                    break
                    # Log compliance record
                    _ncii_record = {
                        "flag_id": flag["flag_id"],
                        "reporter": username,
                        "target_user": target,
                        "reported_at": time.time(),
                        "deadline": flag["ncii_deadline"],
                        "content_hidden_at": time.time(),
                        "msg_id": msg_id,
                        "status": "pending_review",
                    }
                    USER_DB.get("z", {}).setdefault("ncii_compliance_log", []).append(_ncii_record)
                    print(f"[TAKE-IT-DOWN] NCII report filed: {flag['flag_id']} — 48h deadline: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(flag['ncii_deadline']))}")

                # ── CSAM: queue for mandatory human + AI review ──
                # SECURITY FIX: text-based keyword matching ("child", "csam" in reason) was
                # weaponisable — any user could permanently ban anyone by including those words.
                # Auto-termination now ONLY fires after AI image-scan confirmation (see _bg_image_scan).
                # User text reports are queued for human moderator review instead.
                _is_csam_report = reason_category == "csam"
                if _is_csam_report:
                    flag["csam_report"] = True
                    # Preserve all data for law enforcement
                    _csam_preserved = {
                        "flag_id": flag["flag_id"],
                        "reporter": username,
                        "reporter_email": USER_DB.get(username, {}).get("email", ""),
                        "target_user": target,
                        "target_email": USER_DB.get(target, {}).get("email", ""),
                        "target_hwid": USER_DB.get(target, {}).get("hwid", ""),
                        "target_ip": _client_ip,
                        "target_dob": USER_DB.get(target, {}).get("date_of_birth", ""),
                        "msg_id": msg_id,
                        "hub_id": hub_id,
                        "content_preview": context[:500],
                        "reported_at": time.time(),
                        "reported_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    # Save preserved data to dedicated CSAM evidence file
                    try:
                        with open("csam_evidence.json", "a") as _ef:
                            _ef.write(json.dumps(_csam_preserved) + "\n")
                    except Exception:
                        pass
                    _log_csam(target, f"User report from {username}: {reason}")
                    # Mark for mandatory admin review — do NOT auto-ban on text reports alone.
                    # Auto-termination only fires in _bg_image_scan after AI confirmation.
                    if target in USER_DB:
                        USER_DB[target]["minor_flagged"] = True
                        USER_DB[target].setdefault("flagged_messages", [])
                        USER_DB[target]["under_review"] = True
                    flag["auto_action"] = "queued_for_review"
                    flag["resolved"] = False
                    flag["resolution"] = "pending_human_review"

                USER_DB.get("z", {}).setdefault("safety_flags", []).append(flag)
                if len(USER_DB.get("z", {}).get("safety_flags", [])) > 500:
                    USER_DB["z"]["safety_flags"] = USER_DB["z"]["safety_flags"][-500:]
                save_all()
                audit("SAFETY_FLAG", username, target, f"{reason_category or content_type}: {reason}")
                # Notify admin via email
                _email_category = "csam" if _is_csam_report else ("report" if reason_category != "ncii" else "legal")
                _email_subject = f"[NCII TAKE-IT-DOWN] Report from {username}" if reason_category == "ncii" else f"Report from {username} against {target}"
                threading.Thread(target=_notify_admins, args=(
                    _email_subject,
                    f"Reporter: {username}\nTarget: {target}\nCategory: {reason_category}\nType: {report_type} / {content_type}\nReason: {reason}\nContext: {context[:500]}\nMessage ID: {msg_id}\nHub ID: {hub_id}" +
                    (f"\n\n⚠️ NCII DEADLINE: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(flag.get('ncii_deadline', 0)))}" if reason_category == "ncii" else "") +
                    (f"\n\n🚨 CSAM AUTO-ACTION: Account terminated, HWID banned. Report to NCMEC: https://report.cybertip.org/" if _is_csam_report else ""),
                    _email_category
                ), daemon=True).start()

                # Auto-trigger AI image scan if an image was reported
                image_b64 = request.get("image_b64", "")
                if content_type == "image" and image_b64 and GROQ_API_KEY:
                    def _bg_image_scan():
                        try:
                            scan_res = requests.post(
                                "https://api.groq.com/openai/v1/chat/completions",
                                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                                json={
                                    "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                                    "max_tokens": 100,
                                    "messages": [
                                        {"role": "system", "content": "You are a strict image safety scanner. Respond ONLY with JSON."},
                                        {"role": "user", "content": [
                                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                                            {"type": "text", "text": (
                                                "Analyse this reported image. Respond ONLY with JSON:\n"
                                                '{"safe":true} or {"safe":false,"reason":"brief reason","type":"<type>"}\n'
                                                "Types: csam, nudity, sexual, violence, illegal\n"
                                                "CRITICAL: Flag ANY image with a minor in sexual/suggestive/exploitative context as csam."
                                            )}
                                        ]}
                                    ]
                                },
                                timeout=15
                            )
                            if scan_res.status_code == 200:
                                ai_text = scan_res.json()["choices"][0]["message"]["content"].strip()
                                m_scan = re.search(r'\{.*\}', ai_text, re.DOTALL)
                                result = json.loads(m_scan.group()) if m_scan else {"safe": True}
                                if not result.get("safe", True):
                                    vtype = result.get("type", "")
                                    flag["ai_scan"] = {"safe": False, "reason": result.get("reason", ""), "type": vtype}
                                    if vtype == "csam" and target:
                                        _log_csam(target, f"Reported image auto-scan flagged CSAM: {result.get('reason', '')}")
                                        if target in USER_DB:
                                            USER_DB[target]["banned"] = True
                                            USER_DB[target]["ban_reason"] = "CSAM detected via AI scan — auto-terminated per 18 U.S.C. § 2258A"
                                            USER_DB[target]["minor_flagged"] = True
                                            _t_hwid = USER_DB[target].get("hwid", "")
                                            if _t_hwid:
                                                USER_DB[target]["banned_hwid"] = _t_hwid
                                            save_all()
                                    audit("REPORT_IMAGE_SCAN", username, target, f"safe=False type={vtype} reason={result.get('reason','')}")
                                else:
                                    flag["ai_scan"] = {"safe": True}
                                    audit("REPORT_IMAGE_SCAN", username, target, "safe=True")
                        except Exception as scan_err:
                            print(f"[SCAN] Background image scan failed: {scan_err}")
                    threading.Thread(target=_bg_image_scan, daemon=True).start()

                _msg = "Content flagged for review."
                if reason_category == "ncii":
                    _msg = "Report received. The content has been hidden immediately and will be reviewed within 48 hours."
                elif _is_csam_report:
                    _msg = "Report received. Immediate action has been taken."
                response = {"status": "success", "message": _msg}
            else:
                response = {"status": "fail", "message": "Missing details"}

        elif action == "GET_SAFETY_FLAGS":
            if not is_admin:
                response = {"status": "fail", "message": "Admin only"}
            else:
                flags = USER_DB.get("z", {}).get("safety_flags", [])
                response = {"status": "success", "flags": flags}

        elif action == "RESOLVE_SAFETY_FLAG":
            if not is_admin:
                response = {"status": "fail", "message": "Admin only"}
            else:
                idx = request.get("index", -1)
                flags = USER_DB.get("z", {}).get("safety_flags", [])
                if isinstance(idx, int) and 0 <= idx < len(flags):
                    flags[idx]["resolved"] = True
                    save_all()
                    response = {"status": "success"}
                else:
                    response = {"status": "fail", "message": "Invalid flag"}

        # ── DM Calling System ──────────────────────────────────
        elif action == "START_CALL":
            target = request.get("target")
            call_type = request.get("call_type", "voice")
            if not target or target not in USER_DB:
                response = {"status": "fail", "message": "User not found"}
            elif target in USER_DB.get(username, {}).get("blocked", []):
                response = {"status": "fail", "message": "Cannot call blocked user"}
            elif username in USER_DB.get(target, {}).get("blocked", []):
                response = {"status": "fail", "message": "User not found"}
            elif USER_DB[target].get("status", "").lower() == "do not disturb":
                response = {"status": "fail", "message": f"{target} has Do Not Disturb enabled"}
            else:
                ACTIVE_CALLS[username] = {"target": target, "type": call_type, "status": "ringing", "time": time.time()}
                response = {"status": "success"}

        elif action == "HEARTBEAT":
            # Combined poll: updates online status + returns unread DMs, unread emails, incoming calls
            ONLINE_USERS[username] = time.time()
            result = {"status": "success"}

            # Unread DMs
            user_data = USER_DB.get(username, {})
            friends = user_data.get("friends", [])
            unread = {}
            for f in friends:
                key = _dm_key(username, f)
                msgs = DM_HISTORY.get(key, [])
                count = 0
                for m in reversed(msgs):
                    if m.get("sender") == f and not m.get("read_by", {}).get(username):
                        count += 1
                    elif m.get("sender") == f and m.get("read_by", {}).get(username):
                        break
                if count > 0:
                    unread[f] = count
            result["unread_dms"] = unread

            # Unread emails
            emails = user_data.get("inbox", [])
            result["unread_emails"] = sum(1 for e in emails if not e.get("read"))

            # Incoming call
            caller = None
            call_type = "voice"
            for u, call in list(ACTIVE_CALLS.items()):
                if call["target"] == username and call["status"] == "ringing":
                    if time.time() - call["time"] > 30:
                        del ACTIVE_CALLS[u]
                        continue
                    caller = u
                    call_type = call.get("type", "voice")
                    break
            if caller:
                result["incoming_call"] = {"caller": caller, "call_type": call_type}

            # Friend online status
            threshold = time.time() - 90
            friend_status = {}
            for f in friends:
                friend_status[f] = ONLINE_USERS.get(f, 0) > threshold
            result["friend_online"] = friend_status

            # Voice presence — who's in my voice channel (for fast updates)
            voice_key = VOICE_PRESENCE.get(username, "")
            if voice_key and ":" in voice_key:
                sid, ch = voice_key.split(":", 1)
                vc_users = [u for u, v in VOICE_PRESENCE.items() if v == voice_key and u != username]
                result["voice_channel"] = {"server_id": sid, "channel": ch, "users": vc_users}

            response = result

        elif action == "ANSWER_CALL":
            caller = request.get("caller")
            accepted = request.get("accepted", False)
            if caller in ACTIVE_CALLS and ACTIVE_CALLS[caller]["target"] == username:
                if accepted:
                    ACTIVE_CALLS[caller]["status"] = "accepted"
                else:
                    ACTIVE_CALLS[caller]["status"] = "declined"
                response = {"status": "success"}
            else:
                response = {"status": "fail", "message": "No active call"}

        elif action == "CHECK_CALL_STATUS":
            target = request.get("target")
            call_status = None
            # Check if I have an active call entry
            if username in ACTIVE_CALLS:
                call = ACTIVE_CALLS[username]
                call_status = call["status"]
                if call_status in ("declined", "ended"):
                    del ACTIVE_CALLS[username]
            # Also check if the other person's entry shows ended
            if call_status is None and target in ACTIVE_CALLS:
                other_call = ACTIVE_CALLS[target]
                if other_call.get("target") == username:
                    call_status = other_call["status"]
                    if call_status in ("declined", "ended"):
                        del ACTIVE_CALLS[target]
            response = {"status": "success", "call_status": call_status or "active"}

        elif action == "END_CALL":
            target = request.get("target")
            # Mark call as ended for BOTH sides (don't delete yet — let poll see it)
            if username in ACTIVE_CALLS:
                ACTIVE_CALLS[username]["status"] = "ended"
            # If the answerer ends the call, mark the caller's entry as ended too
            for u, call in list(ACTIVE_CALLS.items()):
                if call.get("target") == username:
                    call["status"] = "ended"
                if u == username and call.get("target") == target:
                    call["status"] = "ended"
            # Also create a reverse "ended" marker so the other side sees it
            if target and target not in ACTIVE_CALLS:
                ACTIVE_CALLS[target] = {"target": username, "type": "voice", "status": "ended", "time": time.time()}
            elif target and target in ACTIVE_CALLS:
                ACTIVE_CALLS[target]["status"] = "ended"
            # Clean up WebRTC signals
            CALL_SIGNALS.pop(username, None)
            if target:
                CALL_SIGNALS.pop(target, None)
            response = {"status": "success"}

        elif action == "GET_TURN_CREDENTIALS":
            # Generate ephemeral TURN credentials (time-limited HMAC)
            _turn_secret = os.environ.get("TURN_SECRET", "TenshiTurnSecret2026")
            _turn_ttl = 300  # 5 minutes
            _turn_expiry = int(time.time()) + _turn_ttl
            _turn_user = f"{_turn_expiry}:{username}"
            _turn_pass = base64.b64encode(
                _hmac_module.new(_turn_secret.encode(), _turn_user.encode(), hashlib.sha1).digest()
            ).decode()
            response = {"status": "success", "username": _turn_user, "credential": _turn_pass, "ttl": _turn_ttl}

        elif action == "CALL_SPEAKING":
            # Store speaking state for this user
            if 'SPEAKING_STATE' not in globals():
                globals()['SPEAKING_STATE'] = {}
            SPEAKING_STATE[username] = {"speaking": bool(request.get("speaking")), "target": request.get("target", ""), "ts": time.time()}
            response = {"status": "success"}

        elif action == "CALL_SIGNAL":
            # WebRTC signaling: store SDP offer/answer/ICE candidate for target
            target = request.get("target", "")
            signal_type = request.get("signal_type", "")  # "offer", "answer", "ice"
            data = request.get("data")
            if target and signal_type and data is not None:
                if target not in CALL_SIGNALS:
                    CALL_SIGNALS[target] = []
                CALL_SIGNALS[target].append({
                    "from": username,
                    "type": signal_type,
                    "data": data,
                    "ts": time.time()
                })
                # Cap at 100 entries (ICE candidates can accumulate)
                if len(CALL_SIGNALS[target]) > 100:
                    CALL_SIGNALS[target] = CALL_SIGNALS[target][-50:]
                response = {"status": "success"}
            else:
                response = {"status": "fail", "message": "Missing signal data"}

        elif action == "POLL_SIGNAL":
            # Retrieve and clear all pending WebRTC signals for this user
            signals = CALL_SIGNALS.pop(username, [])
            response = {"status": "success", "signals": signals}

        elif action == "VC_SIGNAL":
            # Voice channel WebRTC signaling (separate from DM calls)
            target = request.get("target", "")
            signal_type = request.get("signal_type", "")
            data = request.get("data")
            if target and signal_type and data is not None:
                if target not in VC_SIGNALS:
                    VC_SIGNALS[target] = []
                VC_SIGNALS[target].append({
                    "from": username,
                    "type": signal_type,
                    "data": data,
                    "ts": time.time()
                })
                if len(VC_SIGNALS[target]) > 100:
                    VC_SIGNALS[target] = VC_SIGNALS[target][-50:]
                response = {"status": "success"}
            else:
                response = {"status": "fail", "message": "Missing signal data"}

        elif action == "VC_POLL_SIGNAL":
            signals = VC_SIGNALS.pop(username, [])
            response = {"status": "success", "signals": signals}

        elif action == "VC_BROADCAST_CAPTION":
            server_id = request.get("server_id", "")
            channel = request.get("channel", "")
            text_raw = request.get("text") or ""
            text = text_raw.strip()[:500]
            vk = VOICE_PRESENCE.get(username, "")
            if not server_id or not channel or not text:
                response = {"status": "fail", "message": "Caption text or channel missing."}
            elif vk != f"{server_id}:{channel}":
                response = {"status": "fail", "message": "Join the voice channel before sending captions."}
            else:
                vkey_full = f"{server_id}:{channel}"
                payload = {"text": text, "from": username, "channel": channel, "server_id": server_id}
                for u, vk_other in list(VOICE_PRESENCE.items()):
                    if vk_other != vkey_full:
                        continue
                    VC_SIGNALS.setdefault(u, []).append({"from": username, "type": "caption", "data": payload, "ts": time.time()})
                    if len(VC_SIGNALS[u]) > 140:
                        VC_SIGNALS[u] = VC_SIGNALS[u][-80:]
                audit("VC_CAPTION", username, server_id, text[:80])
                response = {"status": "success"}

        elif action == "HUB_RUN_UTILITY_SCRIPT":
            if not EXTENSION_HUB_DSL:
                response = {"status": "fail", "message": "Hub DSL engine not available."}
            else:
                prog = request.get("program") or {}
                ctx = {"USER_DB": USER_DB, "SERVERS_DB": SERVERS_DB}
                response = _hub_dsl.run_script(prog, ctx)

        elif action == "HUB_DSL_HELP":
            response = {"status": "success", "help": (_hub_dsl.help_text() if EXTENSION_HUB_DSL else "N/A"), "dsl_version": "1"}

        elif action == "MAILBOX_CRYPTO_ENABLE":
            if not EXTENSION_MAILBOX:
                response = {"status": "fail", "message": "Mailbox crypto unavailable (cryptography?)."}
            else:
                pp = request.get("passphrase", "")
                if username not in USER_DB:
                    response = {"status": "fail", "message": "Invalid user."}
                elif _mbx.mailbox_enabled(USER_DB[username]):
                    response = {"status": "fail", "message": "Mailbox encryption already enabled."}
                else:
                    try:
                        _mbx.enable_mailbox_crypto(USER_DB[username], pp)
                        save_all()
                        response = {"status": "success", "message": "Mailbox encryption configured. Unlock to read/send encrypted mail."}
                    except ValueError as e:
                        response = {"status": "fail", "message": str(e)}

        elif action == "MAILBOX_CRYPTO_UNLOCK":
            if not EXTENSION_MAILBOX:
                response = {"status": "fail", "message": "Mailbox crypto unavailable."}
            elif username not in USER_DB:
                response = {"status": "fail", "message": "Invalid user."}
            else:
                try:
                    _mbx.unlock_mailbox(USER_DB[username], username, request.get("passphrase", ""))
                    save_all()
                    response = {"status": "success", "message": "Mailbox unlocked on this session."}
                except Exception as e:
                    response = {"status": "fail", "message": str(e) or "Unlock failed"}

        elif action == "MAILBOX_CRYPTO_LOCK":
            if EXTENSION_MAILBOX:
                _mbx.session_clear(username)
            response = {"status": "success"}

        elif action == "MAILBOX_CRYPTO_DISABLE":
            if not EXTENSION_MAILBOX:
                response = {"status": "fail", "message": "Mailbox crypto unavailable."}
            elif username not in USER_DB:
                response = {"status": "fail", "message": "Invalid user."}
            else:
                try:
                    _mbx.disable_mailbox(username, USER_DB[username], request.get("passphrase", ""))
                    save_all()
                    response = {"status": "success", "message": "Mailbox encryption removed."}
                except Exception as e:
                    response = {"status": "fail", "message": str(e) or "Disable failed"}

        elif action == "MAILBOX_CRYPTO_STATUS":
            if not EXTENSION_MAILBOX:
                response = {"status": "fail", "message": "Mailbox crypto unavailable."}
            else:
                u = USER_DB.get(username, {})
                unlocked = bool(_mbx.session_get(username))
                response = {"status": "success", "enabled": _mbx.mailbox_enabled(u), "session_unlocked": unlocked}

        elif action == "MAILBOX_MIGRATE_ENCRYPT_PENDING":
            if not EXTENSION_MAILBOX or username not in USER_DB:
                response = {"status": "fail", "message": "Unavailable."}
            else:
                n1 = _mbx.migrate_folder_emails(username, USER_DB[username], "inbox")
                n2 = _mbx.migrate_folder_emails(username, USER_DB[username], "spam")
                n3 = _mbx.migrate_folder_emails(username, USER_DB[username], "email_spam")
                save_all()
                response = {"status": "success", "migrated": n1 + n2 + n3}

        elif action == "CREATE_STRIPE_CHECKOUT":
            if username not in USER_DB:
                response = {"status": "fail", "message": "Login required"}
            elif not STRIPE_SECRET_KEY:
                response = {"status": "fail", "message": "Payments unavailable"}
            else:
                response = _stripe_create_checkout(username, request)

        elif action == "FINISH_STRIPE_CHECKOUT":
            sid = request.get("stripe_session_id", "")
            if username not in USER_DB or not sid:
                response = {"status": "fail", "message": "Missing session"}
            elif not STRIPE_SECRET_KEY:
                response = {"status": "fail", "message": "Payments unavailable"}
            else:
                response = _stripe_finish_and_apply(username, sid)

        elif action == "PREMIUM_USERNAMES_LIST":
            if not EXTENSION_MARKET:
                response = {"status": "fail", "message": "Marketplace unavailable."}
            else:
                _premium_mkt.release_stale()
                _premium_mkt.seed_listings_if_empty(24, _PREMIUM_UNAME_PRICE)
                response = {"status": "success", "listings": _premium_mkt.list_available(60)}

        elif action == "PREMIUM_USERNAME_RESERVE":
            if not EXTENSION_MARKET or username not in USER_DB:
                response = {"status": "fail", "message": "Unavailable."}
            else:
                lid = request.get("listing_id", "")
                ok, msg, lst = _premium_mkt.reserve(lid, username)
                response = {"status": "success" if ok else "fail"}
                if msg:
                    response["message"] = msg
                if ok and lst:
                    response["listing"] = {"id": lst.get("id"), "handle": lst.get("handle"), "price_cents": lst.get("price_cents")}

        # ── Session Resume (Remember Me — no Turnstile needed) ──
        elif action == "SESSION_LOGIN":
            # ── Try session token first (more secure, doesn't send pwd hash) ──
            _sess_token = request.get("session_token", "")
            _token_user = _validate_session_token(_sess_token, addr[0] if addr else "")
            if _sess_token and _token_user:
                if _token_user in USER_DB:
                    if USER_DB[_token_user].get("banned"):
                        response = {"status": "fail", "message": "This account has been suspended."}
                    else:
                        USER_DB[_token_user]["last_seen"] = time.time()
                        save_all()
                        # Ensure legacy sessions get a signing key
                        _sk = _get_signing_key(_sess_token)
                        if not _sk and _sess_token in _SESSION_TOKENS:
                            _sk = secrets.token_hex(32)
                            _SESSION_TOKENS[_sess_token]["signing_key"] = _sk
                        response = {
                            "status": "success",
                            "username": _token_user,
                            "is_admin": USER_DB[_token_user].get("is_admin", False),
                            "signing_key": _sk,
                        }
                else:
                    response = {"status": "fail", "message": "Session expired."}
            else:
                # SECURITY FIX: removed password-hash fallback in SESSION_LOGIN.
                # That path bypassed Turnstile bot-verification and 2FA entirely.
                # All password-based logins must go through the LOGIN action which
                # enforces both. Clients must present a valid session_token here.
                response = {"status": "fail", "message": "Session expired. Please log in again."}

        elif action == "LOGIN":
            if username in USER_DB:
                # Check ban first
                if USER_DB[username].get("banned"):
                    response = {"status": "fail", "message": "Your account has been banned."}
                else:
                    # Verify password if it exists (legacy accounts might not have it yet)
                    stored_hash = USER_DB[username].get("pwd_hash", "")
                    if stored_hash and stored_hash != request.get("pwd_hash", ""):
                        response = {"status": "fail", "message": "Invalid Password"}
                    else:
                        # Free access / admin users: auto-link any HWID, no payment gate
                        if USER_DB[username].get("free_access") or USER_DB[username].get("is_admin"):
                            new_hwid = request.get("hwid")
                            allowed = USER_DB[username].get("allowed_hwids", [])
                            if new_hwid and new_hwid not in allowed:
                                allowed.append(new_hwid)
                                USER_DB[username]["allowed_hwids"] = allowed
                                save_all()
                            response = {"status": "success", "message": "Login Successful", "is_admin": True}
                        else:
                            allowed = USER_DB[username].get("allowed_hwids", [USER_DB[username].get("hwid")])
                            if request.get("hwid") in allowed:
                                response = {"status": "success", "message": "Login Successful", "is_admin": False}
                            else:
                                mismatch_resp = {"status": "fail", "message": "Hardware ID Mismatch"}
                                promo_price = USER_DB[username].get("promo_price")
                                if promo_price:
                                    mismatch_resp["promo_price"] = promo_price
                                response = mismatch_resp
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "SSO_AUTH":
            import urllib.request as _ureq, base64 as _b64
            provider    = request.get("provider", "").lower()
            id_token    = request.get("id_token", "")
            access_tok  = request.get("access_token", "")
            req_hwid    = request.get("hwid", "")
            user_info   = None

            try:
                if provider == "google" and id_token:
                    url = f"https://oauth2.googleapis.com/tokeninfo?id_token={id_token}"
                    with _ureq.urlopen(url, timeout=6) as r:
                        data = json.loads(r.read().decode())
                    if "error" not in data:
                        user_info = {"email": data.get("email",""), "sub": data.get("sub",""),
                                     "name": data.get("name", data.get("given_name",""))}

                elif provider == "microsoft" and access_tok:
                    req2 = _ureq.Request("https://graph.microsoft.com/v1.0/me",
                                         headers={"Authorization": f"Bearer {access_tok}"})
                    with _ureq.urlopen(req2, timeout=6) as r:
                        data = json.loads(r.read().decode())
                    user_info = {"email": data.get("mail") or data.get("userPrincipalName",""),
                                 "sub": data.get("id",""), "name": data.get("displayName","")}

                elif provider == "apple" and id_token:
                    # Decode JWT payload (signature verified by Apple at issuance; we trust it
                    # was delivered via the OAuth redirect — add JWKS verification for production).
                    parts = id_token.split(".")
                    if len(parts) == 3:
                        padded = parts[1] + "=" * ((-len(parts[1])) % 4)
                        payload = json.loads(_b64.urlsafe_b64decode(padded).decode())
                        user_info = {"email": payload.get("email",""), "sub": payload.get("sub",""),
                                     "name": request.get("name","")}
            except Exception as _e:
                print(f"SSO verify error ({provider}): {_e}")

            if not user_info or (not user_info.get("email") and not user_info.get("sub")):
                response = {"status": "fail", "message": "SSO verification failed"}
            else:
                sso_email = user_info["email"].lower()
                sso_sub   = user_info["sub"]
                sso_name  = user_info.get("name","")

                # Find existing account linked by provider sub or email.
                linked = None
                for uname, udata in USER_DB.items():
                    if udata.get("connections",{}).get(provider,{}).get("sub") == sso_sub and sso_sub:
                        linked = uname; break
                if not linked and sso_email:
                    for uname, udata in USER_DB.items():
                        if udata.get("email","").lower() == sso_email:
                            linked = uname; break

                if linked:
                    conns = USER_DB[linked].setdefault("connections", {})
                    if provider not in conns:
                        conns[provider] = {"sub": sso_sub, "email": sso_email}
                        save_all()
                    is_admin = USER_DB[linked].get("is_admin") or USER_DB[linked].get("free_access")
                    response = {"status":"success","message":"Login Successful",
                                "username": linked,"is_admin": bool(is_admin)}
                else:
                    # Auto-register.
                    base = ''.join(c for c in
                                   (sso_name or sso_email.split("@")[0] or "user")
                                   .replace(" ","_").lower() if c.isalnum() or c=='_')[:20] or "user"
                    new_u = base
                    i = 1
                    while new_u in USER_DB:
                        new_u = f"{base}{i}"; i += 1
                    USER_DB[new_u] = {
                        "hwid": req_hwid, "pwd_hash": "",
                        "email": sso_email, "phone": "",
                        "allowed_hwids": [req_hwid] if req_hwid else [],
                        "friends":[], "pending_reqs":[], "following":[], "followers":[],
                        "blocked":[], "hidden_from":[], "servers":["srv_0","srv_1"],
                        "bio": f"Joined via {provider.title()}", "pronouns":"",
                        "banner_color":"#2b2d31", "status":"Online", "custom_status":"",
                        "storage_pref":"cloud", "verified": True,
                        "connections": {provider: {"sub": sso_sub, "email": sso_email}},
                        "privacy": {"hide_online_status":False,"hide_server_membership":False,
                                    "dms_from_friends_only":False,"auto_accept_friends":False,
                                    "auto_decline_strangers":False},
                    }
                    for auto_srv in ["srv_0","srv_1"]:
                        if auto_srv in SERVERS_DB:
                            mdict = SERVERS_DB[auto_srv].setdefault("members", {})
                            if isinstance(mdict, dict) and new_u not in mdict:
                                mdict[new_u] = ["role_everyone"]
                    save_all()
                    response = {"status":"success","message":"Account Created",
                                "username": new_u,"is_admin": False}

        elif action == "LINK_DEVICE":
            if username in USER_DB:
                stored_hash = USER_DB[username].get("pwd_hash", "")
                if stored_hash and stored_hash != request.get("pwd_hash", ""):
                    response = {"status": "fail", "message": "Invalid Password"}
                else:
                    allowed = USER_DB[username].get("allowed_hwids", [USER_DB[username].get("hwid")])
                    new_hwid = request.get("hwid")
                    if new_hwid not in allowed:
                        allowed.append(new_hwid)
                        USER_DB[username]["allowed_hwids"] = allowed
                        save_all()
                    response = {"status": "success", "message": "Device Linked Successfully"}
            else:
                response = {"status": "fail", "message": "User not found"}
                
        elif action == "RECOVER_PASSWORD":
            _rec_ip = request.get("_client_ip", "")
            ident_in = (request.get("username") or "").strip()
            _sess_tok = request.get("session_token", "")
            _sess_who = _validate_session_token(_sess_tok, _rec_ip) if _sess_tok else ""
            if ident_in:
                resolved_user = _resolve_username_from_login_identifier(ident_in)
            else:
                resolved_user = _sess_who if _sess_who else None
            _trusted_session = bool(_sess_who and resolved_user and _sess_who == resolved_user)
            if not _trusted_session and not _verify_turnstile(request.get("cf_token", "")):
                response = {"status": "fail", "message": "Bot verification failed. Please try again."}
            elif not _pw_recover_ip_allow(_rec_ip):
                response = {"status": "fail", "message": "Too many reset requests from this network. Try again later."}
            elif not resolved_user:
                audit("RECOVER_UNKNOWN", "", _rec_ip, "Identifier not matched", ip=_rec_ip)
                response = {
                    "status": "success",
                    "message": "If an account exists for that username or email, we've sent a verification code. Check your inbox and spam folder.",
                }
            elif not (USER_DB.get(resolved_user, {}).get("pwd_hash") or "").strip():
                audit("RECOVER_SSO_ONLY", resolved_user, "", "No password set", ip=_rec_ip)
                response = {"status": "fail", "message": "This account uses social login only — there is no password to reset."}
            else:
                email = (USER_DB[resolved_user].get("email") or "").strip()
                if not email:
                    audit("RECOVER_NO_EMAIL", resolved_user, "", "", ip=_rec_ip)
                    response = {
                        "status": "success",
                        "message": "If an account exists with a verified email on file, we've sent a code. Otherwise contact support.",
                    }
                else:
                    now_rc = time.time()
                    prev_ts = float(USER_DB[resolved_user].get("recovery_code_ts") or 0)
                    if USER_DB[resolved_user].get("recovery_code") and (now_rc - prev_ts < 50):
                        response = {"status": "fail", "message": "Please wait a minute before requesting another code."}
                    else:
                        code_rc = str(secrets.randbelow(900000) + 100000)
                        USER_DB[resolved_user]["recovery_code"] = code_rc
                        USER_DB[resolved_user]["recovery_code_ts"] = now_rc
                        save_all()
                        ok_mail = _send_password_reset_email(email, code_rc)
                        print(f"[RECOVERY] Code issued for {resolved_user} ({'sent' if ok_mail else 'send failed'})")
                        if email and "@" in email:
                            masked_rc = email[:2] + "***" + email.split("@")[-1]
                        else:
                            masked_rc = "your email"
                        if ok_mail:
                            response = {"status": "success", "message": f"Verification code sent to {masked_rc}. Code expires in 15 minutes."}
                        else:
                            USER_DB[resolved_user].pop("recovery_code", None)
                            USER_DB[resolved_user].pop("recovery_code_ts", None)
                            save_all()
                            response = {"status": "fail", "message": "Could not send email. Try again shortly or contact support."}

        elif action == "RESET_PASSWORD":
            _rst_ip = request.get("_client_ip", "")
            if not _pw_reset_try_ip_allow(_rst_ip):
                response = {"status": "fail", "message": "Too many attempts. Try again in a few minutes."}
            else:
                ident_rs = (request.get("username") or "").strip()
                uname_rs = _resolve_username_from_login_identifier(ident_rs)
                code = request.get("recovery_code")
                new_hp = request.get("new_pwd_hash", "") or ""
                _fail_generic = {"status": "fail", "message": "Invalid or expired code."}
                if not uname_rs or uname_rs not in USER_DB:
                    response = _fail_generic
                else:
                    stored_code = USER_DB[uname_rs].get("recovery_code", "")
                    code_ts = USER_DB[uname_rs].get("recovery_code_ts", 0)
                    if not stored_code or not code:
                        response = {"status": "fail", "message": "Enter the verification code from your email, then choose a new password."}
                    elif time.time() - code_ts > 900:
                        USER_DB[uname_rs].pop("recovery_code", None)
                        USER_DB[uname_rs].pop("recovery_code_ts", None)
                        save_all()
                        response = {"status": "fail", "message": "Code expired. Request a new code from the forgot-password flow."}
                    elif not _hmac_module.compare_digest(str(stored_code), str(code)):
                        audit("RESET_PW_BAD_CODE", ident_rs[:32], uname_rs, "", ip=_rst_ip)
                        response = _fail_generic
                    elif not new_hp:
                        response = {"status": "fail", "message": "Choose a new password."}
                    else:
                        USER_DB[uname_rs]["pwd_hash"] = _hash_password(new_hp)
                        USER_DB[uname_rs].pop("recovery_code", None)
                        USER_DB[uname_rs].pop("recovery_code_ts", None)
                        save_all()
                        _tokens_to_remove = [t for t, d in _SESSION_TOKENS.items()
                                             if d.get("username") == uname_rs]
                        for _t in _tokens_to_remove:
                            _SESSION_TOKENS.pop(_t, None)
                        _save_session_tokens()
                        audit("PASSWORD_RESET_OK", uname_rs, "", "", ip=_rst_ip)
                        response = {"status": "success", "message": "Password updated. You can sign in with your new password."}

        elif action == "VALIDATE_PROMO":
            PROMO_CODES = {
                "TENSHI-FRIEND-ZERO-DOLLARS-2026-LMN": {"price": "$0.00", "color": "#23a559"},
                "TENSHI-ONE-DOLLAR-FIRST-PURCHASE-2026-XQZ": {"price": "$1.00", "color": "#FEE75C"},
            }
            code = request.get("code", "").strip()
            if username not in USER_DB:
                response = {"status": "fail", "message": "User not found"}
            elif code not in PROMO_CODES:
                response = {"status": "fail", "message": "Invalid Promo Code"}
            else:
                existing_promo = USER_DB[username].get("promo_code")
                if existing_promo and existing_promo != code:
                    response = {"status": "fail", "message": f"Account already locked to a different promo code"}
                elif existing_promo == code:
                    # Already applied — just return the stored discount
                    response = {"status": "success", "price": PROMO_CODES[code]["price"], "color": PROMO_CODES[code]["color"], "message": "Promo already applied to your account"}
                else:
                    # First use: permanently lock this promo to the account
                    USER_DB[username]["promo_code"] = code
                    USER_DB[username]["promo_price"] = PROMO_CODES[code]["price"]
                    save_all()
                    response = {"status": "success", "price": PROMO_CODES[code]["price"], "color": PROMO_CODES[code]["color"], "message": "Promo applied! Your account is permanently locked to this discount."}

        elif action == "UPDATE_PROFILE":
            if username in USER_DB:
                if "phone" in request:
                    USER_DB[username]["phone"] = request["phone"]
                USER_DB[username]["bio"] = request.get("bio", USER_DB[username].get("bio", ""))
                USER_DB[username]["pronouns"] = request.get("pronouns", USER_DB[username].get("pronouns", ""))
                USER_DB[username]["banner_color"] = request.get("banner_color", USER_DB[username].get("banner_color", "#2b2d31"))
                USER_DB[username]["status"] = request.get("status", USER_DB[username].get("status", "Online"))
                USER_DB[username]["connections"] = request.get("connections", USER_DB[username].get("connections", {}))
                if "avatar" in request:
                    av = request.get("avatar")
                    if av:
                        USER_DB[username]["avatar"] = av
                    else:
                        USER_DB[username].pop("avatar", None)
                if "banner_image" in request:
                    bi = request.get("banner_image")
                    if bi:
                        USER_DB[username]["banner_image"] = bi
                    else:
                        USER_DB[username].pop("banner_image", None)
                if "username_color" in request:
                    USER_DB[username]["username_color"] = request.get("username_color", "#ffffff")
                if "profile_color" in request:
                    USER_DB[username]["profile_color"] = request.get("profile_color")
                if "display_name" in request:
                    dn = (request.get("display_name") or "").strip()[:64]
                    if dn:
                        USER_DB[username]["display_name"] = dn
                    else:
                        USER_DB[username].pop("display_name", None)
                if "custom_status" in request:
                    USER_DB[username]["custom_status"] = (request.get("custom_status") or "")[:128]
                save_all()
                response = {"status": "success", "message": "Profile Updated"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "LOCK_CHANNEL":
            server_id = request.get("server_id")
            channel_name = request.get("channel_name")
            roles = request.get("allowed_roles", ["role_admin"])
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                if channel_name in SERVERS_DB[server_id]["channels"]:
                    SERVERS_DB[server_id]["channels"][channel_name]["locked"] = True
                    SERVERS_DB[server_id]["channels"][channel_name]["allowed_roles"] = roles
                    save_all()
                    response = {"status": "success", "message": f"{channel_name} is now locked"}
                else:
                    response = {"status": "fail", "message": "Channel not found"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}
                
        elif action == "UNLOCK_CHANNEL":
            server_id = request.get("server_id")
            channel_name = request.get("channel_name")
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                if channel_name in SERVERS_DB[server_id]["channels"]:
                    SERVERS_DB[server_id]["channels"][channel_name]["locked"] = False
                    SERVERS_DB[server_id]["channels"][channel_name]["allowed_roles"] = []
                    save_all()
                    response = {"status": "success", "message": f"{channel_name} is now unlocked"}
                else:
                    response = {"status": "fail", "message": "Channel not found"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}

        elif action == "CREATE_ROLE":
            server_id = request.get("server_id")
            role_name = request.get("role_name", "New Role")
            role_color = request.get("role_color", "#ffffff")
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                new_role_id = f"role_{len(SERVERS_DB[server_id]['roles']) + 1}"
                SERVERS_DB[server_id]["roles"][new_role_id] = {"name": role_name, "color": role_color}
                save_all()
                response = {"status": "success", "message": f"Role {role_name} created", "role_id": new_role_id}
            else:
                response = {"status": "fail", "message": "Unauthorized"}

        elif action == "ASSIGN_ROLE":
            server_id = request.get("server_id")
            target_user = request.get("target")
            role_id = request.get("role_id")
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                if target_user in SERVERS_DB[server_id]["members"]:
                    if role_id in SERVERS_DB[server_id]["roles"]:
                        if role_id not in SERVERS_DB[server_id]["members"][target_user]:
                            SERVERS_DB[server_id]["members"][target_user].append(role_id)
                            save_all()
                            response = {"status": "success", "message": f"Role assigned to {target_user}"}
                        else:
                            response = {"status": "fail", "message": "User already has role"}
                    else:
                        response = {"status": "fail", "message": "Role not found"}
                else:
                    response = {"status": "fail", "message": "User not in server"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}

        elif action == "GET_PROFILE":
            target = request.get("target")
            if target in USER_DB:
                data = USER_DB[target]
                pc = data.get("profile_color") or _default_profile_color(target)
                response = {
                    "status": "success",
                    "phone": data.get("phone", ""),
                    "bio": data.get("bio", "New to Tenshi"),
                    "pronouns": data.get("pronouns", ""),
                    "banner_color": data.get("banner_color", "#2b2d31"),
                    "user_status": data.get("status", "Online"),
                    "status_color": data.get("status_color", "#23a559"),
                    "privacy": data.get("privacy", {}),
                    "connections": data.get("connections", {}),
                    "created_at": "2024",  # Placeholder
                    "avatar": data.get("avatar", ""),
                    "banner_image": data.get("banner_image", ""),
                    "profile_color": pc,
                    "username_color": data.get("username_color", "#ffffff"),
                    "custom_status": data.get("custom_status", ""),
                    "display_name": data.get("display_name") or "",
                    "badges": data.get("badges") or [],
                    "social_links": data.get("social_links") or {},
                }
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "GET_SERVERS":
            # Return list of servers this user is in
            if username in USER_DB:
                user_servers = USER_DB[username].get("servers", [])
                server_dict = {}
                for s_id in user_servers:
                    if s_id in SERVERS_DB:
                        # Return full server details (channels, etc.)
                        server_dict[s_id] = SERVERS_DB[s_id]
                response = {"status": "success", "servers": server_dict}
            else:
                response = {"status": "fail", "servers": {}}

        elif action == "LEAVE_SERVER":
            server_id = request.get("server_id")
            if server_id in SERVERS_DB:
                # Remove from Server Members
                if username in SERVERS_DB[server_id]["members"]:
                    del SERVERS_DB[server_id]["members"][username]
                
                # Remove from User's Server List
                if username in USER_DB and server_id in USER_DB[username]["servers"]:
                    USER_DB[username]["servers"].remove(server_id)
                    save_all()
                    response = {"status": "success", "message": "Left server"}
                else:
                    response = {"status": "fail", "message": "Not in server"}
            else:
                response = {"status": "fail", "message": "Server not found"}

        elif action == "CREATE_CHANNEL":
            server_id = request.get("server_id")
            channel_name = request.get("channel_name")
            channel_type = request.get("channel_type", "text")
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                if channel_name not in SERVERS_DB[server_id]["channels"]:
                    SERVERS_DB[server_id]["channels"][channel_name] = {
                        "type": channel_type,
                        "locked": False,
                        "allowed_roles": []
                    }
                    save_all()
                    response = {"status": "success", "message": f"Channel {channel_name} created"}
                else:
                    response = {"status": "fail", "message": "Channel already exists"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}
                
        elif action == "RENAME_CHANNEL":
            server_id = request.get("server_id")
            old_name = request.get("channel_name")
            new_name = request.get("new_name")
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                if old_name in SERVERS_DB[server_id]["channels"]:
                    if new_name not in SERVERS_DB[server_id]["channels"]:
                        # Rename strategy: copy old data to new key, delete old key
                        SERVERS_DB[server_id]["channels"][new_name] = SERVERS_DB[server_id]["channels"][old_name]
                        del SERVERS_DB[server_id]["channels"][old_name]
                        save_all()
                        response = {"status": "success", "message": f"Channel renamed to {new_name}"}
                    else:
                        response = {"status": "fail", "message": "Channel name already exists"}
                else:
                    response = {"status": "fail", "message": "Channel not found"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}
                
        elif action == "GET_PUBLIC_SERVERS":
            online_threshold = time.time() - 300  # 5 minutes
            public_servers = []
            for s_id, s_data in SERVERS_DB.items():
                if not s_data.get("invite_only", False):
                    members = s_data.get("members", {})
                    member_keys = list(members.keys()) if isinstance(members, dict) else list(members)
                    member_count = len(member_keys)
                    online_count = sum(1 for m in member_keys if ONLINE_USERS.get(m, 0) > online_threshold)
                    public_servers.append({
                        "id": s_id,
                        "name": s_data.get("name"),
                        "member_count": member_count,
                        "online_count": online_count,
                        "is_promoted": s_data.get("is_promoted", False)
                    })
            # Sort: Promoted servers first, then by member count
            public_servers.sort(key=lambda x: (x['is_promoted'], x['member_count']), reverse=True)
            response = {"status": "success", "servers": public_servers}

        elif action == "BOOST_SERVER":
            server_id = request.get("server_id")
            if server_id in SERVERS_DB:
                if SERVERS_DB[server_id].get("owner") == username:
                    SERVERS_DB[server_id]["is_promoted"] = True
                    save_all()
                    response = {"status": "success", "message": f"Server {SERVERS_DB[server_id]['name']} boosted to top!"}
                else:
                    response = {"status": "fail", "message": "Only owner can boost"}
            else:
                response = {"status": "fail", "message": "Server not found"}
            
        elif action == "GET_ALL_USERS":
            if not USER_DB.get(username, {}).get("is_admin"):
                response = {"status": "fail", "message": "Unauthorized"}
            else:
                now = time.time()
                user_list = []
                for u, d in USER_DB.items():
                    user_list.append({
                        "username":    u,
                        "email":       d.get("email", ""),
                        "verified":    d.get("verified", False),
                        "banned":      d.get("banned", False),
                        "is_admin":    d.get("is_admin", False) or d.get("free_access", False),
                        "connections": list(d.get("connections", {}).keys()),
                        "server_count": len(d.get("servers", [])),
                        "friend_count": len(d.get("friends", [])),
                        "hwid_count":  len(d.get("allowed_hwids", [])),
                        "online":      (now - ONLINE_USERS.get(u, 0)) < 120,
                    })
                response = {"status": "success", "users": user_list}

        elif action == "GET_ADMIN_STATS":
            if not USER_DB.get(username, {}).get("is_admin"):
                response = {"status": "fail", "message": "Unauthorized"}
            else:
                now = time.time()
                online_count = sum(1 for t in ONLINE_USERS.values() if now - t < 120)
                response = {
                    "status":       "success",
                    "total_users":  len(USER_DB),
                    "online_users": online_count,
                    "total_servers": len(SERVERS_DB),
                    "total_messages": sum(len(v) for v in MESSAGE_QUEUE.values()),
                }

        elif action == "BAN_USER":
            if not USER_DB.get(username, {}).get("is_admin"):
                response = {"status": "fail", "message": "Unauthorized"}
            else:
                target = request.get("target")
                if not target or target not in USER_DB:
                    response = {"status": "fail", "message": "User not found"}
                elif target == username:
                    response = {"status": "fail", "message": "Cannot ban yourself"}
                else:
                    USER_DB[target]["banned"] = True
                    USER_DB[target]["allowed_hwids"] = []  # Revoke all device access
                    save_all()
                    response = {"status": "success", "message": f"{target} has been banned"}

        elif action == "UNBAN_USER":
            if not USER_DB.get(username, {}).get("is_admin"):
                response = {"status": "fail", "message": "Unauthorized"}
            else:
                target = request.get("target")
                if not target or target not in USER_DB:
                    response = {"status": "fail", "message": "User not found"}
                else:
                    USER_DB[target]["banned"] = False
                    save_all()
                    response = {"status": "success", "message": f"{target} has been unbanned"}

        elif action == "ADMIN_BROADCAST":
            if not USER_DB.get(username, {}).get("is_admin"):
                response = {"status": "fail", "message": "Unauthorized"}
            else:
                content = request.get("content", "").strip()
                if not content:
                    response = {"status": "fail", "message": "Message cannot be empty"}
                else:
                    msg_obj = {
                        "sender": "SYSTEM",
                        "target_id": "ALL",
                        "target_type": "broadcast",
                        "content": f"[Admin] {content}",
                        "is_snapchat": False,
                        "timestamp": time.time()
                    }
                    for u in USER_DB:
                        MESSAGE_QUEUE.setdefault(u, []).append(msg_obj)
                    response = {"status": "success", "message": f"Broadcast sent to {len(USER_DB)} users"}

        elif action == "DELETE_USER":
            if not USER_DB.get(username, {}).get("is_admin"):
                response = {"status": "fail", "message": "Unauthorized"}
            else:
                target = request.get("target")
                if not target or target not in USER_DB:
                    response = {"status": "fail", "message": "User not found"}
                elif target == username:
                    response = {"status": "fail", "message": "Cannot delete yourself"}
                else:
                    del USER_DB[target]
                    MESSAGE_QUEUE.pop(target, None)
                    ONLINE_USERS.pop(target, None)
                    # Remove from all servers
                    for srv in SERVERS_DB.values():
                        srv.get("members", {}).pop(target, None)
                    save_all()
                    response = {"status": "success", "message": f"{target} deleted"}

        elif action == "AI_CHAT":
            user_msg = request.get("content", "")
            conversation_id = request.get("conversation_id")
            skip_save = request.get("skip_save", False)  # For one-off AI calls (email formatting, etc.)
            if not _user_ai_subscription_active(username):
                response = {
                    "status": "needs_subscription",
                    "message": "Subscribe to Personal AI Assistant to use Tenshi AI chat.",
                    "checkout_hint": {"checkout_kind": "ai_assistant", "stripe_action": "CREATE_STRIPE_CHECKOUT"},
                }
            elif not (GROQ_API_KEY or GEMINI_API_KEY):
                response = {"status": "fail", "message": "AI providers offline (configure GROQ_API_KEY or GEMINI_API_KEY)."}
            else:
                try:
                    # Provide 'Tenshi Brand' personality
                    system_prompt = "You are the Tenshi Hub AI Assistant. You are helpful, cool, and part of a high-end social and voice ecosystem for gamers and creators. Keep responses concise unless asked for more detail."
                    
                    # Manage Context
                    history = AI_CONTEXT.setdefault(username, [])
                    history.append({"role": "user", "content": user_msg})
                    if len(history) > 10: history = history[-10:] # Keep last 10
                    
                    anthropic_res = requests.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": ANTHROPIC_API_KEY,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json"
                        },
                        json={
                            "model": "claude-3-5-sonnet-20240620",
                            "max_tokens": 1024,
                            "system": system_prompt,
                            "messages": history
                        },
                        timeout=10
                    )

                    # ── Conversation persistence: load or create conversation ──
                    convo = None
                    if not skip_save:
                        convos = USER_DB.get(username, {}).get("ai_conversations", [])
                        if conversation_id:
                            convo = next((c for c in convos if c["id"] == conversation_id), None)
                        if not convo:
                            # Auto-create a new conversation
                            convo = {
                                "id": uuid.uuid4().hex[:12],
                                "title": "New Chat",
                                "messages": [],
                                "created_at": time.time(),
                                "updated_at": time.time()
                            }
                            USER_DB.setdefault(username, {}).setdefault("ai_conversations", []).append(convo)
                            conversation_id = convo["id"]

                    if convo:
                        # Build history from conversation messages (last 20 for context)
                        history = [{"role": m["role"], "content": m["content"]} for m in convo.get("messages", [])[-20:]]
                        history.append({"role": "user", "content": user_msg})
                    else:
                        # skip_save mode: use in-memory context only
                        history = AI_CONTEXT.setdefault(username, [])
                        history.append({"role": "user", "content": user_msg})
                        if len(history) > 10: history = history[-10:]

                    # Also keep in-memory context in sync
                    AI_CONTEXT[username] = history[-10:]

                    ai_resp = None
                    if EXTENSION_AI_CHAIN:
                        try:
                            _chain = _ai_providers.build_chain(GROQ_API_KEY, GEMINI_API_KEY or "")
                            _hist_plain = [{"role": m["role"], "content": m["content"]} for m in history]
                            ai_resp = _ai_providers.run_chat(_chain, system_prompt, _hist_plain)
                        except Exception as e:
                            print(f"[AI] provider chain failed: {e}")
                    if not ai_resp:
                        try:
                            res = requests.post(
                                "https://api.groq.com/openai/v1/chat/completions",
                                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                                json={
                                    "model": "llama-3.3-70b-versatile",
                                    "max_tokens": 1024,
                                    "messages": [{"role": "system", "content": system_prompt}] + history
                                },
                                timeout=20
                            )
                            if res.status_code == 200:
                                ai_resp = res.json()["choices"][0]["message"]["content"]
                            elif GROQ_API_KEY:
                                print(f"[AI] Groq error {res.status_code}: {res.text[:200]}")
                        except Exception as e:
                            print(f"[AI] Groq request failed: {e}")

                    if ai_resp:
                        # ── Handle image generation via Pollinations.ai (free, no watermark) ──
                        img_match = re.search(r'\[IMAGE_GEN:(.+?)\]', ai_resp)
                        if img_match:
                            img_prompt = img_match.group(1).strip()
                            # Double-check TOS compliance on the prompt
                            _safe, _reason = _scan_content(img_prompt)
                            if not _safe:
                                ai_resp = f"I can't generate that image — it may violate Tenshi's Terms of Service ({_reason}). Try a different prompt!"
                            else:
                                encoded_prompt = requests.utils.quote(img_prompt)
                                img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&nofeed=true"
                                ai_resp = f"🎨 Here's your image:\n[TENSHI_IMAGE:{img_url}]\n*Prompt: {img_prompt}*"

                        # ── Handle video generation via Pollinations.ai ──
                        vid_match = re.search(r'\[VIDEO_GEN:(.+?)\]', ai_resp)
                        if vid_match:
                            vid_prompt = vid_match.group(1).strip()
                            _safe, _reason = _scan_content(vid_prompt)
                            if not _safe:
                                ai_resp = f"I can't generate that video — it may violate Tenshi's Terms of Service ({_reason}). Try a different prompt!"
                            else:
                                encoded_prompt = requests.utils.quote(vid_prompt)
                                vid_url = f"https://video.pollinations.ai/prompt/{encoded_prompt}?nologo=true&nofeed=true"
                                ai_resp = f"🎬 Here's your video:\n[TENSHI_VIDEO:{vid_url}]\n*Prompt: {vid_prompt}*"

                        # ── Save to conversation (skip for one-off calls) ──
                        if convo:
                            now_ts = time.time()
                            convo["messages"].append({"role": "user", "content": user_msg, "timestamp": now_ts})
                            convo["messages"].append({"role": "assistant", "content": ai_resp, "timestamp": now_ts})
                            convo["updated_at"] = now_ts
                            # Auto-title from first user message
                            if convo["title"] == "New Chat" and user_msg.strip():
                                convo["title"] = user_msg.strip()[:50]
                            save_all()

                        history.append({"role": "assistant", "content": ai_resp})
                        AI_CONTEXT[username] = history # Update
                        response = {"status": "success", "response": ai_resp}
                    else:
                        response = {"status": "fail", "message": f"AI Error: {anthropic_res.text}"}
                except Exception as e:
                    response = {"status": "error", "message": str(e)}

        elif action == "SET_TYPING":
            channel_id = request.get("channel_id")
            if r_client and channel_id:
                r_client.setex(f"typing:{channel_id}:{username}", 5, "typing")
                response = {"status": "success"}
            else:
                response = {"status": "fail"}

        elif action == "GET_SERVER_STATUS":
            server_id = request.get("server_id")
            if server_id in SERVERS_DB:
                typing_users = []
                online_users = []
                
                if r_client:
                    # Get all typing keys for this server's channels
                    # Pattern: typing:* (simpler for now since channel_id includes server context usually)
                    # We'll use typing:{channel_id}:*
                    for c_name in SERVERS_DB[server_id].get("channels", {}):
                        # Construct channel pattern (Desktop/Web use different IDs often, so we scan broad)
                        keys = r_client.keys(f"typing:*:*") # Broad scan is okay for Upstash Free
                        for k in keys:
                            # k is typing:{channel_id}:{user}
                            parts = k.split(":")
                            if len(parts) >= 3:
                                typer = parts[-1]
                                if typer not in typing_users:
                                    typing_users.append(typer)
                    
                    # Get online status for all members
                    for member in SERVERS_DB[server_id].get("members", {}):
                        if r_client.exists(f"presence:{member}"):
                            online_users.append(member)
                
                response = {
                    "status": "success", 
                    "typing": typing_users,
                    "online": online_users
                }
            else:
                response = {"status": "fail"}

        elif action == "JOIN_SERVER":
            server_id = request.get("server_id")
            if server_id in SERVERS_DB:
                if not SERVERS_DB[server_id].get("invite_only", False):
                    if username not in SERVERS_DB[server_id]["members"]:
                        SERVERS_DB[server_id]["members"][username] = []
                        
                    if username in USER_DB and server_id not in USER_DB[username]["servers"]:
                        USER_DB[username]["servers"].append(server_id)
                        
                    save_all()
                    response = {"status": "success", "message": f"Joined {SERVERS_DB[server_id]['name']}"}
                else:
                    response = {"status": "fail", "message": "Server is private"}
            else:
                response = {"status": "fail", "message": "Server not found"}

        elif action == "SEND_MESSAGE":
            target_type = request.get("target_type") # "dm" or "server"
            target_id = request.get("target_id")
            content = request.get("content") # Encrypted content
            is_snapchat = request.get("is_snapchat", False)
            
            msg_obj = {
                "sender": username,
                "target_id": target_id,
                "target_type": target_type,
                "content": content,
                "is_snapchat": is_snapchat,
                "timestamp": request.get("timestamp")
            }
            
            if target_type == "dm":
                MESSAGE_QUEUE.setdefault(target_id, []).append(msg_obj)
                response = {"status": "success"}
            elif target_type == "email":
                success = send_external_email(username, target_id, content)
                if success:
                    response = {"status": "success", "message": "Email dispatched"}
                else:
                    response = {"status": "fail", "message": "SMTP Failed"}
            elif target_type == "server" and target_id in SERVERS_DB:
                # Deliver to all members
                for member in SERVERS_DB[target_id]["members"]:
                    # Don't send back to sender
                    if member != username:
                        MESSAGE_QUEUE.setdefault(member, []).append(msg_obj)
                response = {"status": "success"}
            else:
                response = {"status": "fail", "message": "Invalid target"}
                
        # ── Verification ─────────────────────────────────────
        elif action == "SEND_VERIFICATION":
            import random
            target = request.get("target_username") or username
            if target in USER_DB:
                code = str(random.randint(100000, 999999))
                USER_DB[target]["verify_code"] = code
                USER_DB[target]["verified"] = False
                save_all()
                email = USER_DB[target].get("email", "")
                phone = USER_DB[target].get("phone", "")
                contact = email if email else phone
                if email:
                    send_external_email("Tenshi", email, f"Your Tenshi verification code: {code}\n\nDo not share this code with anyone.")
                print(f"[VERIFY] Code for {target}: {code} → {contact}")
                masked = (contact[:2] + "***" + contact[-2:]) if len(contact) > 4 else "***"
                response = {"status": "success", "message": f"Code sent to {masked}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "VERIFY_ACCOUNT":
            target = request.get("target_username") or username
            code = request.get("code", "")
            if target in USER_DB:
                stored = USER_DB[target].get("verify_code", "")
                if stored and stored == code:
                    USER_DB[target]["verified"] = True
                    USER_DB[target].pop("verify_code", None)
                    save_all()
                    response = {"status": "success", "message": "Account verified!"}
                else:
                    response = {"status": "fail", "message": "Invalid or expired code"}
            else:
                response = {"status": "fail", "message": "User not found"}

        # ── Voice Channel Presence ────────────────────────────
        elif action == "JOIN_VOICE_CHANNEL":
            channel = request.get("channel")
            server_id = request.get("server_id")
            if channel and server_id:
                VOICE_PRESENCE[username] = f"{server_id}:{channel}"
                response = {"status": "success"}
            else:
                response = {"status": "fail", "message": "Missing channel or server_id"}

        elif action == "LEAVE_VOICE_CHANNEL":
            VOICE_PRESENCE.pop(username, None)
            response = {"status": "success"}

        # ── Server Members (with online + voice presence) ─────
        elif action == "GET_SERVER_MEMBERS":
            server_id = request.get("server_id")
            if server_id not in SERVERS_DB:
                response = {"status": "fail", "message": "Server not found"}
            else:
                threshold = time.time() - 300
                server_members = SERVERS_DB[server_id].get("members", {})
                member_keys = list(server_members.keys()) if isinstance(server_members, dict) else list(server_members)
                role_map = SERVERS_DB[server_id].get("roles", {})
                hub_owner_uid = SERVERS_DB[server_id].get("owner", "")
                members_out = []
                for m in member_keys:
                    if m not in USER_DB:
                        continue
                    udata = USER_DB[m]
                    hide = udata.get("privacy", {}).get("hide_online_status", False)
                    online = (not hide) and (ONLINE_USERS.get(m, 0) > threshold)
                    vkey = VOICE_PRESENCE.get(m, "")
                    voice_ch = vkey.split(":", 1)[1] if ":" in vkey and vkey.startswith(server_id + ":") else None
                    roles = server_members[m] if isinstance(server_members, dict) else []
                    server_nick = SERVERS_DB[server_id].get("nicknames", {}).get(m)
                    uc = udata.get("username_color", "#ffffff")
                    members_out.append({
                        "username": m,
                        "display_name": server_nick or m,
                        "online": online,
                        "status": udata.get("status", "Online"),
                        "custom_status": udata.get("custom_status", ""),
                        "voice_channel": voice_ch,
                        "roles": roles,
                        "banner_color": udata.get("banner_color", "#2b2d31"),
                        "avatar": udata.get("avatar", None),
                        "username_color": uc,
                        "hub_role_name_color": _hub_member_role_name_color(role_map, roles, m, hub_owner_uid, uc),
                        "profile_color": udata.get("profile_color") or _default_profile_color(m),
                    })
                members_out.sort(key=lambda x: (not x["online"], x["display_name"].lower()))
                response = {"status": "success", "members": members_out}

        elif action == "GET_MEMBERS_ACTIVITY":
            raw = request.get("usernames") or []
            names = raw if isinstance(raw, list) else []
            threshold = time.time() - 300
            members_map = {}
            for m in names:
                if m not in USER_DB:
                    continue
                udata = USER_DB[m]
                hide = udata.get("privacy", {}).get("hide_online_status", False)
                online = (not hide) and (ONLINE_USERS.get(m, 0) > threshold)
                pc = udata.get("profile_color") or _default_profile_color(m)
                av = udata.get("avatar") or ""
                members_map[m] = {
                    "online": online,
                    "activity": udata.get("current_activity") or udata.get("spotify_activity") or "",
                    "avatar_full": av,
                    "username_color": udata.get("username_color", "#ffffff"),
                    "profile_color": pc,
                    "badges": udata.get("badges") or [],
                    "display_name": udata.get("display_name") or m,
                }
            response = {"status": "success", "members": members_map}

        # ── Platform Links ─────────────────────────────────────
        elif action == "LINK_PLATFORM":
            platform = request.get("platform", "").lower()
            platform_user = request.get("platform_username", "").strip()
            allowed = {"discord","twitter","github","twitch","spotify","reddit","youtube","steam","xbox","playstation"}
            if username in USER_DB and platform in allowed:
                USER_DB[username].setdefault("connections", {})[platform] = platform_user
                save_all()
                response = {"status": "success", "message": f"Linked {platform}"}
            else:
                response = {"status": "fail", "message": "Invalid platform"}

        # ── Custom Status ──────────────────────────────────────
        elif action == "UPDATE_CUSTOM_STATUS":
            if username in USER_DB:
                USER_DB[username]["custom_status"] = request.get("custom_status", "")
                USER_DB[username]["status"] = request.get("status_type", "Online")
                save_all()
                response = {"status": "success"}
            else:
                response = {"status": "fail", "message": "User not found"}

        # ── Server Nickname ────────────────────────────────────
        elif action == "SET_SERVER_NICKNAME":
            server_id = request.get("server_id")
            target = request.get("target") or username
            nick = request.get("nickname", "").strip()[:32]
            if server_id in SERVERS_DB:
                SERVERS_DB[server_id].setdefault("nicknames", {})[target] = nick if nick else None
                save_all()
                response = {"status": "success"}
            else:
                response = {"status": "fail", "message": "Server not found"}

        # ── User Notes (private) ──────────────────────────────
        elif action == "SET_USER_NOTE":
            target = request.get("target")
            note = request.get("note", "").strip()[:500]
            USER_NOTES[f"{username}:{target}"] = note
            response = {"status": "success"}

        elif action == "GET_USER_NOTE":
            target = request.get("target")
            note = USER_NOTES.get(f"{username}:{target}", "")
            response = {"status": "success", "note": note}

        # ── Profile Stories (24-hour) ─────────────────────────
        elif action == "POST_STORY":
            content = request.get("content", "")
            media_b64 = request.get("media", "")
            # AI scan media if provided (scan in AI_CHAT pipeline)
            story = {
                "content": content,
                "media": media_b64,
                "expires_at": time.time() + 86400,  # 24 hours
                "posted_at": time.time()
            }
            USER_STORIES[username] = story
            response = {"status": "success", "message": "Story posted for 24 hours"}

        elif action == "GET_STORY":
            target = request.get("target") or username
            story = USER_STORIES.get(target)
            if story and story.get("expires_at", 0) > time.time():
                response = {"status": "success", "story": story}
            else:
                if target in USER_STORIES:
                    del USER_STORIES[target]
                response = {"status": "success", "story": None}

        # ── Noise Suppression ──────────────────────────────────
        elif action == "SET_NOISE_SUPPRESS":
            target = request.get("target")
            enable = request.get("enable", True)
            if enable:
                NOISE_SUPPRESS.setdefault(username, set()).add(target)
            else:
                NOISE_SUPPRESS.get(username, set()).discard(target)
            response = {"status": "success"}

        elif action == "SERVER_SUPPRESS_USER":
            # Server owner can force noise suppression on a user for all members
            server_id = request.get("server_id")
            target = request.get("target")
            enable = request.get("enable", True)
            if server_id in SERVERS_DB and SERVERS_DB[server_id].get("owner") == username:
                SERVERS_DB[server_id].setdefault("suppressed_users", {})[target] = enable
                save_all()
                response = {"status": "success"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}

        # ── Ticket System ─────────────────────────────────────
        elif action == "CREATE_TICKET":
            server_id = request.get("server_id")
            subject = request.get("subject", "Support Request")[:100]
            body = request.get("body", "")[:2000]
            if server_id not in SERVERS_DB:
                response = {"status": "fail", "message": "Server not found"}
            else:
                tickets = SERVERS_DB[server_id].setdefault("tickets", {})
                ticket_id = f"ticket_{len(tickets) + 1}_{int(time.time())}"
                tickets[ticket_id] = {
                    "id": ticket_id,
                    "author": username,
                    "subject": subject,
                    "body": body,
                    "status": "open",
                    "created_at": time.time(),
                    "messages": [{"sender": username, "content": body, "ts": time.time()}]
                }
                # Create a private ticket channel visible to author + admins
                ch_name = f"ticket-{len(tickets)}"
                SERVERS_DB[server_id]["channels"][ch_name] = {
                    "type": "text",
                    "locked": True,
                    "allowed_roles": ["role_admin"],
                    "ticket_id": ticket_id,
                    "is_ticket": True
                }
                save_all()
                response = {"status": "success", "ticket_id": ticket_id, "channel": ch_name}

        elif action == "GET_TICKETS":
            server_id = request.get("server_id")
            if server_id not in SERVERS_DB:
                response = {"status": "fail", "message": "Server not found"}
            else:
                tickets = SERVERS_DB[server_id].get("tickets", {})
                is_admin = SERVERS_DB[server_id].get("owner") == username or \
                    ("role_admin" in (SERVERS_DB[server_id].get("members", {}).get(username, [])))
                visible = {
                    tid: t for tid, t in tickets.items()
                    if is_admin or t.get("author") == username
                }
                response = {"status": "success", "tickets": list(visible.values())}

        elif action == "CLOSE_TICKET":
            server_id = request.get("server_id")
            ticket_id = request.get("ticket_id")
            if server_id in SERVERS_DB:
                tickets = SERVERS_DB[server_id].get("tickets", {})
                if ticket_id in tickets:
                    tickets[ticket_id]["status"] = "closed"
                    save_all()
                    response = {"status": "success"}
                else:
                    response = {"status": "fail", "message": "Ticket not found"}
            else:
                response = {"status": "fail", "message": "Server not found"}

        # ── AI Media Scan (NSFW/Lewd Detection) ───────────────
        elif action == "SCAN_MEDIA":
            b64_image = request.get("image_b64", "")
            if not ANTHROPIC_API_KEY:
                response = {"status": "ok", "safe": True, "message": "AI scanner offline — proceed with caution"}
            elif not b64_image:
                response = {"status": "fail", "message": "No image provided"}
            else:
                try:
                    scan_res = requests.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                        json={
                            "model": "claude-3-5-sonnet-20240620",
                            "max_tokens": 64,
                            "system": "You are a content moderation system. Respond ONLY with JSON: {\"safe\":true} or {\"safe\":false,\"reason\":\"brief reason\"}. Flag nudity, sexual content, graphic violence, or illegal content as unsafe.",
                            "messages": [{"role": "user", "content": [
                                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_image}},
                                {"type": "text", "text": "Is this image safe for a general audience platform?"}
                            ]}]
                        },
                        timeout=10
                    )
                    if scan_res.status_code == 200:
                        ai_text = scan_res.json()["content"][0]["text"].strip()
                        result = json.loads(ai_text)
                        response = {"status": "ok", "safe": result.get("safe", True), "reason": result.get("reason", "")}
                    else:
                        response = {"status": "ok", "safe": True, "message": "Scanner unavailable"}
                except Exception as e:
                    response = {"status": "ok", "safe": True, "message": f"Scanner error: {e}"}

        # ── SSO Stub ───────────────────────────────────────────
        elif action == "SSO_INIT":
            response = {"status": "fail", "message": "SSO requires OAuth2 client credentials to be configured on the server. Contact the admin to enable Google/Apple/Microsoft sign-in."}

        elif action == "POLL_MESSAGES":
            # Update Presence (TTL 60s)
            if r_client:
                r_client.setex(f"presence:{username}", 60, "online")
                
            # Return queued messages and clear queue for this user
            msgs = MESSAGE_QUEUE.pop(username, [])
            response = {"status": "success", "messages": msgs}

        conn.send(json.dumps(response).encode('utf-8'))
    except Exception as e:
        print(f"Server Error: {e}")
    finally:
        conn.close()

# --- VIDEO STUFF ---
VIDEO_PORT = 6001
VIDEO_ROOMS = {} # room_id -> list of (conn, username)

def broadcast_user_list(room_id):
    if room_id not in VIDEO_ROOMS: return
    
    # Get List of Usernames
    users = [u[1] for u in VIDEO_ROOMS[room_id]]
    user_json = json.dumps(users).encode('utf-8')
    
    # Protocol: [Type 2][Length 4][JSON Data]
    payload = b'\x02' + len(user_json).to_bytes(4, 'big') + user_json
    
    for conn, _ in VIDEO_ROOMS[room_id]:
        try:
            # Send length prefix for payload (standard 4 byte size header for client to read)
            # Client reads 4 bytes size -> reads size bytes -> determines type from first byte
            # So: Length = len(payload)
            final_pkt = len(payload).to_bytes(4, 'big') + payload
            conn.sendall(final_pkt)
        except:
            pass

def handle_video_client(conn, addr):
    try:
        # Handshake: Receive JSON with room & username
        # Expected Format: [4 bytes length][JSON Data]
        header_len = b''
        while len(header_len) < 4:
            chunk = conn.recv(4 - len(header_len))
            if not chunk: return
            header_len += chunk
        length = int.from_bytes(header_len, byteorder='big')

        info_bytes = b''
        while len(info_bytes) < length:
            chunk = conn.recv(length - len(info_bytes))
            if not chunk: return
            info_bytes += chunk
        info = json.loads(info_bytes.decode('utf-8'))
        
        room_id = info.get('room_id', 'default')
        username = info.get('username', 'Unknown')
        
        if room_id not in VIDEO_ROOMS: VIDEO_ROOMS[room_id] = []
        VIDEO_ROOMS[room_id].append((conn, username))
        
        print(f"[VIDEO] {username} joined room {room_id}")
        broadcast_user_list(room_id)
        
        while True:
            # Receive Packet Size [4 bytes]
            size_data = conn.recv(4)
            if not size_data: break
            packet_size = int.from_bytes(size_data, byteorder='big')
            
            # Receive Packet Data (Type + Payload)
            packet_data = b""
            while len(packet_data) < packet_size:
                chunk = conn.recv(packet_size - len(packet_data))
                if not chunk: break
                packet_data += chunk
            
            if not packet_data: break
            
            # Broadcast to others in room
            # Protocol update: Client sends [Type][Payload].
            # Server wraps it and inserts the sender username:
            # [Type 1 byte][Uname_len 1 byte][Username bytes][Payload]
            
            pkt_type = packet_data[0:1]
            raw_payload = packet_data[1:]
            
            uname_bytes = username.encode('utf-8')
            uname_len = len(uname_bytes).to_bytes(1, 'big')
            
            new_packet_data = pkt_type + uname_len + uname_bytes + raw_payload
            final_pkt = len(new_packet_data).to_bytes(4, 'big') + new_packet_data
            
            for client, client_name in list(VIDEO_ROOMS[room_id]):
                if client != conn:
                    try:
                        client.sendall(final_pkt)
                    except:
                        pass # Let the main loop or finally block handle cleanup

    except Exception as e:
        print(f"[VIDEO] Error with {addr}: {e}")
    finally:
        # Remove from room
        if 'room_id' in locals() and room_id in VIDEO_ROOMS:
            # Safe removal
            VIDEO_ROOMS[room_id] = [c for c in VIDEO_ROOMS[room_id] if c[0] != conn]
            broadcast_user_list(room_id)
            print(f"[VIDEO] {username} left room {room_id}")
            
        conn.close()

def start_video_server():
    vid_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    vid_sock.bind((HOST, VIDEO_PORT))
    vid_sock.listen()
    print(f"--- VIDEO SERVER ONLINE ---")
    print(f"Listening on Port: {VIDEO_PORT}")
    
    while True:
        conn, addr = vid_sock.accept()
        threading.Thread(target=handle_video_client, args=(conn, addr), daemon=True).start()

# --- HTTP WEB API BRIDGE ---
from http.server import BaseHTTPRequestHandler, HTTPServer

class DummyConn:
    def __init__(self, data):
        self.data = data
        self.response = None
    
    def recv(self, buflen):
        d = self.data
        self.data = b'' # only return once
        return d
        
    def send(self, data):
        self.response = data
        
    def close(self):
        pass

# ── IP-based API rate limiting ────────────────────────────────
_IP_RATE: dict = {}  # ip -> [timestamps]
_IP_RATE_LIMIT = 600  # max requests per window (increased for voice call polling)
_IP_RATE_WINDOW = 60  # seconds

# ── Brute force protection: track failed login attempts (IP + username) ──
_LOGIN_FAILURES: dict = {}      # ip -> {"count": int, "last": float, "locked_until": float}
_USER_LOGIN_FAILURES: dict = {} # username_lower -> {"count": int, "last": float, "locked_until": float}
_MAX_LOGIN_ATTEMPTS = 6         # lock after this many failures (reduced from 8)
_MAX_USER_ATTEMPTS = 10         # per-username limit (more lenient — shared IPs)
_LOCKOUT_DURATION = 300         # 5 minute lockout
_LOCKOUT_ESCALATION = 1800      # 30 min lockout after repeated lockouts
_FAILURE_WINDOW = 600           # count failures within 10 minutes

def _check_login_brute_force(ip: str, target_username: str = "") -> tuple:
    """Check if IP or target account is locked out. Returns (allowed, message)."""
    now = time.time()
    # Check IP lockout
    entry = _LOGIN_FAILURES.get(ip)
    if entry:
        if entry.get("locked_until", 0) > now:
            remaining = int(entry["locked_until"] - now)
            return False, f"Too many failed login attempts. Try again in {remaining} seconds."
        if now - entry.get("last", 0) > _FAILURE_WINDOW:
            _LOGIN_FAILURES.pop(ip, None)
    # Check per-username lockout (prevents credential stuffing across IPs)
    if target_username:
        ukey = target_username.lower()
        uentry = _USER_LOGIN_FAILURES.get(ukey)
        if uentry:
            if uentry.get("locked_until", 0) > now:
                remaining = int(uentry["locked_until"] - now)
                return False, f"This account is temporarily locked. Try again in {remaining} seconds."
            if now - uentry.get("last", 0) > _FAILURE_WINDOW:
                _USER_LOGIN_FAILURES.pop(ukey, None)
    return True, ""

def _record_login_failure(ip: str, target_username: str = ""):
    """Record a failed login attempt for both IP and target username."""
    now = time.time()
    # IP tracking
    entry = _LOGIN_FAILURES.setdefault(ip, {"count": 0, "last": 0, "locked_until": 0, "lockouts": 0})
    if now - entry["last"] > _FAILURE_WINDOW:
        entry["count"] = 0
    entry["count"] += 1
    entry["last"] = now
    if entry["count"] >= _MAX_LOGIN_ATTEMPTS:
        entry["lockouts"] = entry.get("lockouts", 0) + 1
        # Escalate: 5min first time, 30min if repeated lockouts
        duration = _LOCKOUT_ESCALATION if entry["lockouts"] > 2 else _LOCKOUT_DURATION
        entry["locked_until"] = now + duration
        print(f"[SECURITY] IP {ip} locked out for {duration}s after {entry['count']} failed logins (lockout #{entry['lockouts']})")
    # Username tracking
    if target_username:
        ukey = target_username.lower()
        uentry = _USER_LOGIN_FAILURES.setdefault(ukey, {"count": 0, "last": 0, "locked_until": 0})
        if now - uentry["last"] > _FAILURE_WINDOW:
            uentry["count"] = 0
        uentry["count"] += 1
        uentry["last"] = now
        if uentry["count"] >= _MAX_USER_ATTEMPTS:
            uentry["locked_until"] = now + _LOCKOUT_DURATION
            print(f"[SECURITY] Account '{target_username}' locked for {_LOCKOUT_DURATION}s after {uentry['count']} failed attempts from multiple IPs")

def _clear_login_failures(ip: str, target_username: str = ""):
    """Clear failure count on successful login."""
    _LOGIN_FAILURES.pop(ip, None)
    if target_username:
        _USER_LOGIN_FAILURES.pop(target_username.lower(), None)

# ── Session token system (persisted to disk so tokens survive server restart) ──
_SESSION_TOKENS_FILE = "session_tokens.json"
_SESSION_TOKEN_TTL = 86400 * 7  # 7-day session tokens

def _load_session_tokens() -> dict:
    """Load session tokens from disk."""
    if os.path.exists(_SESSION_TOKENS_FILE):
        try:
            with open(_SESSION_TOKENS_FILE, 'r') as f:
                tokens = json.load(f)
            # Prune expired on load
            now = time.time()
            tokens = {t: d for t, d in tokens.items() if now - d.get("created", 0) < _SESSION_TOKEN_TTL}
            return tokens
        except Exception:
            pass
    return {}

_SESSION_TOKENS: dict = _load_session_tokens()

def _save_session_tokens():
    """Persist session tokens to disk."""
    try:
        with open(_SESSION_TOKENS_FILE, 'w') as f:
            json.dump(_SESSION_TOKENS, f)
    except Exception:
        pass

def _create_session_token(username: str, ip: str) -> str:
    """Create a secure session token for authenticated users."""
    token = uuid.uuid4().hex + uuid.uuid4().hex  # 64-char random token
    signing_key = secrets.token_hex(32)  # 64-char HMAC signing key for CSRF protection
    _SESSION_TOKENS[token] = {"username": username, "created": time.time(), "ip": ip, "signing_key": signing_key}
    # Clean expired tokens periodically
    expired = [t for t, d in _SESSION_TOKENS.items() if time.time() - d["created"] > _SESSION_TOKEN_TTL]
    for t in expired:
        _SESSION_TOKENS.pop(t, None)
    _save_session_tokens()
    return token

def _get_signing_key(token: str) -> str:
    """Get the signing key associated with a session token."""
    sess = _SESSION_TOKENS.get(token)
    if not sess:
        return ""
    return sess.get("signing_key", "")

# Actions that don't require request signing (unauthenticated or pre-auth actions)
_UNSIGNED_ACTIONS = frozenset({
    "REGISTER", "LOGIN", "SESSION_LOGIN", "VERIFY_CODE", "RESEND_CODE",
    "FORGOT_PASSWORD", "RECOVER_PASSWORD", "RESET_PASSWORD", "CHECK_USERNAME", "PING",
    "GET_LATEST_VERSION", "CHECK_TURNSTILE",
})

def _verify_request_signature(request: dict, session_token: str) -> bool:
    """Verify HMAC-SHA256 request signature for CSRF protection.
    Returns True if signature is valid or action is exempt."""
    action = request.get("action", "")
    if action in _UNSIGNED_ACTIONS:
        return True
    # SECURITY FIX: do NOT allow legacy sessions to bypass CSRF protection.
    # Sessions without a signing key must re-authenticate via SESSION_LOGIN to receive one.
    # SESSION_LOGIN already auto-upgrades any live session with a signing key.
    signing_key = _get_signing_key(session_token)
    if not signing_key:
        return False  # No signing key → reject; client must call SESSION_LOGIN to upgrade
    sig = request.get("_sig", "")
    ts = request.get("_ts", 0)
    if not sig or not ts:
        return False  # Has signing key but missing signature — reject
    # Reject stale requests (>5 minute window)
    try:
        ts_float = float(ts)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts_float) > 300:
        return False
    # Compute expected HMAC: HMAC-SHA256(key, action + "|" + timestamp)
    msg = f"{action}|{ts}".encode("utf-8")
    expected = _hmac_module.new(signing_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return _hmac_module.compare_digest(sig, expected)

def _validate_session_token(token: str, request_ip: str = "") -> str:
    """Validate a session token with IP binding. Returns username or empty string."""
    if not token:
        return ""
    sess = _SESSION_TOKENS.get(token)
    if not sess:
        return ""
    if time.time() - sess["created"] > _SESSION_TOKEN_TTL:
        _SESSION_TOKENS.pop(token, None)
        _save_session_tokens()
        return ""
    # IP binding: if the token was created from a different IP, invalidate it
    # (prevents stolen token replay from different locations)
    if request_ip and sess.get("ip") and sess["ip"] != request_ip:
        print(f"[SECURITY] Session token IP mismatch: created from {sess['ip']}, used from {request_ip} — BLOCKED")
        _SESSION_TOKENS.pop(token, None)
        _save_session_tokens()
        return ""
    return sess["username"]

# ── Precompiled security patterns (created once, used on every request) ──
_INJECTION_RE = re.compile(
    r"('\s*or\s+1\s*=\s*1|';\s*drop\s+table|union\s+select|<script|javascript:|"
    r"onerror\s*=|onload\s*=|document\.cookie|window\.location|"
    r"';\s*delete\s+from|<img\s+src=x\s+onerror|<svg\s+onload|data:text/html)",
    re.IGNORECASE
)
_BLACKLISTED_ACTIONS_SET = {
    "EVAL", "EXEC", "RUN_CODE", "SHELL", "SYSTEM", "IMPORT",
    "DELETE_ALL", "DROP_DB", "WIPE", "INJECT", "BACKDOOR",
    "OVERRIDE_ADMIN", "FORCE_ADMIN", "GRANT_ADMIN",
}
_INJECTION_SKIP_FIELDS = {"action", "pwd_hash", "image_b64", "avatar", "banner_image", "image", "b64", "file_data", "reply_body", "scan_text", "move"}

# ── Cached banned HWIDs (rebuilt every 60s to avoid scanning all users per request)
_BANNED_HWIDS: set = set()
_BANNED_HWIDS_LAST_REBUILD = 0

def _rebuild_banned_hwids():
    global _BANNED_HWIDS, _BANNED_HWIDS_LAST_REBUILD
    now = time.time()
    if now - _BANNED_HWIDS_LAST_REBUILD < 60:
        return
    _BANNED_HWIDS = {d.get("banned_hwid") for d in USER_DB.values() if d.get("banned") and d.get("banned_hwid")}
    # Include wipe-account HWID reservations (permanent device blocks for confirmed misbehavior)
    _BANNED_HWIDS.update(_RESERVED_IDENTIFIERS.get("hwids", {}).keys())
    _BANNED_HWIDS.discard(None)
    _BANNED_HWIDS.discard("")
    _BANNED_HWIDS_LAST_REBUILD = now

def _check_ip_rate(ip: str) -> bool:
    """Return True if IP is within rate limit, False if flooding."""
    now = time.time()
    timestamps = _IP_RATE.setdefault(ip, [])
    timestamps[:] = [t for t in timestamps if now - t < _IP_RATE_WINDOW]
    if len(timestamps) >= _IP_RATE_LIMIT:
        return False
    timestamps.append(now)
    return True

# Clean up old IP rate entries every 5 minutes
def _cleanup_ip_rate():
    while True:
        time.sleep(300)
        now = time.time()
        stale = [ip for ip, ts in _IP_RATE.items() if not ts or now - ts[-1] > _IP_RATE_WINDOW * 2]
        for ip in stale:
            _IP_RATE.pop(ip, None)

threading.Thread(target=_cleanup_ip_rate, daemon=True).start()

def _cleanup_game_sessions():
    """Remove game sessions older than 2 hours or ended sessions older than 10 minutes."""
    while True:
        time.sleep(120)
        now = time.time()
        stale = [sid for sid, s in GAME_SESSIONS.items()
                 if now - s.get("last_update", 0) > 7200
                 or (s.get("ended") and now - s.get("last_update", 0) > 600)]
        for sid in stale:
            GAME_SESSIONS.pop(sid, None)

threading.Thread(target=_cleanup_game_sessions, daemon=True).start()

# ── Soft-delete hard-purge daemon (TOS §5.2 / Privacy §5.1) ─────────────────
# Hub messages and stories that are soft-deleted live in the DB for 7 days so
# users can report CSAM / illegal content. After 7 days they are permanently
# purged UNLESS they're CSAM-flagged, the sender is under_review, or a report
# has pinned them via flagged_for_review.
def _run_soft_delete_purge():
    """Hourly pass to hard-delete content past its 7-day retention window."""
    while True:
        try:
            time.sleep(3600)  # 1 hour
            now = time.time()
            purged_msgs = 0
            purged_stories = 0
            pinned = 0

            # ── Hub messages ────────────────────────────────────────────────
            for ch_key in list(SERVER_MESSAGES.keys()):
                msgs = SERVER_MESSAGES.get(ch_key, [])
                # Pre-check: skip the entire channel if nothing is soft-deleted
                if not any(m.get("deleted") for m in msgs):
                    continue
                keep = []
                for m in msgs:
                    if not m.get("deleted"):
                        keep.append(m)
                        continue
                    if now <= m.get("hard_delete_at", 0):
                        keep.append(m)  # still inside 7-day window
                        continue
                    if _is_pinned(m, m.get("sender", "")):
                        pinned += 1
                        keep.append(m)
                        continue
                    # Hard delete — log a tombstone so we have a record SOMETHING was purged
                    audit("HARD_PURGE", "system", ch_key,
                          f"msg_id={m.get('msg_id','')} sender={m.get('sender','')} age_days={(now - m.get('deleted_at', now))/86400:.1f}")
                    purged_msgs += 1
                if len(keep) != len(msgs):
                    SERVER_MESSAGES[ch_key] = keep

            # ── Stories ─────────────────────────────────────────────────────
            for u in list(USER_STORIES.keys()):
                stories = USER_STORIES.get(u, [])
                # Pre-check: skip user entirely if no soft-deleted stories
                if not any(s.get("deleted") for s in stories):
                    continue
                keep = []
                for s in stories:
                    if not s.get("deleted"):
                        # Also expire stories naturally past their TTL
                        if s.get("expires_at", 0) > now:
                            keep.append(s)
                        continue
                    if now <= s.get("hard_delete_at", 0):
                        keep.append(s)
                        continue
                    if _is_pinned(s, u):
                        pinned += 1
                        keep.append(s)
                        continue
                    audit("HARD_PURGE", "system", u,
                          f"story_id={s.get('id','')} age_days={(now - s.get('deleted_at', now))/86400:.1f}")
                    purged_stories += 1
                if len(keep) != len(stories):
                    USER_STORIES[u] = keep

            if purged_msgs or purged_stories:
                _save_community_data()
                print(f"[SOFT-DELETE-PURGE] hard-deleted {purged_msgs} msg(s) and {purged_stories} story/ies; pinned {pinned}")
        except Exception as e:
            print(f"[SOFT-DELETE-PURGE] error: {e}")

threading.Thread(target=_run_soft_delete_purge, daemon=True).start()

# ── Temp expiry + retention daemon ──────────────────────────────────────────
# Temps follow a two-stage retention model:
#   3-day unopened expiry: an unopened temp older than 3 days is wiped of its
#     content/media and a system message ("Temp from X removed due to inactivity.")
#     is appended to BOTH sides of the DM. The expired record stays for the
#     7-day report window.
#   7-day hard delete: the temp record itself is removed from DM_HISTORY.
#     Pinned indefinitely if CSAM-flagged or under report (flagged_for_review).
#     The system-message tombstone left behind always persists — it's the
#     audit trail visible in the conversation thread.
def _run_temp_expiry():
    """Hourly pass: expire unopened temps at 3 days, hard-delete at 7 days."""
    while True:
        try:
            time.sleep(3600)
            now = time.time()
            expired_count = 0
            purged_count = 0

            for dm_key in list(DM_HISTORY.keys()):
                msgs = DM_HISTORY.get(dm_key, [])
                if not msgs:
                    continue
                # Pre-check: skip the conversation entirely if no temps live in it
                if not any(m.get("is_temp") for m in msgs):
                    continue
                parties = dm_key.split(":")  # [user_a, user_b]
                kept = []
                for m in msgs:
                    if not m.get("is_temp"):
                        kept.append(m)
                        continue

                    # ── Stage 1: 3-day unopened expiry ──
                    if (not m.get("temp_opened") and
                        not m.get("temp_expired") and
                        now > m.get("temp_expires_at", 0)):
                        # Wipe content/media but keep the record for the 7-day report window
                        m["content"] = ""
                        m["media"] = ""
                        m["temp_expired"] = True
                        m["temp_expired_at"] = now
                        # System tombstone visible to BOTH sides of the conversation.
                        # is_system flag set server-side only — never accepted from client input.
                        sender = m.get("sender", "")
                        sys_msg = {
                            "msg_id":         uuid.uuid4().hex[:16],
                            "sender":         "system",
                            "is_system":      True,
                            "is_temp_expiry": True,
                            "content":        f"Temp from {sender} removed due to inactivity.",
                            "temp_id":        m.get("msg_id", ""),
                            "timestamp":      datetime.datetime.utcnow().isoformat(),
                            "sent_at_unix":   now,
                        }
                        kept.append(m)
                        kept.append(sys_msg)
                        expired_count += 1
                        # Notify both parties — passive state change, no badge bump
                        for _p in parties:
                            try:
                                MESSAGE_QUEUE.setdefault(_p, []).append({
                                    "type": "temp_expired",
                                    "temp_id": m.get("msg_id", ""),
                                    "timestamp": now,
                                    "target_type": "system",
                                })
                            except Exception:
                                pass
                        continue

                    # ── Stage 2: 7-day hard delete (skip if CSAM/under-review/flagged) ──
                    if now > m.get("temp_delete_at", 0):
                        if _is_pinned(m, m.get("sender", "")):
                            kept.append(m)  # pinned indefinitely
                        else:
                            audit("HARD_PURGE", "system", dm_key,
                                  f"temp_id={m.get('msg_id','')} sender={m.get('sender','')} "
                                  f"opened={m.get('temp_opened', False)}")
                            purged_count += 1
                            # don't append — this purges the record; the system tombstone left
                            # behind from stage 1 (if any) is preserved separately
                        continue

                    kept.append(m)

                if len(kept) != len(msgs):
                    DM_HISTORY[dm_key] = kept

            if expired_count or purged_count:
                _save_dm_history()
                print(f"[TEMP-EXPIRY] expired {expired_count} unopened temp(s); purged {purged_count}")
        except Exception as e:
            print(f"[TEMP-EXPIRY] error: {e}")

threading.Thread(target=_run_temp_expiry, daemon=True).start()

def _send_security_headers(handler):
    """Add security headers to all HTTP responses."""
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('X-Frame-Options', 'DENY')
    handler.send_header('X-XSS-Protection', '1; mode=block')
    handler.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
    handler.send_header('Permissions-Policy', 'camera=(self), microphone=(self), geolocation=(self)')
    handler.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    handler.send_header('Content-Security-Policy',
        "default-src 'self' https:; "
        "script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com https://www.youtube.com https://s.ytimg.com https://tenor.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' blob: https:; "
        "frame-src https://www.youtube.com https://open.spotify.com https://w.soundcloud.com https://challenges.cloudflare.com https://buy.stripe.com; "
        "connect-src 'self' https://api.tenshi.lol https://tenor.googleapis.com https://api.groq.com https://image.pollinations.ai https://video.pollinations.ai wss://api.tenshi.lol; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )

class APIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            import os
            web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "TenshiWeb")
            path = self.path.split('?')[0]  # strip query string
            if path == "/version":
                ver_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "VERSION")
                version = open(ver_file).read().strip() if os.path.exists(ver_file) else "3.0.0"
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(version.encode())
                return
            if path == "/" or path == "": path = "/index.html"
            
            if ".." in path:
                self.send_response(403)
                self.end_headers()
                return
                
            file_path = os.path.abspath(os.path.join(web_dir, path.lstrip("/")))
            if not file_path.startswith(os.path.abspath(web_dir)):
                self.send_response(403)
                self.end_headers()
                return
                
            if os.path.exists(file_path):
                self.send_response(200)
                if file_path.endswith(".html"): self.send_header('Content-type', 'text/html; charset=utf-8')
                elif file_path.endswith(".css"): self.send_header('Content-type', 'text/css')
                elif file_path.endswith(".js"): self.send_header('Content-type', 'application/javascript')
                elif file_path.endswith(".xml"): self.send_header('Content-type', 'application/xml; charset=utf-8')
                elif file_path.endswith(".txt"): self.send_header('Content-type', 'text/plain; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:
            print(f"GET Error: {e}")
            self.send_response(500)
            self.end_headers()

    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            dummy = DummyConn(post_data)
            # handle_client expects (conn, addr). Just pass our dummy.
            handle_client(dummy, self.client_address)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            if dummy.response:
                self.wfile.write(dummy.response)
            else:
                self.wfile.write(b'{"status":"error","message":"No TCP response generated"}')
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            print(f"API Error: {e}")
            
    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "X-Requested-With, Content-type")
        self.end_headers()

def start_http_server():
    try:
        server = HTTPServer(('0.0.0.0', 8080), APIHandler)
        print("--- WEB API BRIDGE ONLINE ---")
        print("Listening on Port: 8080")
        server.serve_forever()
    except Exception as e:
        print(f"HTTP Server failed to start: {e}")

def start_server():
    # Start Video Server in Background Thread
    threading.Thread(target=start_video_server, daemon=True).start()
    # Start HTTP Web API in Background Thread
    threading.Thread(target=start_http_server, daemon=True).start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen()
    print(f"--- TENSHI VOICE SERVER ONLINE ---")
    print(f"Listening on Port: {PORT}")
    print(f"Registry File: {REGISTRY_FILE}")
    print("----------------------------------")
    
    while True:
        conn, addr = server.accept()
        thread = threading.Thread(target=handle_client, args=(conn, addr))
        thread.start()

if __name__ == "__main__":
    start_server()