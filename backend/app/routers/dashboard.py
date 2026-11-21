from __future__ import annotations

from fastapi import APIRouter

from app.db import connect, transaction
from app.services import conflict_store

router = APIRouter(tags=["dashboard"])


def _snapshot(conn):
    """在单一连接/单一快照内读取总览所需的全部数字，保证彼此可比。"""
    day_row = conn.execute(
        "SELECT value FROM settings WHERE key='fixture_day'"
    ).fetchone()
    conflict_n = conflict_store.count_conflicts(conn)
    conflict_rows = conflict_store.list_conflicts(conn)
    return {
        "fixture_day": day_row["value"] if day_row else None,
        "open_leases": int(
            conn.execute(
                "SELECT COUNT(*) c FROM leases WHERE status='open'"
            ).fetchone()["c"]
        ),
        "conflict_count": conflict_n,
        "conflict_rows_visible": len(conflict_rows),
        "live_sessions": int(
            conn.execute(
                "SELECT COUNT(*) c FROM windows WHERE status='live'"
            ).fetchone()["c"]
        ),
        "draft_sessions": int(
            conn.execute(
                "SELECT COUNT(*) c FROM windows WHERE status='draft'"
            ).fetchone()["c"]
        ),
        "watermark": conflict_store.watermark_state(conn),
    }


@router.get("/dashboard")
def dashboard():
    """总览：所有计数由事实表/事件表现算，与各列表页同一读函数、同一口径。

    冲突计数只认 conflicts 表；旧水位若存在则按表重算回写，重算不符时把
    stale 标记带出——绝不允许水位/徽章缓存覆盖表计数，也绝不谎报绿色一致。
    常态读取仅用只读连接；只有检测到水位撕裂时才开写事务回写并重读快照。
    """
    conn = connect()
    try:
        snap = _snapshot(conn)
    finally:
        conn.close()

    wm = snap["watermark"]
    if wm["present"] and wm["stale"]:
        # 撕裂只在水位行残留时出现：短写事务内按表回写，并在同一事务重读快照
        with transaction() as wconn:
            snap["watermark"] = conflict_store.reconcile_watermark(wconn)
            fresh = _snapshot(wconn)
            fresh["watermark"] = snap["watermark"]
            snap = fresh

    wm = snap["watermark"]
    # 自洽核对：计数必须等于列表行数，且水位（若存在）回写后须与表一致
    consistent = (
        snap["conflict_count"] == snap["conflict_rows_visible"]
        and not wm["stale"]
    )
    return {
        "fixture_day": snap["fixture_day"],
        "open_leases": snap["open_leases"],
        "conflict_count": snap["conflict_count"],
        "conflict_count_actual": snap["conflict_count"],
        "conflict_rows_visible": snap["conflict_rows_visible"],
        "live_sessions": snap["live_sessions"],
        "draft_sessions": snap["draft_sessions"],
        "watermark": wm,
        "watermarks_consistent": consistent,
    }
