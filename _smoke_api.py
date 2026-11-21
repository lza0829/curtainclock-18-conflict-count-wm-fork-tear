# -*- coding: utf-8 -*-
"""0-1 API smoke against running server on 11100.

臂 A 追加核对：一次冲突失败 → 一条新事件行；总览计数 == /conflicts 行数；
409 回执带 conflict_ids；再失败一次 → 列表 +1 且计数同步 +1。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:11100/api"


def call(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
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


def main() -> None:
    st, dash = call("GET", "/dashboard")
    assert st == 200, dash
    print("dashboard", dash)

    st, open1 = call("POST", "/leases/open", {"window_code": "W-DIM-01"})
    assert st == 200, open1
    print("open1", open1)

    st, created = call(
        "POST",
        "/windows",
        {
            "code": "W-DIM-DUP",
            "title": "冲突试窗",
            "subject": "ETL",
            "slot_code": "S-ETL-01",
            "executor_code": "E-BI-01",
            "day": "2026-11-20",
            "start_min": 540,
            "end_min": 630,
        },
    )
    assert st == 200, created

    before_n = call("GET", "/conflicts/count")[1]["conflict_count"]

    st, conflict = call("POST", "/leases/open", {"window_code": "W-DIM-DUP"})
    assert st == 409, conflict
    receipt = conflict["detail"]
    print("conflict receipt", receipt)
    assert isinstance(receipt, dict) and receipt["conflict_ids"], "回执须带新事件 id"

    # 连败第二次：追加臂下应再 +1 行，计数同步
    st, conflict2 = call("POST", "/leases/open", {"window_code": "W-DIM-DUP"})
    assert st == 409, conflict2

    st, rows = call("GET", "/conflicts")
    st, cnt = call("GET", "/conflicts/count")
    st, win_cnt = call("GET", "/conflicts/count?window_code=W-DIM-DUP")
    st, win_rows = call("GET", "/conflicts?window_code=W-DIM-DUP")
    st, dash2 = call("GET", "/dashboard")
    print("list/global/window counts:", len(rows), cnt["conflict_count"], win_cnt["conflict_count"])

    # 同源铁律：总览 == 列表行数 == count 端点；过滤口径自洽
    assert dash2["conflict_count"] == len(rows) == cnt["conflict_count"]
    assert win_cnt["conflict_count"] == len(win_rows) == 2
    assert cnt["conflict_count"] == before_n + 2, "连败两次必须追加两行"
    assert all(r["window_code"] == "W-DIM-DUP" for r in win_rows)

    st, audit = call("GET", "/audit?window_code=W-DIM-DUP")
    assert st == 200 and len(audit) == 2 and all(a["result"] == "conflict" for a in audit)
    print("audit for W-DIM-DUP:", len(audit), "rows")

    st, closed = call("POST", "/leases/close", {"lease_no": open1["lease_no"]})
    assert st == 200, closed
    print("closed", closed)

    # 关窗/成功路径不得偷删事件
    st, cnt_after = call("GET", "/conflicts/count")
    assert cnt_after["conflict_count"] == cnt["conflict_count"], "成功关窗不得删历史冲突"
    print("SMOKE OK")


if __name__ == "__main__":
    main()
