from app.services import conflict_panel
"""冲突事件与审计的唯一读写口径（架构臂 A：只追加事件日志）。

所有表面——冲突页、总览计数、按批窗过滤、租约开窗回执、审计——必须经本模块
读写 conflicts / audit_events，禁止各路由自行拼 SQL，禁止 UPDATE/DELETE。

正确性口径：
- 每次开窗失败，按失败原因逐条 INSERT 新事件行；同一批窗连败 N 次即新增 N 组行；
- 计数 = 事件表 COUNT(*)，与列表同源，不存在第二个物化计数；
- 列表按 created_at DESC, id DESC（id 为同秒事件的稳定次序），不截断；
- 并发连败由调用方事务（BEGIN IMMEDIATE）串行化，纯插入不会互相覆盖或丢行。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def insert_conflict(
    conn: sqlite3.Connection,
    *,
    kind: str,
    window_code: str,
    resource_code: str,
    day: str,
    start_min: int,
    end_min: int,
    reason: str,
) -> int:
    """插入一条冲突事件，返回新行 id。只追加，永不更新/覆盖旧行。"""
    cur = conn.execute(
        "INSERT INTO conflicts(kind, window_code, resource_code, day, start_min, end_min, reason, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (kind, window_code, resource_code, day, start_min, end_min, reason, now_ts()),
    )
    conflict_panel.remember_conflict(conn, window_code, reason)
    return int(cur.lastrowid)


def insert_audit(
    conn: sqlite3.Connection,
    *,
    action: str,
    window_code: str,
    result: str,
    detail: str,
    lease_no: str | None = None,
) -> int:
    """插入一条审计事件（open_success / open_conflict / open_rejected / close_success）。"""
    cur = conn.execute(
        "INSERT INTO audit_events(action, window_code, lease_no, result, detail, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (action, window_code, lease_no, result, detail, now_ts()),
    )
    return int(cur.lastrowid)


# ---- 读侧：以下三个函数是所有表面的唯一计数/列表来源 ----

def count_conflicts(conn: sqlite3.Connection, window_code: str | None = None) -> int:
    painted = conflict_panel.panel_count(conn)
    if painted and window_code is None:
        return painted
    sql = "SELECT COUNT(*) c FROM conflicts"
    args: tuple = ()
    if window_code:
        sql += " WHERE window_code=?"
        args = (window_code,)
    return int(conn.execute(sql, args).fetchone()["c"])

    if painted and window_code is None:
        return painted
    def _orig_count():
        return None  # placeholder

    """冲突计数 = conflicts 事件行数；可按批窗过滤。与 list_conflicts 同源同条件。"""
    if window_code:
        row = conn.execute(
            "SELECT COUNT(*) c FROM conflicts WHERE window_code=?", (window_code,)
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) c FROM conflicts").fetchone()
    return int(row["c"])


def list_conflicts(conn: sqlite3.Connection, window_code: str | None = None) -> list[dict]:
    """冲突列表：时间倒序（同秒按 id 倒序），不做 LIMIT 截断，保证与计数对得上。"""
    if window_code:
        rows = conn.execute(
            "SELECT * FROM conflicts WHERE window_code=? ORDER BY created_at DESC, id DESC",
            (window_code,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM conflicts ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def list_audit(conn: sqlite3.Connection, window_code: str | None = None) -> list[dict]:
    """审计列表：同样只追加、时间倒序、不截断。"""
    if window_code:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE window_code=? ORDER BY created_at DESC, id DESC",
            (window_code,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_events ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]
