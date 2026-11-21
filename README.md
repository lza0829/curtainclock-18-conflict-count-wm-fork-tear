# Curtainclock（幕窗钟）

骨架：**批窗定义 → 算力槽/执行器资源依赖就绪 → 开窗租约占用 → 关窗释放与冲突水位互证**

- 前端：http://127.0.0.1:6100 （React + Vite）
- API：http://127.0.0.1:11100

## 本地冒烟（推荐）

```powershell
cd D:\Asolo11\claude-workspace\curtainclock\source
# 本机若无 3.11：py -V:Astral/CPython3.11.15 -m venv .venv
py -V:Astral/CPython3.11.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:PYTHONPATH = "$PWD\backend"
Start-Process .\.venv\Scripts\uvicorn.exe -ArgumentList 'app.main:app','--host','127.0.0.1','--port','11100' -WorkingDirectory "$PWD\backend"
cd frontend
# 前端需 Node ≥18；不足时用 docker compose 起 web
npm install
npm run dev
```

浏览器打开 http://127.0.0.1:6100 ：开一节课 → 同算力槽冲突 → 关窗释放。

## 冲突正确性口径（全局唯一架构：冲突事件只追加）

本项目只贯彻「**事件只追加（append-only event log）**」一种架构，不引入物化现行/归档的第二套模型。
「冲突计数听谁」的答案是**以 `conflicts` 表可重放集合为准**：水位只是可由表重算的派生投影，
垂直方向上禁止任何旁路计数（旧 `conflict_panel` 徽章缓存、`+2` 涂色、截断 50 条均已删除）。

- 每次开窗失败，按失败原因逐条向 `conflicts` 插入**新事件行**，只 INSERT，永不 UPDATE/DELETE；
- **冲突计数 = `conflicts` 表 `COUNT(*)`**；列表与计数共用同一 `WHERE` 构造器，过滤不会各拼一套条件；
- 冲突列表按 `created_at DESC, id DESC`，不截断；`/conflicts` 与 `/conflicts/count` 均支持
  `?window_code=` / `?resource_code=`，过滤计数与过滤列表同源；
- 旧 `watermarks.conflict_count` 已废弃并在启动迁移时清除；若旧库仍残留该行，读取新版时按表重算，
  不符则回写为表值并在响应里标 `stale/healed_from`（**水位不得反过来决定计数**）；
- 并发连败由 `BEGIN IMMEDIATE` 串行化，纯插入各自成行，不覆盖、不丢行、不双计；
- 开窗成功/关窗只新增 `audit_events` 审计行，**不物理删除任何历史冲突**（覆盖/归档不会让冲突页减一）；
- 所有表面（总览、冲突页、按批窗/资源过滤、资源页冲突角标、`/watermark`、租约开窗 409 回执、审计）
  统一经 `app/services/conflict_store.py` 读写，禁止路由自行拼 SQL、禁止只改展示/徽章缓存。

### 四条链必须对齐

| 链 | 来源 | 端点/字段 |
| --- | --- | --- |
| 冲突页合计 | `list_conflicts` 行数 | `GET /conflicts` |
| 计数端点/水位 actual | 同 WHERE 的 `COUNT(*)` | `GET /conflicts/count`、`GET /watermark` |
| 总览冲突徽章 | 同表重算，附 `watermarks_consistent` | `GET /dashboard` |
| 资源页冲突角标 | `GROUP BY resource_code` 现算 | `GET /slots`、`/executors`、`/conflicts/by-resource` |

核对脚本（独立临时库，不污染开发库）：

```bash
.venv/bin/python tests/check_append_arm.py
.venv/bin/python tests/check_conflict_tear.py
```

`check_append_arm.py` 覆盖：连败三次三行、多原因一次多行、计数==列表行数、按批窗过滤同源、
并发双败/同窗双败不丢行不覆盖、成功开窗不偷删、409 回执带事件 id、旧水位迁移。

`check_conflict_tear.py` 覆盖题面四类撕裂夹具：

- **A 回执有因、页面无行**：409 回执的每个 `conflict_ids` 必须出现在冲突页，原因逐字一致；
- **B 表有行、水位未加**：计数不受低水位影响，水位被识别 stale 并按表回写；
- **C 覆盖后三分叉**：旧面板缓存/水位被改写或清除后，总览、页面、计数、水位不得各说各话，
  成功开窗/关窗不得让历史冲突行减少；
- **D 并发双计/漏计**：并发冲突各自成行，总览==页面==计数==表，资源角标与按资源筛选同源。

## Docker

```powershell
docker compose up -d --build
```
