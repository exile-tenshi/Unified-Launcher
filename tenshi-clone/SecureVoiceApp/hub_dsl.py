# Phase 1 Hub utility DSL — safe JSON op stream executed against read-only Hub primitives.
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

OpFn = Callable[[Dict[str, Any], List[Any]], Tuple[Any, str]]

_PRMITIVES_DOC = """
Primitives (Phase 1):
  ECHO texts...                    -> joined string
  USER_SERVERS username            -> list of hub ids joined by user
  SERVER_NAME server_id            -> hub display name or null
  SERVER_OWNER server_id         -> owner username or null
  CHANNEL_COUNT server_id        -> len(channels dict)
  IS_MEMBER username server_id   -> bool
"""


def primitives_registry(ctx: Dict[str, Any]) -> Dict[str, OpFn]:
    USER_DB = ctx["USER_DB"]
    SERVERS_DB = ctx["SERVERS_DB"]

    def _echo(_c, args: List[Any]):
        return " ".join(str(a) for a in args), ""

    def _user_servers(_c, args: List[Any]):
        if not args:
            return None, "USER_SERVERS needs username"
        u = str(args[0])
        return list(USER_DB.get(u, {}).get("servers", [])), ""

    def _srv_name(_c, args: List[Any]):
        if not args:
            return None, "SERVER_NAME needs server_id"
        sid = str(args[0])
        s = SERVERS_DB.get(sid)
        return (s.get("name") if s else None), ""

    def _srv_owner(_c, args: List[Any]):
        if not args:
            return None, "SERVER_OWNER needs server_id"
        sid = str(args[0])
        s = SERVERS_DB.get(sid)
        return (s.get("owner") if s else None), ""

    def _ch_count(_c, args: List[Any]):
        if not args:
            return None, "CHANNEL_COUNT needs server_id"
        sid = str(args[0])
        s = SERVERS_DB.get(sid) or {}
        ch = s.get("channels") or {}
        return len(ch), ""

    def _is_member(_c, args: List[Any]):
        if len(args) < 2:
            return None, "IS_MEMBER needs username server_id"
        u, sid = str(args[0]), str(args[1])
        members = (SERVERS_DB.get(sid) or {}).get("members")
        if isinstance(members, dict):
            return u in members, ""
        return u in (members or []), ""

    return {
        "ECHO": _echo,
        "USER_SERVERS": _user_servers,
        "SERVER_NAME": _srv_name,
        "SERVER_OWNER": _srv_owner,
        "CHANNEL_COUNT": _ch_count,
        "IS_MEMBER": _is_member,
    }


def run_script(script_obj: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    script_obj schema:
      { "program": [ {"op": "ECHO", "args": ["hello"] }, ... ], "capture_last": false }
    """
    program = script_obj.get("program")
    if not isinstance(program, list) or len(program) > 32:
        return {"status": "fail", "message": "Invalid program (max 32 ops)"}

    regs = primitives_registry(ctx)
    results: List[Dict[str, Any]] = []
    last_val: Any = None

    for i, step in enumerate(program):
        if not isinstance(step, dict):
            return {"status": "fail", "message": f"Step {i} not an object"}
        op = step.get("op")
        args = step.get("args") or []
        if not isinstance(op, str) or not re.match(r"^[A-Z][A-Z0-9_]{0,31}$", op):
            return {"status": "fail", "message": f"Invalid op at {i}"}
        if op not in regs:
            return {"status": "fail", "message": f"Unknown primitive: {op}"}
        if not isinstance(args, list):
            return {"status": "fail", "message": "args must be a list"}

        trimmed_args: List[Any] = []
        for a in args[:16]:
            if isinstance(a, str) and len(a) > 200:
                trimmed_args.append(a[:200])
            else:
                trimmed_args.append(a)

        val, err = regs[op](ctx, trimmed_args)
        if err:
            return {"status": "fail", "message": err, "step": i}
        last_val = val
        results.append({"op": op, "result": val})

    out: Dict[str, Any] = {"status": "success", "steps": results, "last": last_val}
    if script_obj.get("capture_last"):
        out["value"] = last_val
    return out


def help_text() -> str:
    return _PRMITIVES_DOC.strip()
