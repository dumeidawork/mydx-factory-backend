"""种子默认管理员账号：admin / Admin@123456（已存在则跳过）。"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.database import execute, fetch_one  # noqa: E402
from app.core.security import hash_password  # noqa: E402

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "Admin@123456"
DEFAULT_NAME = "系统管理员"
DEFAULT_ROLE_ID = 1


def main() -> None:
    existing = fetch_one("SELECT id FROM users WHERE username = %s", (DEFAULT_USERNAME,))
    if existing:
        print(f"user '{DEFAULT_USERNAME}' already exists (id={existing['id']}), skip")
        return
    role = fetch_one("SELECT id FROM roles WHERE id = %s", (DEFAULT_ROLE_ID,))
    if not role:
        raise SystemExit("roles 表缺少 id=1 (super_admin)，请先执行 init_db.sql")
    new_id = execute(
        """
        INSERT INTO users (name, role_id, department, language_preference, username, password_hash)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (DEFAULT_NAME, DEFAULT_ROLE_ID, "管理", "zh-CN", DEFAULT_USERNAME, hash_password(DEFAULT_PASSWORD)),
    )
    print(f"created admin user id={new_id} username={DEFAULT_USERNAME} password={DEFAULT_PASSWORD}")


if __name__ == "__main__":
    main()
