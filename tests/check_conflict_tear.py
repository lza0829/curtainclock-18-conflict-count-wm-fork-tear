# -*- coding: utf-8 -*-
"""冲突宣告与计数证据撕裂的四个夹具（全局唯一架构：事件只追加）。

题面四类撕裂：
A. 回执有因、页面无行 —— 开窗失败回执写了原因，冲突页却 0 行；
B. 表有行、水位未加 —— conflicts 有行，旧水位值偏低/缺失、总览仍报绿；
C. 覆盖后三分叉 —— 旧版覆盖式计数（水位/面板徽章）被改写后，页面、表、水位各一套；
D. 并发双计/漏计 —— 并发开窗冲突双写导致多算或少算。

架构口径：冲突计数只认 conflicts 表 COUNT(*)；水位只能由表重算，重算不符则
回写并标 stale；页面、计数端点、总览、资源角标、409 回执全部同源。
本脚本用独立临时库，不碰开发库。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_tmp = tempfile.mkdtemp(prefix="curtainclock-tear-")
DB_FILE = Path(_tmp) / "tear.db"
os.environ["CURTAINCLOCK_DB"] = str(DB_FILE)

from app.db import connect, init_db, transaction  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402
from app.services import conflict_store, lease as lease_svc  # noqa: E402

DAY = "2026-11-20"
PORT = "11198"
BASE = f"http://127.0.0.1:{PORT}/api"
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def make_window(code: str, slot: str = "S-ETL-01", executor: str = "E-PIPE-02",
                start: int = 1500, end: int = 1560) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO windows(code,title,subject,slot_code,executor_code,day,start_min,end_min,status) "
            "VALUES(?,?,?,?,?,?,?,?,'draft')",
            (code, f"试窗{code}", "ETL", slot, executor, DAY, start, end),
        )


def raw_count(table: str, where: str = "", params: tuple = ()) -> int:
    conn = connect()
    try:
        return conn.execute(f"SELECT COUNT(*) c FROM {table} {where}", params).fetchone()["c"]
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def http(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw}
        return e.code, payload


# ---------- 夹具 A：回执有因、页面无行 ----------
def fixture_receipt_without_row() -> None:
    print("== 夹具 A：回执有因 → 冲突页必须有行 ==")
    make_window("W-TEAR-A")
    make_window("W-TEAR-A-LOCK", executor="E-PIPE-01")
    with transaction() as conn:
        locked = lease_svc.open_lease(conn, "W-TEAR-A-LOCK")
    check("A 锁窗成功", locked["ok"] is True)

    with transaction() as conn:
        receipt = lease_svc.open_lease(conn, "W-TEAR-A")
    check("A 回执判失败且带原因", receipt["ok"] is False and receipt.get("error"))
    ids = receipt.get("conflict_ids") or []
    check("A 回执带新事件 id", bool(ids), str(ids))

    # 页面同源读取：回执里每个事件 id 必须出现在冲突页列表中
    conn = connect()
    try:
        page = conflict_store.list_conflicts(conn, "W-TEAR-A")
        cnt = conflict_store.count_conflicts(conn, "W-TEAR-A")
    finally:
        conn.close()
    page_ids = {r["id"] for r in page}
    check("A 回执事件 id 全部在冲突页有行", set(ids) <= page_ids, f"回执 {ids} 页 {sorted(page_ids)}")
    check("A 页面行数 == 同条件计数 == 回执该窗计数",
          len(page) == cnt == receipt["window_conflict_count"])
    check("A 回执原因与页面行原因一致",
          any(r["reason"] == receipt["error"] for r in page),
          f"{receipt['error']} vs {[r['reason'] for r in page]}")
    check("A 回执自带的 conflict_rows 与冲突页同 id 同行数",
          [r["id"] for r in receipt.get("conflict_rows", [])] == sorted(ids)
          and len(receipt.get("conflict_rows", [])) == len(ids),
          str([r.get("id") for r in receipt.get("conflict_rows", [])]))

    # 负向证据：直接写一个无行的假回执场景不存在——失败路径不允许只返回错误
    check("A 失败路径不会零行回执",
          raw_count("conflicts", "WHERE window_code=?", ("W-TEAR-A",)) > 0)

    with transaction() as conn:
        lease_svc.close_lease(conn, locked["lease_no"])


# ---------- 夹具 B：表有行、水位未加 ----------
def fixture_rows_without_watermark() -> None:
    print("== 夹具 B：表有行、水位未加 → 计数仍以表为准 ==")
    # 直接向事件表追加两行，模拟“冲突表有行”；水位故意留旧值（偏低）
    before = raw_count("conflicts")
    with transaction() as conn:
        conflict_store.insert_conflict(
            conn, kind="room", window_code="W-TEAR-B", resource_code="S-ETL-01",
            day=DAY, start_min=1500, end_min=1560, reason="算力槽已被占用（夹具 B）",
        )
        conflict_store.insert_conflict(
            conn, kind="teacher", window_code="W-TEAR-B", resource_code="E-PIPE-02",
            day=DAY, start_min=1500, end_min=1560, reason="执行器已被占用（夹具 B）",
        )
        conn.execute(
            "INSERT INTO watermarks(key,value,updated_at) VALUES('conflict_count',?, 'old') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (before,),  # 水位停在加行之前
        )
    check("B 表已 +2 行", raw_count("conflicts") == before + 2)

    conn = connect()
    try:
        read_count = conflict_store.count_conflicts(conn)
        state = conflict_store.watermark_state(conn)
    finally:
        conn.close()
    check("B 计数由表重算，不受低水位影响", read_count == before + 2, f"{read_count}")
    check("B 水位被识别为 stale", state["present"] and state["stale"]
          and state["actual"] == before + 2, str(state))

    # GET /watermark 重算回写；总览不得因旧水位而报绿
    st, healed = http("GET", "/watermark")
    check("B /watermark 200", st == 200)
    check("B 回写后水位 == 表计数", healed["stored"] == healed["actual"] == before + 2,
          str(healed))
    st, again = http("GET", "/watermark")
    check("B 再读不再 stale", again["stale"] is False, str(again))

    st, dash = http("GET", "/dashboard")
    check("B 总览计数 == 表计数", dash["conflict_count"] == before + 2, str(dash["conflict_count"]))
    st, rows = http("GET", "/conflicts")
    st, cnt = http("GET", "/conflicts/count")
    check("B 总览 == 列表行数 == count", dash["conflict_count"] == len(rows) == cnt["conflict_count"])
    check("B 总览水位一致标志为真", dash["watermarks_consistent"] is True, str(dash["watermark"]))


# ---------- 夹具 C：覆盖后三分叉 ----------
def fixture_overwrite_fork() -> None:
    print("== 夹具 C：旧覆盖式计数被改写 → 页面/表/水位不得各说各话 ==")
    table_n = raw_count("conflicts")
    # 旧版遗留：水位与面板徽章各写一个互相冲突的值（覆盖式双写残留）
    with transaction() as conn:
        conn.execute(
            "INSERT INTO watermarks(key,value,updated_at) VALUES('conflict_count',?, 'legacy') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (table_n + 7,),
        )
    set_setting("conflict_panel_badge", str(table_n + 3))
    set_setting("conflict_panel_cache", json.dumps({"items": [], "count": table_n + 3}))

    # 启动迁移清除残留的面板缓存键（模拟服务重启）
    init_db()
    conn = connect()
    try:
        cache_left = conn.execute(
            "SELECT COUNT(*) c FROM settings "
            "WHERE key IN ('conflict_panel_cache','conflict_panel_badge')"
        ).fetchone()["c"]
        read_n = conflict_store.count_conflicts(conn)
        rows_n = len(conflict_store.list_conflicts(conn))
    finally:
        conn.close()
    check("C 旧面板缓存键已清除", cache_left == 0)
    check("C 计数仍等于表行数（不被 +3/+7 覆盖）", read_n == table_n == rows_n,
          f"{read_n}/{table_n}/{rows_n}")

    # 水位三方分叉：总览重算回写；页面减一问题——成功开窗/关窗不得减少历史冲突行
    st, before_dash = http("GET", "/dashboard")
    check("C 总览重算后计数与表一致", before_dash["conflict_count"] == table_n,
          str(before_dash["conflict_count"]))
    wm0 = before_dash["watermark"]
    check("C 水位只作核对：缺失即 present=False 且 actual==表计数，存在则 stored==表计数",
          (not wm0["present"] and wm0["stored"] is None) or wm0["stored"] == table_n,
          str(wm0))
    check("C 水位 actual 永远等于表计数", wm0["actual"] == before_dash["conflict_count"], str(wm0))
    make_window("W-TEAR-C")
    make_window("W-TEAR-C-LOCK", executor="E-PIPE-01")
    st, lock = http("POST", "/leases/open", {"window_code": "W-TEAR-C-LOCK"})
    check("C 占窗成功", st == 200, str(lock))
    st, fail = http("POST", "/leases/open", {"window_code": "W-TEAR-C"})
    check("C 冲突 409", st == 409)
    after_fail = raw_count("conflicts")
    st, close = http("POST", "/leases/close", {"lease_no": lock["lease_no"]})
    check("C 关窗成功", st == 200, str(close))
    st, reopened = http("POST", "/leases/open", {"window_code": "W-TEAR-C"})
    check("C 释放后可成功开窗", st == 200, str(reopened))
    check("C 成功开窗/关窗后冲突行一行未减", raw_count("conflicts") == after_fail == table_n + 1,
          f"{table_n} -> {after_fail} -> {raw_count('conflicts')}")
    st, after_dash = http("GET", "/dashboard")
    st, after_rows = http("GET", "/conflicts")
    st, after_cnt = http("GET", "/conflicts/count")
    check("C 三分叉收口：总览 == 页面 == 计数 == 表",
          after_dash["conflict_count"] == len(after_rows) == after_cnt["conflict_count"] == table_n + 1,
          f"{after_dash['conflict_count']}/{len(after_rows)}/{after_cnt['conflict_count']}/{table_n + 1}")
    wm_after = after_dash["watermark"]
    check("C 三分叉收口：水位缺失或与表一致，actual 恒为表计数",
          (not wm_after["present"] and wm_after["stored"] is None)
          or wm_after["stored"] == table_n + 1,
          str(wm_after))
    check("C 三分叉收口：水位 actual == 表计数", wm_after["actual"] == table_n + 1, str(wm_after))


# ---------- 夹具 D：并发双计/漏计 ----------
def fixture_concurrent_double() -> None:
    print("== 夹具 D：并发双写不得双计或漏计 ==")
    make_window("W-TEAR-D1", start=1600, end=1660)
    make_window("W-TEAR-D2", start=1600, end=1660)
    make_window("W-TEAR-D-LOCK", executor="E-PIPE-01", start=1600, end=1660)
    with transaction() as conn:
        lock = lease_svc.open_lease(conn, "W-TEAR-D-LOCK")
    check("D 锁窗成功", lock["ok"] is True)
    base = raw_count("conflicts")

    receipts: list[dict] = []
    errors: list[Exception] = []

    def worker(code: str) -> None:
        try:
            with transaction() as conn:
                receipts.append(lease_svc.open_lease(conn, code))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(c,)) for c in ("W-TEAR-D1", "W-TEAR-D2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("D 并发无异常", not errors, str(errors))
    check("D 两次均失败", len(receipts) == 2 and all(not r["ok"] for r in receipts))
    ids = sorted(i for r in receipts for i in r.get("conflict_ids", []))
    check("D 事件 id 不重叠（不双计）", len(ids) == len(set(ids)) == 2, str(ids))
    check("D 表行数恰好 +2（不漏计）", raw_count("conflicts") == base + 2,
          f"{base} -> {raw_count('conflicts')}")

    # 并发写不得顺带把任何第二计数源写歪：不存在面板设置键
    conn = connect()
    try:
        panel_keys = conn.execute(
            "SELECT COUNT(*) c FROM settings "
            "WHERE key IN ('conflict_panel_cache','conflict_panel_badge')"
        ).fetchone()["c"]
    finally:
        conn.close()
    check("D 并发后无第二计数源残留", panel_keys == 0)

    st, dash = http("GET", "/dashboard")
    st, rows = http("GET", "/conflicts")
    st, cnt = http("GET", "/conflicts/count")
    check("D 并发后总览 == 页面 == 计数 == 表",
          dash["conflict_count"] == len(rows) == cnt["conflict_count"] == base + 2,
          f"{dash['conflict_count']}/{len(rows)}/{cnt['conflict_count']}/{base + 2}")

    # 资源角标同源：按资源分组之和 == 全局计数
    st, badges = http("GET", "/conflicts/by-resource")
    check("D 资源角标 200 且分组之和 == 全局计数",
          st == 200 and sum(badges.values()) == base + 2, str(badges))
    st, slots = http("GET", "/slots")
    slot_badges = {r["code"]: r["conflict_count"] for r in slots}
    check("D 资源页角标与 by-resource 同源",
          slot_badges.get("S-ETL-01", 0) == badges.get("S-ETL-01", 0),
          f"{slot_badges.get('S-ETL-01')} vs {badges.get('S-ETL-01')}")
    st, filtered = http("GET", "/conflicts?resource_code=S-ETL-01")
    st, filtered_cnt = http("GET", "/conflicts/count?resource_code=S-ETL-01")
    check("D 按资源筛选：列表行数 == 计数 == 角标",
          len(filtered) == filtered_cnt["conflict_count"] == badges.get("S-ETL-01", -1),
          f"{len(filtered)}/{filtered_cnt['conflict_count']}/{badges.get('S-ETL-01')}")

    with transaction() as conn:
        lease_svc.close_lease(conn, lock["lease_no"])


def wait_ready(proc: subprocess.Popen) -> None:
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/dashboard", timeout=2).read()
            return
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("测试服务器未就绪")


if __name__ == "__main__":
    seed_if_empty()

    env = {**os.environ, "CURTAINCLOCK_DB": str(DB_FILE), "PYTHONPATH": str(ROOT / "backend")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", PORT],
        cwd=str(ROOT / "backend"), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        wait_ready(proc)
        fixture_receipt_without_row()
        fixture_rows_without_watermark()
        fixture_overwrite_fork()
        fixture_concurrent_double()

        # HTTP 侧汇总：四夹具跑完后四条链必须仍对齐
        st, dash = http("GET", "/dashboard")
        st, rows = http("GET", "/conflicts")
        st, cnt = http("GET", "/conflicts/count")
        st, wm = http("GET", "/watermark")
        wm_matches = (
            wm["actual"] == dash["conflict_count"]
            and (not wm["present"] or wm["stored"] == wm["actual"])
        )
        check("HTTP 汇总：总览 == 页面 == 计数 == 水位（actual/存在时 stored）",
              dash["conflict_count"] == len(rows) == cnt["conflict_count"] and wm_matches,
              f"{dash['conflict_count']}/{len(rows)}/{cnt['conflict_count']}/{wm}")
        check("HTTP 汇总：总览自洽标志为真", dash["watermarks_consistent"] is True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if failures:
        print(f"FAILED {len(failures)} 项: {failures}")
        sys.exit(1)
    print("ALL TEAR FIXTURES PASSED")
