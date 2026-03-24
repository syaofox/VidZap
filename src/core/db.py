import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("database.sqlite")


def init_db() -> None:
    """初始化数据库，创建表"""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cookies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL UNIQUE,
                cookie_file TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT,
                thumbnail TEXT,
                format_id TEXT,
                resolution TEXT,
                file_path TEXT,
                status TEXT DEFAULT 'pending',
                error_msg TEXT,
                file_size INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );
        """)
        # 兼容旧表：添加新列（忽略已存在的错误）
        for sql in [
            "ALTER TABLE downloads ADD COLUMN thumbnail TEXT",
            "ALTER TABLE downloads ADD COLUMN error_msg TEXT",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass


@contextmanager
def get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
