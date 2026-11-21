"""冲突事件与审计的唯一读写口径（全局唯一架构：冲突事件只追加）。

所有表面——冲突页、总览冲突计数、水位核对、按批窗过滤、按资源角标、
租约开窗 409 回执、审计——都必须经本模块读写 conflicts / audit_events，
禁止各路由自行拼 SQL，禁止维护第二套可写的物化计数，禁止只刷前端徽章。

正确性口径：
- 每次开窗失败，按失败原因逐条 INSERT 新事件行；同一批窗连败 N 次即新增 N 组行；
- 冲突计数 = conflicts 表 COUNT(*)，与列表同一 WHERE、同一连接、同一事务；
- 列表按 created_at DESC, id DESC（id 为同秒事件的稳定次序），不截断；
- 旧 watermarks.conflict_count 不再作为计数来源；若库中仍存在该行，读取时按
  conflicts 表重算，重算不符则回写为表值并标记 stale（GET 不直接信任水位）；
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
    """插入一条冲突事件，返回新行 id。只追加，永不更新/覆盖旧行。

    注意：本函数只写 conflicts 表，不再顺带维护任何计数缓存/徽章。
    计数一律在读取时由本表 COUNT(*) 重算，避免失败路径双写。
    """
    cur = conn.execute(
        "INSERT INTO conflicts(kind, window_code, resource_code, day, start_min, end_min, reason, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (kind, window_code, resource_code, day, start_min, end_min, reason, now_ts()),
    )
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


# ---- 读侧：以下函数是所有表面的唯一计数/列表来源 ----

def _filter_sql(
    window_code: str | None = None, resource_code: str | None = None
) -> tuple[str, tuple]:
    """冲突列表与计数共用的唯一 WHERE 构造器——同源不能靠两处手抄条件。"""
    clauses: list[str] = []
    args: list = []
    if window_code:
        clauses.append("window_code=?")
        args.append(window_code)
    if resource_code:
        clauses.append("resource_code=?")
        args.append(resource_code)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, tuple(args)


def count_conflicts(
    conn: sqlite3.Connection,
    window_code: str | None = None,
    resource_code: str | None = None,
) -> int:
    """冲突计数 = conflicts 事件行数；可按批窗/资源过滤。

    与 list_conflicts 同源同条件：同一个 WHERE，没有第二套物化值。
    """
    where, args = _filter_sql(window_code, resource_code)
    row = conn.execute(f"SELECT COUNT(*) c FROM conflicts{where}", args).fetchone()
    return int(row["c"])


def list_conflicts(
    conn: sqlite3.Connection,
    window_code: str | None = None,
    resource_code: str | None = None,
) -> list[dict]:
    """冲突列表：时间倒序（同秒按 id 倒序），不做 LIMIT 截断，保证与计数对得上。"""
    where, args = _filter_sql(window_code, resource_code)
    rows = conn.execute(
        f"SELECT * FROM conflicts{where} ORDER BY created_at DESC, id DESC", args
    ).fetchall()
    return [dict(r) for r in rows]


def list_conflicts_by_ids(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    """按 id 取冲突行（回执用）：只读同表，回执里的 id 必须能在本函数查到。"""
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM conflicts WHERE id IN ({placeholders}) ORDER BY id", tuple(ids)
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


def _sync_watermark(conn: sqlite3.Connection) -> None:
    """把水位写为 conflicts 表当前计数（同事务内）。

    水位是「表可重放集合」的派生投影，不是独立计数源：只能由表重算写入
    （校验时若不符则回写并报 healed/stale），读取对外计数永远以 COUNT(*) 为准。
    """
    actual = count_conflicts(conn)
    conn.execute(
        "INSERT INTO watermarks(key, value, updated_at) VALUES('conflict_count', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (actual, now_ts()),
    )


def count_conflicts_by_resource(conn: sqlite3.Connection) -> dict[str, int]:
    """资源维度的冲突角标：直接按 conflicts.resource_code 分组，不另存计数。

    资源页、冲突页筛选与总览共用同一张事件表，角标不会先绿而表仍撒谎。
    """
    rows = conn.execute(
        "SELECT resource_code, COUNT(*) c FROM conflicts GROUP BY resource_code"
    ).fetchall()
    return {str(r["resource_code"]): int(r["c"]) for r in rows}


def watermark_state(conn: sqlite3.Connection) -> dict:
    """水位核对（只读）：水位只能由 conflicts 表重算，不能反过来决定计数。

    返回的 actual 永远来自事件表；若旧 watermarks.conflict_count 行仍存在，
    其值与 actual 不符即视为 stale——冲突计数仍以 actual 对外，水位不得覆盖它。
    """
    actual = count_conflicts(conn)
    row = conn.execute(
        "SELECT value, updated_at FROM watermarks WHERE key='conflict_count'"
    ).fetchone()
    if row is None:
        return {
            "present": False,
            "stored": None,
            "actual": actual,
            "stale": False,
            "updated_at": None,
        }
    stored = int(row["value"])
    return {
        "present": True,
        "stored": stored,
        "actual": actual,
        "stale": stored != actual,
        "updated_at": row["updated_at"],
    }


def reconcile_watermark(conn: sqlite3.Connection) -> dict:
    """按 conflicts 表重算水位；仅在旧水位行存在时回写，并保留撕裂证据。

    必须在写事务中调用（dashboard/水位接口走 transaction()）。
    - 水位缺失：架构下视为已废弃（启动迁移已清除），不重建假计数源；
    - 水位存在且与表不符：回写为表值，本次返回 stale=True + healed_from 原值，
      表示「调用前存在撕裂」，供总览/水位接口显式标注，而不是刷绿了事。
    对外计数永远以 conflicts 表 COUNT(*) 为准。
    """
    before = watermark_state(conn)
    if before["present"] and before["stale"]:
        _sync_watermark(conn)
        after = watermark_state(conn)
        after["stale"] = True          # 本次读取检测到的撕裂，回写后仍如实标注
        after["healed"] = True
        after["healed_from"] = before["stored"]
        return after
    return before
