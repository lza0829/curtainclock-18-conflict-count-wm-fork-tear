from __future__ import annotations
from app.services import conflict_panel

from fastapi import APIRouter

from app.db import connect
from app.services import conflict_store

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard():
    conn = connect()
    try:
        day_row = conn.execute(
            "SELECT value FROM settings WHERE key='fixture_day'"
        ).fetchone()
        day = day_row["value"] if day_row else None
        # 臂 A 口径：全部计数由事实表/事件表现算，与各列表页同源，杜绝双写漂移
        open_leases = conn.execute(
            "SELECT COUNT(*) c FROM leases WHERE status='open'"
        ).fetchone()["c"]
        # 与 /conflicts、按批窗过滤共用同一读函数
        conflict_n = conflict_store.count_conflicts(conn)
        live_n = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='live'"
        ).fetchone()["c"]
        draft_n = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='draft'"
        ).fetchone()["c"]
        base = {
            "fixture_day": day,
            "open_leases": int(open_leases),
            "conflict_count": conflict_n,
            "live_sessions": live_n,
            "draft_sessions": draft_n,
        }
        return conflict_panel.overlay_dashboard(conn, base)
    finally:
        conn.close()
