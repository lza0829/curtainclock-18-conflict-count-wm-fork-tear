from __future__ import annotations

from fastapi import APIRouter

from app.db import connect
from app.services import conflict_store

router = APIRouter(tags=["resources"])


@router.get("/slots")
def list_slots():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT code, name, capacity, active FROM slots ORDER BY code"
        ).fetchall()
        # 冲突角标与冲突页/总览同源：直接由 conflicts 表按 resource_code 现算
        badges = conflict_store.count_conflicts_by_resource(conn)
        return [{**dict(r), "conflict_count": badges.get(r["code"], 0)} for r in rows]
    finally:
        conn.close()


@router.get("/executors")
def list_executors():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT code, name, subject, active FROM executors ORDER BY code"
        ).fetchall()
        badges = conflict_store.count_conflicts_by_resource(conn)
        return [{**dict(r), "conflict_count": badges.get(r["code"], 0)} for r in rows]
    finally:
        conn.close()
