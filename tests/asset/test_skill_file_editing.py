"""上传及草稿文件编辑：真实HTTP/持久检查状态，模型始终为本地替身。"""

# ruff: noqa: F811
from types import SimpleNamespace

import pytest

from fde_asset.api import routes_harvest
from fde_asset.modules.asset import skill_checks, skill_package
from tests.asset.test_skill_packages import SKILL_MD, _as, _upload, service  # noqa: F401


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    calls = []

    def complete(system, prompt):
        calls.append(prompt)
        return {"risk": "low", "summary": "Local fake review", "findings": [], "scripts": []}

    monkeypatch.setattr(
        routes_harvest,
        "_model_llm",
        lambda _engine: SimpleNamespace(usable=True, complete_json=complete),
    )
    return calls


def draft(client, *, script=False):
    files = {"SKILL.md": SKILL_MD.format(name="editable-skill"), "references/note.txt": "v1"}
    if script:
        files["scripts/run.py"] = "print('old')\n"
    return _upload(client, files)


def endpoint(candidate):
    return f"/api/v1/harvest-candidates/{candidate['candidate_id']}/skill-files"


def test_upload_and_script_edit_refresh_model_checks_for_current_files(service, fake_model):
    candidate = draft(service, script=True)
    assert candidate["checks"]["skill"]["trial"]["status"] == "pending"
    assert len(fake_model) == 1 and "print('old')" in fake_model[0]
    current = service.get(endpoint(candidate).removesuffix("/skill-files")).json()
    assert current["checks"]["skill"]["model"]["status"] == "done"
    changed = service.put(
        endpoint(candidate), json={"path": "scripts/run.py", "content": "print('new')\n"}
    )
    assert changed.status_code == 200, changed.text
    refreshed = service.get(endpoint(candidate).removesuffix("/skill-files")).json()
    assert refreshed["checks"]["skill"]["model"]["status"] == "done"
    assert len(fake_model) == 2 and "print('new')" in fake_model[-1]
    assert (
        refreshed["checks"]["skill"]["trial"]["id"] != candidate["checks"]["skill"]["trial"]["id"]
    )


def test_author_edit_text_and_delete_refreshes_checks_without_model(service, fake_model):
    candidate = draft(service)
    changed = service.put(
        endpoint(candidate), json={"path": "references/note.txt", "content": "v2"}
    )
    assert changed.status_code == 200 and changed.json()["files"]["references/note.txt"] == "v2"
    assert changed.json()["checks"]["skill"]["trial"] is None
    removed = service.put(
        endpoint(candidate), json={"path": "references/note.txt", "content": None}
    )
    assert removed.status_code == 200 and "references/note.txt" not in removed.json()["files"]
    assert (
        service.put(endpoint(candidate), json={"path": "SKILL.md", "content": None}).status_code
        == 422
    )
    assert (
        service.put(endpoint(candidate), json={"path": "missing.txt", "content": None}).status_code
        == 404
    )
    assert fake_model == []


@pytest.mark.parametrize(
    "path", ["../escape", "/absolute", "asset.yaml", "attachments/x", ".", "./", ".git/config"]
)
def test_edit_rejects_unsafe_or_reserved_paths(service, path):
    candidate = draft(service)
    response = service.put(endpoint(candidate), json={"path": path, "content": "invalid"})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("user", ["zhao", "admin"])
def test_non_author_cannot_edit_draft(service, user, fake_model):
    candidate = draft(service)
    response = _as(service, user).put(
        endpoint(candidate), json={"path": "notes.txt", "content": "no"}
    )
    assert response.status_code == 403
    assert fake_model == []


def test_file_size_count_total_and_type_are_rejected_before_mutation(service, monkeypatch):
    candidate = draft(service)
    monkeypatch.setattr(skill_package, "MAX_FILE_BYTES", 6)
    assert (
        service.put(endpoint(candidate), json={"path": "note.txt", "content": "汉字汉"}).status_code
        == 413
    )
    monkeypatch.setattr(skill_package, "MAX_FILE_BYTES", 1024**2)
    assert (
        service.put(endpoint(candidate), json={"path": "note.txt", "content": {}}).status_code
        == 422
    )
    monkeypatch.setattr(skill_package, "MAX_FILES", 2)
    assert (
        service.put(endpoint(candidate), json={"path": "note.txt", "content": "x"}).status_code
        == 413
    )
    monkeypatch.setattr(skill_package, "MAX_FILES", 200)
    monkeypatch.setattr(skill_package, "MAX_TOTAL_BYTES", 10)
    assert (
        service.put(endpoint(candidate), json={"path": "note.txt", "content": "x"}).status_code
        == 413
    )


def test_model_cache_version_and_script_source_are_used(service, fake_model):
    engine = service.app.state.context.engine
    files = {
        "SKILL.md": SKILL_MD.format(name="cache-test"),
        "scripts/run.py": "print('SOURCE_MARKER')",
    }
    first = skill_checks.model_review(
        engine, "local-test", files, routes_harvest._model_llm(engine)
    )
    assert first["status"] == "done" and "SOURCE_MARKER" in fake_model[0]
    assert skill_checks.model_for(engine, files)["id"] == first["id"]
    assert skill_checks.model_digest(files) != skill_checks.digest(files)
    assert (
        skill_checks.model_review(engine, "local-test", files, routes_harvest._model_llm(engine))[
            "id"
        ]
        == first["id"]
    )
    assert len(fake_model) == 1
