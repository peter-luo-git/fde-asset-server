"""技能依赖：预装的直接用；requirements.txt 写错挡提交；脚本用了没声明的库给提醒；
共享技能带新依赖要评审人确认；个人技能带依赖有数量限额。"""

from __future__ import annotations

import pytest

from fde_asset.modules.asset import skill_deps
from tests.asset.test_skill_packages import (  # noqa: F401 - 复用夹具
    HEADERS,
    SKILL_MD,
    _as,
    _fill_and_submit,
    _upload,
    service,
)

# 和 fde-server、fde-runtime 用同一组对照值
VECTOR_TEXT = "jieba==0.42.1\n# c\nPyYAML>=6 , <7\nRequests[Socks]\n"
VECTOR_LINES = ["jieba==0.42.1", "pyyaml>=6,<7", "requests[socks]"]


def test_analyze_splits_preinstalled_new_and_undeclared() -> None:
    assert skill_deps.parse_requirements(VECTOR_TEXT) == VECTOR_LINES
    report = skill_deps.analyze(
        {
            "SKILL.md": "x",
            "requirements.txt": "tabulate==0.9.0\npython-pptx\nPyYAML\n",
            "scripts/run.py": "import os, json\nimport tabulate\nimport yaml\nfrom pptx import Presentation\n"
            "import requests\nfrom helpers import x\n",
            "scripts/helpers.py": "",
        }
    )
    assert report["new"] == ["pyyaml", "tabulate==0.9.0"]
    assert report["preinstalled"] == ["python-pptx"]
    assert report["undeclared_imports"] == ["requests"]
    assert report["error"] == ""
    assert "网址" in skill_deps.analyze({"requirements.txt": "x @ https://a/b.whl"})["error"]


def _pass_trial(client) -> None:
    job = client.post("/internal/v1/skill-trials/claim", headers=HEADERS).json()
    if job:
        steps = [{"id": step["id"], "ok": True, "exit_code": 0} for step in job["plan"]]
        client.post(
            f"/internal/v1/skill-trials/{job['id']}/result", json={"steps": steps}, headers=HEADERS
        )


def test_shared_skill_with_new_deps_needs_reviewer_confirmation(service) -> None:  # noqa: F811
    draft = _upload(
        service,
        {
            "SKILL.md": SKILL_MD.format(name="table-tool"),
            "requirements.txt": "tabulate==0.9.0\npython-pptx\n",
            "scripts/run.py": "import tabulate\nimport yaml\n",
        },
        scope="department",
        department_code="data-intel",
    )
    checked = service.post(f"/api/v1/harvest-candidates/{draft['candidate_id']}/checks").json()
    deps = checked["skill"]["deps"]
    assert deps["new"] == ["tabulate==0.9.0"] and deps["preinstalled"] == ["python-pptx"]
    assert any("yaml" in hint["message"] for hint in checked["skill"]["hints"])
    _pass_trial(service)
    submitted = _fill_and_submit(service, draft)
    assert submitted.status_code == 200, submitted.text
    review_id = submitted.json()["review_id"]
    reviewer = _as(service, "li")
    refused = reviewer.post(f"/api/v1/reviews/{review_id}/decide", json={"approve": True})
    assert refused.status_code == 422 and "tabulate==0.9.0" in refused.json()["detail"]
    approved = reviewer.post(
        f"/api/v1/reviews/{review_id}/decide", json={"approve": True, "deps_confirmed": True}
    )
    assert approved.status_code == 200, approved.text


def test_invalid_requirements_block_submit(service) -> None:  # noqa: F811
    draft = _upload(
        service,
        {
            "SKILL.md": SKILL_MD.format(name="bad-deps"),
            "requirements.txt": "--index-url http://x\n",
        },
    )
    submitted = _fill_and_submit(service, draft)
    assert submitted.status_code in (400, 409, 422), submitted.text
    assert "requirements.txt" in submitted.text


