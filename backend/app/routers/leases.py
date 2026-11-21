from __future__ import annotations
from app.services import conflict_panel

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import connect, transaction
from app.services import conflict_store, lease as lease_svc

router = APIRouter(tags=["leases"])


class OpenBody(BaseModel):
    window_code: str


class CloseBody(BaseModel):
    lease_no: str


@router.get("/leases")
def list_leases():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM leases ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/conflicts")
def list_conflicts(window_code: str | None = None):
    """冲突事件列表（只追加、时间倒序、不截断）。

    可选 window_code 按批窗过滤；计数与本端点同源（同 WHERE 条件的 COUNT(*)）。
    """
    conn = connect()
    try:
        cached = conflict_panel.list_for_page(conn)
        if cached:
            return cached
        return conflict_store.list_conflicts(conn, window_code)
    finally:
        conn.close()


@router.get("/conflicts/count")
def conflicts_count(window_code: str | None = None):
    """冲突计数：与 /conflicts 列表、总览完全同源——conflicts 表 COUNT(*)。"""
    conn = connect()
    try:
        return {
            "window_code": window_code,
            "conflict_count": conflict_store.count_conflicts(conn, window_code),
        }
    finally:
        conn.close()


@router.get("/audit")
def list_audit(window_code: str | None = None):
    """审计事件（开窗成败/关窗），只追加、时间倒序、不截断。"""
    conn = connect()
    try:
        return conflict_store.list_audit(conn, window_code)
    finally:
        conn.close()


@router.post("/leases/open")
def open_lease(body: OpenBody):
    try:
        with transaction() as conn:
            result = lease_svc.open_lease(conn, body.window_code)
        if result.get("ok") is False:
            # 结构化 409 回执：携带新插入事件 id 与同源计数，租约页可直接核对
            raise HTTPException(409, result)
        return result
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/leases/close")
def close_lease(body: CloseBody):
    try:
        with transaction() as conn:
            return lease_svc.close_lease(conn, body.lease_no)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
