from __future__ import annotations

import sqlite3

from app.services import conflict_store
from app.services.ready import readiness, resource_busy


def _conflict_mode(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='conflict_mode'"
    ).fetchone()
    return row["value"] if row else "reject"


def _open_lease_count(conn: sqlite3.Connection) -> int:
    # 在途租约数同样由租约表现算，不再维护读-改-写水位
    return conn.execute(
        "SELECT COUNT(*) c FROM leases WHERE status='open'"
    ).fetchone()["c"]


def _record_conflicts(conn: sqlite3.Connection, session: sqlite3.Row, reasons: list[str]) -> list[int]:
    """每个失败原因只追加一条冲突事件，返回新事件 id 列表。"""
    ids: list[int] = []
    for reason in reasons:
        kind = "room" if "算力槽" in reason else ("teacher" if "执行器" in reason else "ready")
        resource = (
            session["slot_code"]
            if kind == "room"
            else (session["executor_code"] if kind == "teacher" else session["code"])
        )
        ids.append(
            conflict_store.insert_conflict(
                conn,
                kind=kind,
                window_code=session["code"],
                resource_code=resource,
                day=session["day"],
                start_min=session["start_min"],
                end_min=session["end_min"],
                reason=reason,
            )
        )
    return ids


def _failure_receipt(conn: sqlite3.Connection, session: sqlite3.Row, reasons: list[str]) -> dict:
    """统一的开窗失败回执：事件只追加 + 审计 + 与冲突页同源的计数。"""
    mode = _conflict_mode(conn)
    conflict_ids = _record_conflicts(conn, session, reasons)
    conflict_store.insert_audit(
        conn,
        action="open",
        window_code=session["code"],
        result="conflict",
        detail="；".join(reasons),
    )
    return {
        "ok": False,
        "error": "；".join(reasons),
        "conflict_mode": mode,
        "window_code": session["code"],
        "conflict_ids": conflict_ids,
        # 计数与列表/总览同源：都是 conflicts 表 COUNT(*)，同一事务内读取
        "conflict_count": conflict_store.count_conflicts(conn),
        "window_conflict_count": conflict_store.count_conflicts(conn, session["code"]),
        # 回执原因必须能在冲突页查到：直接回带刚插入的同一批行
        "conflict_rows": conflict_store.list_conflicts_by_ids(conn, conflict_ids),
    }


def open_lease(conn: sqlite3.Connection, window_code: str) -> dict:
    session = conn.execute(
        "SELECT * FROM windows WHERE code=?", (window_code,)
    ).fetchone()
    if session is None:
        raise ValueError(f"批窗不存在：{window_code}")
    if session["status"] == "live":
        raise ValueError("批窗已在占用中，勿重复开窗")
    if session["status"] == "done":
        raise ValueError("批窗已结束")

    ready = readiness(conn, session)
    if not ready["ready"]:
        # 底座贯彻 reject：冲突事件落库后由调用方提交事务
        return _failure_receipt(conn, session, ready["reasons"])

    # 就绪检查与插入之间的竞态兜底：同样按失败落事件，绝不静默丢失败
    race_reasons: list[str] = []
    if resource_busy(
        conn,
        kind="room",
        resource_code=session["slot_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
    ):
        race_reasons.append("算力槽刚被占用，开窗失败")
    if resource_busy(
        conn,
        kind="teacher",
        resource_code=session["executor_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
    ):
        race_reasons.append("执行器刚被占用，开窗失败")
    if race_reasons:
        return _failure_receipt(conn, session, race_reasons)

    n = conn.execute("SELECT COUNT(*) c FROM leases").fetchone()["c"] + 1
    lease_no = f"L-{session['day'].replace('-', '')}-{n:04d}"
    conn.execute(
        "INSERT INTO leases(lease_no, window_code, slot_code, executor_code, day, start_min, end_min, status, opened_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            lease_no,
            window_code,
            session["slot_code"],
            session["executor_code"],
            session["day"],
            session["start_min"],
            session["end_min"],
            "open",
            conflict_store.now_ts(),
        ),
    )
    conn.execute(
        "UPDATE windows SET status='live' WHERE code=?", (window_code,)
    )
    conflict_store.insert_audit(
        conn,
        action="open",
        window_code=window_code,
        lease_no=lease_no,
        result="success",
        detail=f"开窗成功，租约 {lease_no}",
    )
    return {
        "ok": True,
        "lease_no": lease_no,
        "window_code": window_code,
        "status": "open",
        "open_leases": _open_lease_count(conn),
    }


def close_lease(conn: sqlite3.Connection, lease_no: str) -> dict:
    lease = conn.execute(
        "SELECT * FROM leases WHERE lease_no=?", (lease_no,)
    ).fetchone()
    if lease is None:
        raise ValueError(f"租约不存在：{lease_no}")
    if lease["status"] != "open":
        raise ValueError("租约不在占用态")
    conn.execute(
        "UPDATE leases SET status='closed', closed_at=? WHERE lease_no=?",
        (conflict_store.now_ts(), lease_no),
    )
    conn.execute(
        "UPDATE windows SET status='done' WHERE code=?",
        (lease["window_code"],),
    )
    conflict_store.insert_audit(
        conn,
        action="close",
        window_code=lease["window_code"],
        lease_no=lease_no,
        result="success",
        detail=f"关窗释放 {lease_no}",
    )
    # 成功路径不触碰任何 conflicts 行——历史冲突只追加，禁止物理偷删
    return {
        "lease_no": lease_no,
        "status": "closed",
        "open_leases": _open_lease_count(conn),
    }
