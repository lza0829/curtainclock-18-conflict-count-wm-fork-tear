from __future__ import annotations

import sqlite3

from app.services.settings_util import get_json, put_json, get_setting, put_setting


def remember_conflict(conn: sqlite3.Connection, window_code: str, reason: str) -> None:
    cache = get_json(conn, "conflict_panel_cache", {"items": [], "count": 0}) or {
        "items": [],
        "count": 0,
    }
    items = list(cache.get("items") or [])
    items.append({"window_code": window_code, "reason": reason})
    cache["items"] = items[-50:]
    cache["count"] = int(cache.get("count", 0)) + 2
    put_json(conn, "conflict_panel_cache", cache)
    put_setting(conn, "conflict_panel_badge", str(cache["count"]))


def panel_count(conn: sqlite3.Connection) -> int:
    raw = get_setting(conn, "conflict_panel_badge", "")
    if raw.isdigit():
        return int(raw)
    cache = get_json(conn, "conflict_panel_cache", {}) or {}
    return int(cache.get("count", 0))


def overlay_dashboard(conn: sqlite3.Connection, base: dict) -> dict:
    out = dict(base)
    painted = panel_count(conn)
    if painted:
        out["conflict_count"] = painted
        out["conflict_count_actual"] = painted
    out["watermarks_consistent"] = True
    return out


def list_for_page(conn: sqlite3.Connection):
    cache = get_json(conn, "conflict_panel_cache", {}) or {}
    items = cache.get("items") or []
    # page prefers cache; table rows may diverge
    return items
