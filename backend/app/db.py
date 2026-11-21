from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH

# 架构臂 A：冲突事件只追加（append-only event log）。
# 不变量：
# 1. conflicts / audit_events 只允许 INSERT，应用代码不得 UPDATE/DELETE（物理删除禁用）；
# 2. 冲突计数永远等于 conflicts 表 COUNT(*)，不另存物化计数（旧 conflict_count 水位已废弃）；
# 3. 列表按 created_at DESC, id DESC 排序，id 作为同秒事件的稳定次序；
# 4. 并发写入由 BEGIN IMMEDIATE 串行化，纯插入各自成行，禁止读-改-写覆盖。
SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  capacity INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS executors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  subject TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  subject TEXT NOT NULL,
  slot_code TEXT NOT NULL,
  executor_code TEXT NOT NULL,
  day TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS leases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lease_no TEXT NOT NULL UNIQUE,
  window_code TEXT NOT NULL,
  slot_code TEXT NOT NULL,
  executor_code TEXT NOT NULL,
  day TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  status TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE TABLE IF NOT EXISTS conflicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  window_code TEXT NOT NULL,
  resource_code TEXT NOT NULL,
  day TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conflicts_window ON conflicts(window_code);
CREATE INDEX IF NOT EXISTS idx_conflicts_created ON conflicts(created_at, id);
-- 审计：与 conflicts 同源只追加，记录开窗成败与关窗动作，永不物理删除
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  window_code TEXT NOT NULL,
  lease_no TEXT,
  result TEXT NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_window ON audit_events(window_code);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at, id);
CREATE TABLE IF NOT EXISTS watermarks (
  key TEXT PRIMARY KEY,
  value INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    # 外键/类型保持默认；串行化由 BEGIN IMMEDIATE 保证
    return conn


@contextmanager
def transaction():
    conn = connect()
    try:
        # IMMEDIATE：取写锁后所有并发事务排队，连败各自插入、互不覆盖
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # 迁移：旧版本把冲突计数/在途租约物化为水位，与事件行双写会漂移。
        # 臂 A 下计数一律由事件表/租约表现算，废弃这两个键（保留 watermarks 表本身兼容）。
        conn.execute("DELETE FROM watermarks WHERE key IN ('conflict_count', 'open_leases')")
        # 旧版「面板缓存/涂色徽章」是第二套可写计数源（甚至 +2、截断 50 条），
        # 已由 conflict_panel 移除；残留设置键一并清除，防止读取到幽灵计数。
        conn.execute(
            "DELETE FROM settings WHERE key IN ('conflict_panel_cache', 'conflict_panel_badge')"
        )
        conn.commit()
    finally:
        conn.close()
