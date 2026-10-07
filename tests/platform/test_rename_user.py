"""账号改名：数据库里按账号名记的地方和名单文件一起换，别的同名片段不能误伤。"""

from __future__ import annotations

import json
import sqlite3

from scripts.rename_user import rename_in_database, rename_in_directory


def test_renames_exact_values_refs_and_json_strings_only(tmp_path) -> None:
    path = tmp_path / "asset.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE reviews (decided_by TEXT, owner_ref TEXT, payload TEXT, note TEXT)"
    )
    connection.executemany(
        "INSERT INTO reviews VALUES (?, ?, ?, ?)",
        [
            ("li", "user:li", json.dumps({"by": "li", "to": ["user:li", "lin"]}), "li 同意了"),
            ("lin", "department:li", json.dumps({"by": "oliver"}), "policy"),
        ],
    )
    connection.commit()
    connection.close()

    changed = rename_in_database(path, "li", "xiaoli")

    rows = sqlite3.connect(path).execute("SELECT * FROM reviews ORDER BY rowid").fetchall()
    assert rows[0][0] == "xiaoli" and rows[0][1] == "user:xiaoli"
    assert json.loads(rows[0][2]) == {"by": "xiaoli", "to": ["user:xiaoli", "lin"]}
    # 自由文本、别人的名字、别的作用域里碰巧含这两个字母的都不动
    assert rows[0][3] == "li 同意了"
    assert rows[1] == ("lin", "department:li", json.dumps({"by": "oliver"}), "policy")
    assert changed == {"reviews.decided_by": 1, "reviews.owner_ref": 1, "reviews.payload": 1}


def test_renames_user_and_owner_in_directory(tmp_path) -> None:
    path = tmp_path / "directory.json"
    path.write_text(
        json.dumps(
            {
                "users": {"li": {"display_name": "小李"}, "lin": {}},
                "engagements": {"p": {"owner": "li"}, "q": {"owner": "lin"}},
            }
        ),
        encoding="utf-8",
    )

    assert rename_in_directory(path, "li", "xiaoli") == 2

    data = json.loads(path.read_text(encoding="utf-8"))
    assert (
        set(data["users"]) == {"xiaoli", "lin"}
        and data["users"]["xiaoli"]["display_name"] == "小李"
    )
    assert data["engagements"] == {"p": {"owner": "xiaoli"}, "q": {"owner": "lin"}}
    assert rename_in_directory(tmp_path / "missing.json", "li", "xiaoli") == 0
