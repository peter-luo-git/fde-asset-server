"""把数据目录里的一个账号名整体改成另一个。

用在和平台共用账号的时候：资产、草稿、评审、通知都是按账号名记的，平台上的账号名和
这里记的不一样（比如平台要求至少 3 个字符），就要把数据里的名字改过去。

只改数据库和名单文件，不改 git 仓库里的内容（改了会产生新版本并触发变更通知）；
仓库里如果有 `user:<旧名>` 的引用，会列出来让人处理。改之前自动备份数据库。

用法：python scripts/rename_user.py --data-dir <数据目录> --from li --to xiaoli
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def rename_in_database(path: Path, old: str, new: str) -> dict[str, int]:
    """整列等于旧名、`user:旧名`，以及 JSON 文本里作为完整字符串出现的旧名，都换成新名。"""
    token = re.compile(r'"(user:)?' + re.escape(old) + r'"')
    changed: dict[str, int] = {}
    connection = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            for column in [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]:
                count = 0
                for value, replacement in ((old, new), (f"user:{old}", f"user:{new}")):
                    count += connection.execute(
                        f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?',
                        (replacement, value),
                    ).rowcount
                rows = connection.execute(
                    f'SELECT rowid, "{column}" FROM "{table}" '
                    f'WHERE typeof("{column}") = \'text\' AND "{column}" LIKE ?',
                    (f'%{old}"%',),
                ).fetchall()
                for rowid, text in rows:
                    updated = token.sub(lambda m: f'"{m.group(1) or ""}{new}"', text)
                    if updated != text:
                        connection.execute(
                            f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?', (updated, rowid)
                        )
                        count += 1
                if count:
                    changed[f"{table}.{column}"] = count
        connection.commit()
    finally:
        connection.close()
    return changed


def rename_in_directory(path: Path, old: str, new: str) -> int:
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    users = data.get("users", {})
    if old in users and new not in users:
        users[new] = users.pop(old)
        count += 1
    for section in ("engagements", "agents"):
        for item in data.get(section, {}).values():
            if item.get("owner") == old:
                item["owner"] = new
                count += 1
    if count:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return count


def references_in_repos(repos: Path, old: str) -> list[str]:
    found: list[str] = []
    pattern = re.compile(r"user:" + re.escape(old) + r"\b")
    for repo in sorted(repos.glob("*.git")):
        result = subprocess.run(
            ["git", "-C", str(repo), "grep", "-n", f"user:{old}", "main"],
            capture_output=True,
            text=True,
            check=False,
        )
        found += [
            f"{repo.name}: {line}" for line in result.stdout.splitlines() if pattern.search(line)
        ]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--from", dest="old", required=True)
    parser.add_argument("--to", dest="new", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9_.-]{1,32}", args.old) or not re.fullmatch(
        r"[a-z0-9_.-]{3,32}", args.new
    ):
        print("账号名只能是小写字母、数字、_ . -；新名字要 3 到 32 个字符", file=sys.stderr)
        return 2
    database = args.data_dir / "asset.db"
    if not database.exists():
        print(f"找不到 {database}", file=sys.stderr)
        return 2
    backup = database.with_name(f"asset.db.bak-rename-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(database, backup)

    changed = rename_in_database(database, args.old, args.new)
    listed = rename_in_directory(args.data_dir / "directory.json", args.old, args.new)
    print(
        f"数据库（备份在 {backup.name}）："
        + ("、".join(f"{k} {v} 处" for k, v in sorted(changed.items())) or "没有要改的")
    )
    print(f"名单文件：{listed} 处")
    leftovers = references_in_repos(args.data_dir / "repos", args.old)
    if leftovers:
        print("仓库里还有这些引用没有改（需要作为一次资产修改提交）：")
        print("\n".join("  " + line for line in leftovers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
