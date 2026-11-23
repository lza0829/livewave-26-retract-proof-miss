import os
import sqlite3
from pathlib import Path



忙等毫秒 = 5000


def 数据目录() -> Path:
    return Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))


def 夹具目录() -> Path:
    return Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))


def 库路径() -> Path:
    return 数据目录() / "播浪.db"


def 连接() -> sqlite3.Connection:
    数据目录().mkdir(parents=True, exist_ok=True)
    
    
    c = sqlite3.connect(库路径(), timeout=忙等毫秒 / 1000, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    
    c.execute("PRAGMA journal_mode = WAL")
    c.execute(f"PRAGMA busy_timeout = {忙等毫秒}")
    return c
