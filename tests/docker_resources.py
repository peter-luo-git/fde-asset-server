"""真实 Docker 用例的独立资源：仅清理当前 owner 的容器/网络，保留镜像和卷。"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import replace

from fde_asset.platform.runner.compose_runner import LocalComposeRunner
from fde_asset.platform.runner.docker_runner import LocalDockerRunner
from fde_asset.platform.runner.ports import RunLimits, RunningContainer

OWNER_LABEL = "fde.test.owner"


class OwnedDockerResources:
    def __init__(self) -> None:
        self.owner = "fde-test-" + uuid.uuid4().hex
        self.tag = self.owner + ":latest"
        self.network = self.owner
        self.container = self.owner
        self.project = "fde-demo-" + self.owner
        self.network_created = False

    @staticmethod
    def command(args: list[str]) -> str:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return result.stdout.strip()

    def assert_absent(self, kind: str, name: str) -> None:
        result = subprocess.run(
            ["docker", kind, "inspect", name], capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            raise RuntimeError(f"测试资源已存在，拒绝覆盖：{kind} {name}")
        if "No such" not in result.stderr and "not found" not in result.stderr:
            raise RuntimeError(result.stderr.strip())

    def __enter__(self) -> OwnedDockerResources:
        self.assert_absent("image", self.tag)
        self.assert_absent("network", self.network)
        self.assert_absent("container", self.container)
        existing = self.command(
            [
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{.ID}}",
            ]
        )
        if existing:
            raise RuntimeError(f"测试 compose 项目已存在，拒绝覆盖：{self.project}")
        self.command(
            [
                "network",
                "create",
                "--label",
                f"{OWNER_LABEL}={self.owner}",
                self.network,
            ]
        )
        self.network_created = True
        return self

    @staticmethod
    def _exact_container_error(error: str, message: str) -> bool:
        return error.strip() in (
            message,
            f"Error: {message}",
            f"Error response from daemon: {message}",
        )

    def _container_labels(self, container_id: str) -> dict | None:
        inspected = subprocess.run(
            ["docker", "container", "inspect", "--format", "{{json .Config.Labels}}", container_id],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if inspected.returncode:
            if self._exact_container_error(inspected.stderr, f"No such container: {container_id}"):
                return None
            raise RuntimeError(inspected.stderr.strip())
        return json.loads(inspected.stdout)

    def _confirm_container_removed(self, container_id: str, removal_error: RuntimeError) -> None:
        # --rm can already be removing the exact container after ownership was
        # verified. Success requires confirmed absence, not a swallowed error.
        for attempt in range(10):
            labels = self._container_labels(container_id)
            if labels is None:
                return
            if labels.get(OWNER_LABEL) != self.owner:
                raise RuntimeError(f"容器归属已变化，拒绝清理：{container_id}")
            if attempt < 9:
                time.sleep(0.1)
        raise removal_error

    def cleanup_containers(self) -> None:
        ids = self.command(
            ["ps", "-a", "--filter", f"label={OWNER_LABEL}={self.owner}", "--format", "{{.ID}}"]
        ).splitlines()
        for container_id in ids:
            labels = self._container_labels(container_id)
            if labels is None or labels.get(OWNER_LABEL) != self.owner:
                continue
            try:
                self.command(["rm", "-f", container_id])
            except RuntimeError as exc:
                if not (
                    self._exact_container_error(str(exc), f"No such container: {container_id}")
                    or self._exact_container_error(
                        str(exc), f"removal of container {container_id} is already in progress"
                    )
                ):
                    raise
                self._confirm_container_removed(container_id, exc)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self.cleanup_containers()
        finally:
            if self.network_created:
                labels = json.loads(
                    self.command(
                        ["network", "inspect", "--format", "{{json .Labels}}", self.network]
                    )
                )
                if labels.get(OWNER_LABEL) == self.owner:
                    self.command(["network", "rm", self.network])


class OwnedDockerRunner(LocalDockerRunner):
    def __init__(self, resources: OwnedDockerResources) -> None:
        super().__init__()
        self.resources = resources

    def _ensure_network(self) -> None:
        # 当前用例已创建独占 bridge，不创建/复用产品默认网络。
        pass

    def run(
        self, tag: str, container_port: int, *, name: str, limits: RunLimits
    ) -> RunningContainer:
        limits = replace(
            limits,
            memory="128m",
            cpus="0.25",
            bind_host="127.0.0.1",
            hardening=(*limits.hardening, "--label", f"{OWNER_LABEL}={self.resources.owner}"),
        )
        return super().run(tag, container_port, name=self.resources.container, limits=limits)


class OwnedComposeRunner(LocalComposeRunner):
    def __init__(self, resources: OwnedDockerResources) -> None:
        super().__init__()
        self.resources = resources

    def down(self, compose_file: str, project: str) -> None:
        # 用例没有卷；不调用产品的 down -v，不删除镜像或任何 volume。
        self.resources.cleanup_containers()
