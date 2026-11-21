from __future__ import annotations

import os
from pathlib import Path

PROJECT_NAME = "幕窗钟"
# 允许测试用独立库覆盖；默认仍落 data/curtainclock.db
DB_PATH = Path(os.environ.get("CURTAINCLOCK_DB", Path(__file__).resolve().parents[2] / "data" / "curtainclock.db"))
