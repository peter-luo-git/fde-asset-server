"""草稿标识：不填就由服务端生成；自己填的仍然只能是小写字母、数字和连字符。"""

from __future__ import annotations

import base64
import re

from tests.conftest import as_user

GENERATED = re.compile(r"^case-\d{8}-[0-9a-f]{4}$")


def _create(client, **overrides):
    body = {"kind": "Case", "title": "导入时偶发超时", "scope": "department"}
    body["department_code"] = "data-intel"
    body.update(overrides)
    return client.post("/api/v1/harvest-candidates", json=body)


def test_name_is_generated_when_omitted(client) -> None:
    chen = as_user(client, "chen")
    first = _create(chen).json()
    second = _create(chen, name="  ").json()

    assert GENERATED.match(first["name"]), first["name"]
    assert GENERATED.match(second["name"])
    assert first["name"] != second["name"], "同一天连着建两份也不能重名"
    assert f"name: {first['name']}" in first["files"]["asset.yaml"], "生成的标识要写进资产文件"


def test_prefix_follows_the_kind(client) -> None:
    chen = as_user(client, "chen")
    assert _create(chen, kind="Skill").json()["name"].startswith("skill-")
    assert _create(chen, kind="Experience").json()["name"].startswith("exp-")
    assert _create(chen, kind="Application").json()["name"].startswith("application-")


def test_generated_name_passes_the_format_check(client) -> None:
    chen = as_user(client, "chen")
    candidate = _create(chen).json()
    checked = chen.post(f"/api/v1/harvest-candidates/{candidate['candidate_id']}/checks").json()
    findings = checked.get("checks", checked)["format"]
    assert not any("name" in item["code"] for item in findings), findings


def test_own_name_is_kept_and_validated(client) -> None:
    chen = as_user(client, "chen")
    assert _create(chen, name="import-timeout").json()["name"] == "import-timeout"

    for bad in ("导入超时", "Import Timeout", "-leading", "a" * 65):
        response = _create(chen, name=bad)
        assert response.status_code == 422, bad
        assert "不填会自动生成" in response.json()["detail"]


def test_uploads_with_chinese_titles_no_longer_collide(client) -> None:
    chen = as_user(client, "chen")
    names = []
    for title in ("历史方案一", "历史方案二"):
        body = {
            "kind": "Solution",
            "title": title,
            "filename": "note.txt",
            "content_base64": base64.b64encode("一些正文".encode()).decode(),
            "scope": "department",
            "department_code": "data-intel",
        }
        response = chen.post("/api/v1/harvest-candidates/from-upload", json=body)
        assert response.status_code == 200, response.text
        names.append(response.json()["name"])
    assert names[0] != names[1]
    assert all(re.match(r"^solution-\d{8}-[0-9a-f]{4}$", name) for name in names), names
