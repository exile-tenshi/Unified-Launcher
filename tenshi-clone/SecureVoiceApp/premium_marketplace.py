# Generated premium-ish usernames marketplace (server-side inventory + reservation helpers).
from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from typing import Any, Dict, List, Tuple

_MARKET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "premium_username_marketplace.json")

_ADJECTIVES = (
    "Lunar","Solar","Nova","Neo","Phantom","Cyber","Royal","Silent","Frozen","Golden",
    "Ivory","Crimson","Azure","Ghost","Twin","Ultra","Mega","Neo","Ace","Zen",
)

_NOUNS = (
    "Fox","Dragon","Tiger","Spirit","Knight","Nova","Pulse","Echo","Storm","Blade",
    "Wolf","Raven","Hawk","Phantom","Knight","Soul","Ace","Glow","Flux","Orb",
)


def load_marketplace() -> dict:
    if os.path.exists(_MARKET_PATH):
        try:
            with open(_MARKET_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"listings": [], "next_gen": time.time()}


def save_marketplace(data: dict) -> None:
    with open(_MARKET_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f)


def seed_listings_if_empty(target_count: int = 24, price_cents: int = 999) -> int:
    mp = load_marketplace()
    listings: List[dict] = mp.get("listings") or []
    available = sum(1 for L in listings if L.get("status") == "available")
    added = 0
    tries = 0
    handles = {L["handle"].lower() for L in listings}
    while available + added < target_count and tries < 500:
        tries += 1
        h = _gen_handle(handles)
        if not h:
            continue
        handles.add(h.lower())
        listings.append({
            "id": uuid.uuid4().hex[:12],
            "handle": h,
            "price_cents": price_cents,
            "status": "available",
            "reserved_by": "",
            "reserved_until": 0.0,
            "sold_to": "",
            "sold_at": 0.0,
        })
        added += 1
    mp["listings"] = listings
    mp["next_gen"] = time.time() + 3600
    save_marketplace(mp)
    return added


def _gen_handle(used: set) -> str:
    for _ in range(60):
        a = secrets.choice(_ADJECTIVES)
        n = secrets.choice(_NOUNS)
        suf = secrets.randbelow(9999)
        base = f"{a}{n}{suf}".lower()
        base = base[:16]
        if base and base not in used and re_valid_handle(base):
            return base
    return ""


def re_valid_handle(s: str) -> bool:
    import re

    return bool(re.match(r"^[a-zA-Z0-9._-]{3,16}$", s))


def list_available(limit: int = 50) -> List[dict]:
    mp = load_marketplace()
    out = []
    for L in mp.get("listings", []):
        if L.get("status") != "available":
            continue
        if float(L.get("reserved_until") or 0) > time.time():
            rb = L.get("reserved_by")
            if rb:
                continue
        out.append(
            {
                "id": L["id"],
                "handle": L["handle"],
                "price_cents": L.get("price_cents", 999),
                "status": L.get("status"),
            }
        )
        if len(out) >= limit:
            break
    return out


def get_listing(listing_id: str) -> Tuple[Any, dict]:
    mp = load_marketplace()
    for L in mp.get("listings", []):
        if L.get("id") == listing_id:
            return mp, L
    return mp, None


def reserve(listing_id: str, username: str, ttl_sec: int = 900) -> Tuple[bool, str, dict]:
    mp, L = get_listing(listing_id)
    if not L:
        return False, "Listing not found", {}
    now = time.time()
    ru = float(L.get("reserved_until") or 0)
    rb = L.get("reserved_by") or ""
    if L.get("status") != "available":
        return False, "Listing not available", {}
    if ru > now and rb and rb != username:
        return False, "Held by another user", {}
    L["reserved_by"] = username
    L["reserved_until"] = now + ttl_sec
    mp["listings"] = mp.get("listings", [])
    save_marketplace(mp)
    return True, "", L


def fulfill(listing_id: str, buyer: str, handle_expected: str, user_exists) -> Tuple[bool, str]:
    mp, L = get_listing(listing_id)
    if not L or L.get("status") != "available":
        return False, "Invalid listing"
    if L.get("reserved_by") and L.get("reserved_by") != buyer:
        return False, "Reservation mismatch"
    h = L["handle"]
    if h.lower() != handle_expected.strip().lower():
        return False, "Handle mismatch"
    if user_exists(h):
        return False, "Username already claimed"
    L["status"] = "sold"
    L["sold_to"] = buyer
    L["sold_at"] = time.time()
    L["reserved_by"] = ""
    L["reserved_until"] = 0.0
    save_marketplace(mp)
    return True, ""


def release_stale(max_age: float = 1200.0) -> None:
    mp = load_marketplace()
    now = time.time()
    changed = False
    for L in mp.get("listings", []):
        if L.get("status") != "available":
            continue
        ru = float(L.get("reserved_until") or 0)
        if ru and ru < now:
            if (now - ru) > max_age:
                L["reserved_by"] = ""
                L["reserved_until"] = 0.0
                changed = True
    if changed:
        save_marketplace(mp)
