# -*- coding: utf-8 -*-
"""臂 A（冲突事件只追加）核对脚本。

核对项（题面）：
1. 连败三次 → 追加臂下三行（单原因失败，每次失败一条事件，三次三行）；
   另覆盖多原因失败（算力槽+执行器同时冲突，一次两条，仍按事件只追加）；
2. 计数一致：总览计数 == /conflicts 列表行数 == /conflicts/count；
3. 按批窗过滤只返回该窗事件，过滤计数与过滤列表同源；
4. 并发双败不得丢行（两事务各插各的，事件 id 不重叠）；同窗并发连败同样不丢；
5. 开窗成功不得物理偷删历史冲突/审计事件；
6. 租约开窗 409 回执携带新事件 id 与同源计数；
7. 审计只追加：开窗成败/关窗各落一行；
8. 旧库迁移：watermarks 里的 conflict_count/open_leases 被清除且不影响计数。

用独立临时库（CURTAINCLOCK_DB）+ 真实 uvicorn，不碰开发库、不引新依赖。
测试窗统一用 1200–1260 时段，避开种子窗（540–630）。
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

_tmp = tempfile.mkdtemp(prefix="curtainclock-check-")
DB_FILE = Path(_tmp) / "check.db"
os.environ["CURTAINCLOCK_DB"] = str(DB_FILE)

from app.db import connect, init_db, transaction  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402
from app.services import conflict_store, lease as lease_svc  # noqa: E402

DAY = "2026-11-20"
T_START, T_END = 1200, 1260  # 独立时段，避开种子窗
PORT = "11199"
BASE = f"http://127.0.0.1:{PORT}/api"
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def make_window(code: str, slot: str = "S-ETL-01", executor: str = "E-PIPE-02",
                start: int = T_START, end: int = T_END) -> None:
    # 默认执行器取 E-PIPE-02：持有者只占 E-PIPE-01 时，失败仅算力槽单原因
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


# ---------- 服务层核对 ----------
def service_layer() -> None:
    print("== 服务层：连败三追加（单原因，每次一行）==")
    make_window("W-CHK-A")                       # S-ETL-01 + E-PIPE-02
    make_window("W-CHK-HOLDER", executor="E-PIPE-01")  # 同槽不同执行器
    with transaction() as conn:
        holder = lease_svc.open_lease(conn, "W-CHK-HOLDER")
    check("占窗成功", holder["ok"] is True, holder.get("lease_no", ""))

    receipts = []
    for i in range(3):
        with transaction() as conn:
            r = lease_svc.open_lease(conn, "W-CHK-A")
        receipts.append(r)
        check(f"第{i+1}次开窗失败：结构化回执且单事件",
              r["ok"] is False and len(r["conflict_ids"]) == 1, str(r.get("conflict_ids")))

    ids = [rid for r in receipts for rid in r["conflict_ids"]]
    check("连败三次 → 三个不同事件行（追加不覆盖）", len(set(ids)) == 3, f"ids={ids}")
    check("W-CHK-A 事件行数 == 3", raw_count("conflicts", "WHERE window_code=?", ("W-CHK-A",)) == 3)

    print("== 多原因失败：槽+执行器同时冲突，一次两事件，仍只追加 ==")
    make_window("W-CHK-MULTI", executor="E-PIPE-01")  # 与 holder 同槽且同执行器
    with transaction() as conn:
        rm = lease_svc.open_lease(conn, "W-CHK-MULTI")
    check("多原因回执带 2 个事件 id", rm["ok"] is False and len(rm["conflict_ids"]) == 2,
          str(rm.get("conflict_ids")))
    check("多原因窗事件行数 == 2", raw_count("conflicts", "WHERE window_code=?", ("W-CHK-MULTI",)) == 2)

    conn = connect()
    try:
        n_all = conflict_store.count_conflicts(conn)
        n_win = conflict_store.count_conflicts(conn, "W-CHK-A")
        rows_all = conflict_store.list_conflicts(conn)
        rows_win = conflict_store.list_conflicts(conn, "W-CHK-A")
    finally:
        conn.close()
    check("全局计数 == 全局列表行数（同源）", n_all == len(rows_all), f"{n_all} vs {len(rows_all)}")
    check("按批窗过滤计数 == 过滤列表行数 == 3", n_win == len(rows_win) == 3)
    check("列表按 created_at/id 倒序", rows_win[0]["id"] == max(ids))
    check("回执计数与 store 计数一致", receipts[-1]["window_conflict_count"] == 3)
    # 回执内计数在本次事件插入后、同事务内计算；末条回执之后又插入了多原因 2 行，
    # 故末条全局计数 = 当前全局 - 2
    check("回执全局计数为插入后即时快照（同源同函数）",
          receipts[-1]["conflict_count"] == n_all - 2,
          f"{receipts[-1]['conflict_count']} vs {n_all - 2}")

    print("== 审计只追加 ==")
    check(
        "审计含 1 成功开窗 + 4 次冲突回执（3 单 + 1 双原因）",
        raw_count("audit_events", "WHERE action='open' AND result='success'") == 1
        and raw_count("audit_events", "WHERE action='open' AND result='conflict'") == 4,
    )

    print("== 开窗成功不偷删历史 ==")
    before_c = raw_count("conflicts")
    before_a = raw_count("audit_events")
    with transaction() as conn:
        lease_svc.close_lease(conn, holder["lease_no"])
    with transaction() as conn:
        ok = lease_svc.open_lease(conn, "W-CHK-A")
    check("释放后开窗成功", ok["ok"] is True)
    check("成功开窗后冲突事件一行未少（无物理删除）",
          raw_count("conflicts") == before_c, f"{before_c} -> {raw_count('conflicts')}")
    check("成功开窗后审计只增不减（close+open 两行）",
          raw_count("audit_events") == before_a + 2)


# ---------- 并发双败：不得丢行/覆盖 ----------
def concurrency() -> None:
    print("== 并发双败（不同窗，单原因）==")
    # 独立时段 1300–1360，与服务层窗（1200–1260）不重叠
    make_window("W-CHK-C1", start=1300, end=1360)
    make_window("W-CHK-C2", start=1300, end=1360)
    make_window("W-CHK-LOCK", executor="E-PIPE-01", start=1300, end=1360)
    with transaction() as conn:
        lock = lease_svc.open_lease(conn, "W-CHK-LOCK")
    check("锁窗成功", lock["ok"] is True, lock.get("lease_no", "LOCK-FAIL"))
    base = raw_count("conflicts")

    collected: list[dict] = []
    errors: list[Exception] = []

    def worker(code: str) -> None:
        try:
            with transaction() as conn:
                collected.append(lease_svc.open_lease(conn, code))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=worker, args=("W-CHK-C1",))
    t2 = threading.Thread(target=worker, args=("W-CHK-C2",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    check("并发两事务均无异常（无 locked 丢账）", not errors, str(errors))
    check("两次都被判失败", len(collected) == 2 and all(not r["ok"] for r in collected))
    new_ids = sorted(rid for r in collected for rid in r["conflict_ids"])
    check("双败事件 id 不重叠（各自插入不覆盖）", len(new_ids) == len(set(new_ids)) == 2, str(new_ids))
    check("双败后总行数恰好 +2（不丢行）", raw_count("conflicts") == base + 2)

    print("== 同窗并发连败：同窗两事务各落一行 ==")
    base2 = raw_count("conflicts")
    c3: list[dict] = []
    errs2: list[Exception] = []

    def worker_same() -> None:
        try:
            with transaction() as conn:
                c3.append(lease_svc.open_lease(conn, "W-CHK-C1"))
        except Exception as e:  # noqa: BLE001
            errs2.append(e)

    ths = [threading.Thread(target=worker_same) for _ in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    check("同窗并发双败无异常", not errs2, str(errs2))
    check("同窗双败各落一行、不丢行不覆盖", raw_count("conflicts") == base2 + 2,
          f"{base2} -> {raw_count('conflicts')}")
    with transaction() as conn:
        lease_svc.close_lease(conn, lock["lease_no"])


# ---------- HTTP 层：表面同源核对 ----------
def http_layer() -> None:
    print("== HTTP 层：总览/冲突页/过滤/回执同源 ==")
    st, d = http("GET", "/dashboard")
    check("dashboard 200", st == 200)
    st, listed = http("GET", "/conflicts")
    st2, counted = http("GET", "/conflicts/count")
    check(
        "总览冲突计数 == /conflicts 行数 == /conflicts/count",
        d["conflict_count"] == len(listed) == counted["conflict_count"],
        f"{d['conflict_count']} / {len(listed)} / {counted['conflict_count']}",
    )

    st, win_rows = http("GET", "/conflicts?window_code=W-CHK-A")
    st, win_cnt = http("GET", "/conflicts/count?window_code=W-CHK-A")
    check("按批窗过滤：列表行数 == 过滤计数 == 3",
          len(win_rows) == win_cnt["conflict_count"] == 3)
    check("过滤结果只含目标批窗", all(r["window_code"] == "W-CHK-A" for r in win_rows))

    make_window("W-CHK-HTTP", start=1400, end=1460)
    make_window("W-CHK-HTTP-LOCK", executor="E-PIPE-01", start=1400, end=1460)
    st, _ = http("POST", "/leases/open", {"window_code": "W-CHK-HTTP-LOCK"})
    check("HTTP 占窗 200", st == 200)
    st, fail_body = http("POST", "/leases/open", {"window_code": "W-CHK-HTTP"})
    check("HTTP 冲突 409", st == 409)
    body = fail_body.get("detail")
    st, all_rows = http("GET", "/conflicts")
    check(
        "409 回执带 conflict_ids 且计数与列表同源",
        isinstance(body, dict)
        and len(body.get("conflict_ids", [])) == 1
        and body.get("conflict_count") == len(all_rows),
        str(body)[:140],
    )

    st, audit = http("GET", "/audit")
    check("审计端点只追加且含成功/冲突两类结果",
          {r["result"] for r in audit} >= {"success", "conflict"} and len(audit) >= 6)
    st, audit_win = http("GET", "/audit?window_code=W-CHK-A")
    check("审计按批窗过滤", all(r["window_code"] == "W-CHK-A" for r in audit_win) and audit_win)

    st, leases = http("GET", "/leases")
    open_rows = [r for r in leases if r["status"] == "open"]
    st, dash_now = http("GET", "/dashboard")
    check("在途租约同源（leases 表现算）",
          dash_now["open_leases"] == len(open_rows),
          f"{dash_now['open_leases']} vs {len(open_rows)}")


# ---------- 旧库迁移核对 ----------
def migration() -> None:
    print("== 旧水位迁移 ==")
    with transaction() as conn:
        conn.execute("INSERT INTO watermarks(key,value,updated_at) VALUES('conflict_count',999,'x')")
        conn.execute("INSERT INTO watermarks(key,value,updated_at) VALUES('open_leases',999,'x')")
    init_db()
    conn = connect()
    try:
        stale = conn.execute(
            "SELECT COUNT(*) c FROM watermarks WHERE key IN ('conflict_count','open_leases')"
        ).fetchone()["c"]
    finally:
        conn.close()
    check("废弃水位键已清除", stale == 0)
    conn = connect()
    try:
        d_count = conflict_store.count_conflicts(conn)
    finally:
        conn.close()
    check("迁移后计数仍等于事件行数（不受假水位 999 影响）",
          d_count == raw_count("conflicts"))


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
    service_layer()
    concurrency()
    migration()

    env = {**os.environ, "CURTAINCLOCK_DB": str(DB_FILE), "PYTHONPATH": str(ROOT / "backend")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", PORT],
        cwd=str(ROOT / "backend"), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        wait_ready(proc)
        http_layer()
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
    print("ALL CHECKS PASSED")
