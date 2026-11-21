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

本项目只贯彻「**事件只追加（append-only event log）**」一种架构，不引入物化现行/归档的第二套模型：

- 每次开窗失败，按失败原因逐条向 `conflicts` 插入**新事件行**，只 INSERT，永不 UPDATE/DELETE；
- **冲突计数 = `conflicts` 表 `COUNT(*)`**，不另存物化计数（旧 `watermarks.conflict_count` 已废弃并在启动迁移时清除）；
- 冲突列表按 `created_at DESC, id DESC`，不截断；`/conflicts` 支持 `?window_code=` 按批窗过滤，过滤计数与过滤列表同 WHERE 同源；
- 并发连败由 `BEGIN IMMEDIATE` 串行化，纯插入各自成行，不覆盖、不丢行；
- 开窗成功/关窗只新增 `audit_events` 审计行，**不物理删除任何历史冲突**；
- 所有表面（总览、冲突页、按批窗过滤、租约开窗 409 回执、审计）统一经 `app/services/conflict_store.py` 读写，禁止路由自行拼 SQL、禁止只改展示。

核对脚本（独立临时库，不污染开发库）：

```bash
.venv/bin/python tests/check_append_arm.py
```

覆盖：连败三次三行、多原因一次多行、计数==列表行数、按批窗过滤同源、并发双败/同窗双败不丢行不覆盖、成功开窗不偷删、409 回执带事件 id、旧水位迁移。

## Docker

```powershell
docker compose up -d --build
```
