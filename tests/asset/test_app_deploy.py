"""应用容器化演示：上传 → 审核 → 启动 → 访问 → 停止。

带 docker 的那条是真跑容器的集成测试；没有 docker 的环境自动跳过，
其余用假的 Runner 覆盖状态机与权限。
"""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from fde_asset.modules.app import deploy
from fde_asset.platform.blobs import BlobStore
from fde_asset.platform.runner.docker_runner import LocalDockerRunner
from fde_asset.platform.runner.ports import LoadedImage, RunnerError, RunningContainer
from tests.conftest import as_user

DOCKER = LocalDockerRunner()
HAS_DOCKER = DOCKER.available()


class FakeRunner:
    """不碰 docker 的替身，用来测状态机。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.started: list[str] = []
        self.stopped: list[str] = []

    def load_image(self, tar_path: str) -> LoadedImage:
        if self.fail:
            raise RunnerError("镜像坏了")
        return LoadedImage(tag="fake/app:latest", size_bytes=Path(tar_path).stat().st_size)

    def run(self, tag, container_port, *, name, limits) -> RunningContainer:
        self.started.append(tag)
        return RunningContainer(container_id="fake-container", host_port=18080)

    def stop(self, container_id: str) -> None:
        self.stopped.append(container_id)

    def status(self, container_id: str) -> str:
        return "running"

    def logs(self, container_id: str, tail: int = 200) -> str:
        return "fake logs"


@pytest.fixture()
def store(tmp_path):
    return BlobStore(tmp_path / "blobs")


def _app_id(client) -> str:
    apps = as_user(client, "chen").get("/api/v1/apps").json()["items"]
    return next(item["asset_id"] for item in apps if item["name"] == "quick-labeler")


def _upload(client, asset_id: str, user: str = "chen", content: bytes = b"fake-tar"):
    return as_user(client, user).post(
        f"/api/v1/apps/{asset_id}/image",
        json={
            "filename": "app.tar",
            "content_base64": base64.b64encode(content).decode(),
        },
    )


def test_only_owner_uploads(client) -> None:
    asset_id = _app_id(client)
    assert _upload(client, asset_id, "chen").status_code == 200
    assert _upload(client, asset_id, "li").status_code == 403


def test_cannot_run_before_approval(client, indexed, store) -> None:
    asset_id = _app_id(client)
    _upload(client, asset_id)
    with pytest.raises(deploy.DeployError, match="审核"):
        deploy.start(
            indexed.engine,
            asset_id=asset_id,
            runner=FakeRunner(),
            store=store,
            started_by="chen",
        )


def test_reupload_resets_the_approval(client) -> None:
    """重新上传要重新审核，否则审核就白做了。"""
    asset_id = _app_id(client)
    _upload(client, asset_id)
    as_user(client, "admin").post(f"/api/v1/apps/{asset_id}/review", json={"approve": True})
    assert (
        as_user(client, "chen").get(f"/api/v1/apps/{asset_id}/deployment").json()["review_status"]
        == "approved"
    )

    _upload(client, asset_id, content=b"new-tar")
    assert (
        as_user(client, "chen").get(f"/api/v1/apps/{asset_id}/deployment").json()["review_status"]
        == "pending"
    )


def test_only_admin_reviews(client) -> None:
    asset_id = _app_id(client)
    _upload(client, asset_id)
    assert (
        as_user(client, "chen")
        .post(f"/api/v1/apps/{asset_id}/review", json={"approve": True})
        .status_code
        == 403
    )


def test_reject_needs_a_reason(client) -> None:
    asset_id = _app_id(client)
    _upload(client, asset_id)
    admin = as_user(client, "admin")
    assert admin.post(f"/api/v1/apps/{asset_id}/review", json={"approve": False}).status_code == 422
    assert (
        admin.post(
            f"/api/v1/apps/{asset_id}/review", json={"approve": False, "note": "带了真实凭据"}
        ).status_code
        == 200
    )


def test_oversized_image_is_rejected(client) -> None:
    asset_id = _app_id(client)
    as_user(client, "admin").put(
        "/api/v1/settings", json={"values": {"app_image_size_limit_mb": 1}}
    )
    big = base64.b64encode(b"x" * (2 * 1024 * 1024)).decode()
    denied = as_user(client, "chen").post(
        f"/api/v1/apps/{asset_id}/image", json={"filename": "big.tar", "content_base64": big}
    )
    assert denied.status_code == 422
    assert "上限" in denied.json()["detail"]


def test_start_failure_is_recorded(client, indexed, store) -> None:
    asset_id = _app_id(client)
    _upload(client, asset_id)
    as_user(client, "admin").post(f"/api/v1/apps/{asset_id}/review", json={"approve": True})
    store.put(deploy.IMAGE_SCOPE, f"{asset_id}-app.tar", b"fake")

    with pytest.raises(deploy.DeployError, match="启动失败"):
        deploy.start(
            indexed.engine,
            asset_id=asset_id,
            runner=FakeRunner(fail=True),
            store=store,
            started_by="chen",
        )
    record = deploy.get(indexed.engine, asset_id)
    assert record["run_status"] == "failed"
    assert "镜像坏了" in record["last_error"]


def test_full_state_machine_with_fake_runner(client, indexed, store) -> None:
    asset_id = _app_id(client)
    _upload(client, asset_id)
    as_user(client, "admin").post(f"/api/v1/apps/{asset_id}/review", json={"approve": True})
    store.put(deploy.IMAGE_SCOPE, f"{asset_id}-app.tar", b"fake")
    runner = FakeRunner()

    started = deploy.start(
        indexed.engine, asset_id=asset_id, runner=runner, store=store, started_by="chen"
    )
    assert started["run_status"] == "running"
    assert started["access_url"].endswith(":18080")

    # 再点一次启动不会重复起容器
    deploy.start(indexed.engine, asset_id=asset_id, runner=runner, store=store, started_by="chen")
    assert len(runner.started) == 1

    stopped = deploy.stop(indexed.engine, asset_id=asset_id, runner=runner, stopped_by="chen")
    assert stopped["run_status"] == "stopped" and stopped["access_url"] == ""
    assert runner.stopped == ["fake-container"]


@pytest.mark.skipif(not HAS_DOCKER, reason="本机没有可用的 docker")
def test_real_container_runs_and_serves(client, indexed, store) -> None:
    """真·集成测试：造一个最小镜像，走完上传 → 审核 → 启动 → 访问 → 停止。"""
    work = Path(tempfile.mkdtemp())
    (work / "Dockerfile").write_text(
        "FROM busybox\n"
        'RUN mkdir -p /site && echo "<h1>hello from demo app</h1>" > /site/index.html\n'
        "WORKDIR /site\n"
        'CMD ["httpd", "-f", "-p", "7001", "-h", "/site"]\n',
        encoding="utf-8",
    )
    tag = "fde-demo-test:latest"
    build = subprocess.run(
        ["docker", "build", "-t", tag, str(work)], capture_output=True, text=True, timeout=600
    )
    if build.returncode != 0:
        pytest.skip(f"构建镜像失败（多半是没网拉不到 busybox）：{build.stderr[-300:]}")

    tar_path = work / "app.tar"
    subprocess.run(
        ["docker", "save", "-o", str(tar_path), tag], check=True, capture_output=True, timeout=600
    )

    asset_id = _app_id(client)
    response = _upload(client, asset_id, content=tar_path.read_bytes())
    assert response.status_code == 200, response.text
    as_user(client, "admin").post(f"/api/v1/apps/{asset_id}/review", json={"approve": True})

    # 用 API 的 store（不是夹具里的临时 store）
    from fde_asset.api.deps import build_context

    context = build_context(indexed.settings)
    record = deploy.start(
        context.engine,
        asset_id=asset_id,
        runner=DOCKER,
        store=context.blob_store,
        started_by="chen",
    )
    try:
        assert record["run_status"] == "running"
        url = record["access_url"]
        body = ""
        for _ in range(20):
            try:
                body = httpx.get(url, timeout=2).text
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        assert "hello from demo app" in body, f"没从 {url} 读到内容"

        logs = deploy.logs(context.engine, asset_id=asset_id, runner=DOCKER)
        assert isinstance(logs, str)
    finally:
        deploy.stop(context.engine, asset_id=asset_id, runner=DOCKER, stopped_by="chen")
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, timeout=120)

    after = deploy.get(context.engine, asset_id)
    assert after["run_status"] == "stopped"


@pytest.mark.skipif(not HAS_DOCKER, reason="本机没有可用的 docker")
def test_container_runs_without_privileges() -> None:
    """安全边界：不开特权、丢掉 capability、不挂宿主机目录。"""
    from fde_asset.platform.runner.ports import RunLimits

    limits = RunLimits()
    assert "--security-opt" in limits.hardening and "no-new-privileges" in limits.hardening
    assert "--cap-drop" in limits.hardening and "ALL" in limits.hardening
    source = Path("src/fde_asset/platform/runner/docker_runner.py").read_text(encoding="utf-8")
    assert "--privileged" not in source
    assert "-v " not in source and "--volume" not in source
    assert json.dumps(True)  # 占位，保证本用例不被优化掉