def test_personal_skills_with_deps_are_limited(service, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(skill_deps, "PERSONAL_LIMIT", 1)
    for name in ("deps-one", "deps-two"):
        draft = _upload(
            service,
            {"SKILL.md": SKILL_MD.format(name=name), "requirements.txt": "tabulate==0.9.0\n"},
        )
        checked = service.post(f"/api/v1/harvest-candidates/{draft['candidate_id']}/checks").json()
        if name == "deps-one":
            assert not [f for f in checked["format"] if f["code"] == "personal_deps_limit"]
            _pass_trial(service)
            result = _fill_and_submit(service, draft).json()
            if not result.get("auto_approved"):
                _as(service, "li").post(
                    f"/api/v1/reviews/{result['review_id']}/decide", json={"approve": True}
                )
                _as(service, "chen")
        else:
            assert [f for f in checked["format"] if f["code"] == "personal_deps_limit"]


@pytest.mark.parametrize("line", ["-e .", "pkg @ https://x/y.whl", "a; python_version<'3'"])
def test_rejects_non_index_requirements(line) -> None:
    with pytest.raises(skill_deps.RequirementsError):
        skill_deps.parse_requirements(line)


def test_key_matches_server_and_runtime_vector() -> None:
    assert (
        skill_deps.deps_key(VECTOR_LINES)
        == "13a4ccc826ee6691ab0be8df51bc8143bbfc270365ce152cd0d4899b847fbdec"
    )


def test_wanted_status_report_and_personal_bytes_limit(service, monkeypatch) -> None:  # noqa: F811
    # 一个带依赖的个人技能入库
    draft = _upload(
        service,
        {"SKILL.md": SKILL_MD.format(name="deps-a"), "requirements.txt": "tabulate==0.9.0\n"},
    )
    _pass_trial(service)
    result = _fill_and_submit(service, draft).json()
    if not result.get("auto_approved"):
        _as(service, "li").post(
            f"/api/v1/reviews/{result['review_id']}/decide", json={"approve": True}
        )
        _as(service, "chen")
    wanted = service.get("/internal/v1/skill-deps/wanted", headers=HEADERS).json()["items"]
    key = skill_deps.deps_key(["tabulate==0.9.0"])
    assert {"key": key, "requirements": ["tabulate==0.9.0"]} in wanted
    # worker 回报：键要和清单对得上，乱写的不收
    report = {
        "items": [
            {"key": key, "requirements": ["tabulate==0.9.0"], "state": "ready", "bytes": 2048},
            {"key": "0" * 64, "requirements": ["tabulate==0.9.0"], "state": "ready", "bytes": 1},
        ]
    }
    assert service.post("/internal/v1/skill-deps/status", json=report, headers=HEADERS).json() == {
        "saved": 1
    }
    # 再提交一个带依赖的个人技能：合计超过上限就挡
    monkeypatch.setattr(skill_deps, "PERSONAL_BYTES_LIMIT", 1024)
    draft = _upload(
        service, {"SKILL.md": SKILL_MD.format(name="deps-b"), "requirements.txt": "rich==13.0.0\n"}
    )
    checked = service.post(f"/api/v1/harvest-candidates/{draft['candidate_id']}/checks").json()
    assert [f for f in checked["format"] if f["code"] == "personal_deps_bytes_limit"]
    assert checked["skill"]["deps"]["key"] == skill_deps.deps_key(["rich==13.0.0"])


@pytest.mark.parametrize("method, path", [("get", "wanted"), ("post", "status")])
def test_dependency_worker_endpoints_require_service_key(service, method, path) -> None:  # noqa: F811
    request = getattr(service, method)
    kwargs = {"json": {"items": []}} if method == "post" else {}
    url = f"/internal/v1/skill-deps/{path}"
    assert request(url, **kwargs).status_code == 404
    assert request(url, headers={"X-FDE-Service-Key": "wrong"}, **kwargs).status_code == 404
