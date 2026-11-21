from __future__ import annotations

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
def list_conflicts(window_code: str | None = None, resource_code: str | None = None):
    """冲突事件列表（只追加、时间倒序、不截断）。

    可选 window_code / resource_code 过滤；不存在任何旁路缓存，列表永远直读
    conflicts 表，与 /conflicts/count、总览为同一 WHERE 同一表。
    """
    conn = connect()
    try:
        return conflict_store.list_conflicts(conn, window_code, resource_code)
    finally:
        conn.close()


@router.get("/conflicts/count")
def conflicts_count(window_code: str | None = None, resource_code: str | None = None):
    """冲突计数：与 /conflicts 列表、总览完全同源——conflicts 表 COUNT(*)."""
    conn = connect()
    try:
        return {
            "window_code": window_code,
            "resource_code": resource_code,
            "conflict_count": conflict_store.count_conflicts(
                conn, window_code, resource_code
            ),
        }
    finally:
        conn.close()


@router.get("/conflicts/by-resource")
def conflicts_by_resource():
    """资源维度冲突角标：按 conflicts.resource_code 分组现算，不读任何缓存。"""
    conn = connect()
    try:
        return conflict_store.count_conflicts_by_resource(conn)
    finally:
        conn.close()


@router.get("/watermark")
def conflict_watermark():
    """水位核对：按 conflicts 表重算；不符即回写并标 stale（GET 不信任旧水位）。"""
    with transaction() as conn:
        return conflict_store.reconcile_watermark(conn)


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
            # 结构化 409 回执：携带新插入事件 id、同事务同源计数与冲突页同源行，
            # 租约页可直接核对「回执有因，冲突页必有行」。
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
