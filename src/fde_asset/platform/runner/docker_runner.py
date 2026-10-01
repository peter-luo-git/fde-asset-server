"""本机 Docker 实现。

安全边界（故意写死，不给调用方放宽的口子）：
- 不挂宿主机目录、不开特权、丢掉所有 capability、禁止提权
- 只发布声明的那个端口，默认绑 127.0.0.1
- 内存与 CPU 有上限
- 单独一个 bridge 网络，和宿主机其它容器隔开

出网限制（禁止容器访问外网）需要在宿主机上加网络策略，不是 docker run 参数能解决的，
已记在 ENV_BLOCKERS 里；在那之前，演示环境不要放任何真实凭据。
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
from pathlib import Path

from fde_asset.platform.runner.ports import (
    LoadedImage,
    RunLimits,
    RunnerError,
    RunningContainer,
)

NETWORK = "fde-demo"
_TAG = re.compile(r"Loaded image: (\S+)")


def _run(args: list[str], *, timeout: int = 600) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RunnerError((result.stderr or result.stdout).strip()[:500])
    return result.stdout.strip()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LocalDockerRunner:
    def __init__(self, docker: str = "docker") -> None:
        self.docker = docker

    def available(self) -> bool:
        try:
            _run([self.docker, "info", "--format", "{{.ServerVersion}}"], timeout=20)
            return True
        except Exception:  # noqa: BLE001 - 探测失败就当不可用
            return False

    def _ensure_network(self) -> None:
        existing = _run([self.docker, "network", "ls", "--format", "{{.Name}}"])
        if NETWORK not in existing.split():
            _run([self.docker, "network", "create", NETWORK])

    def load_image(self, tar_path: str) -> LoadedImage:
        path = Path(tar_path)
        if not path.exists():
            raise RunnerError(f"镜像文件不存在：{tar_path}")
        output = _run([self.docker, "load", "-i", str(path)])
        match = _TAG.search(output)
        if not match:
            raise RunnerError(f"没能从 docker load 的输出里认出镜像标签：{output[:200]}")
        return LoadedImage(tag=match.group(1), size_bytes=path.stat().st_size)

    def run(
        self, tag: str, container_port: int, *, name: str, limits: RunLimits
    ) -> RunningContainer:
        self._ensure_network()
        port = free_port()
        args = [
            self.docker,
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            NETWORK,
            "--memory",
            limits.memory,
            "--cpus",
            limits.cpus,
            *limits.hardening,
            "-p",
            f"{limits.bind_host}:{port}:{container_port}",
            tag,
        ]
        container_id = _run(args, timeout=120)
        return RunningContainer(container_id=container_id, host_port=port)

    def stop(self, container_id: str) -> None:
        try:
            _run([self.docker, "stop", "-t", "5", container_id], timeout=60)
        except RunnerError as exc:
            # 已经停掉的容器再停一次不算失败
            if "No such container" not in str(exc):
                raise

    def status(self, container_id: str) -> str:
        try:
            output = _run(
                [self.docker, "inspect", "--format", "{{json .State}}", container_id], timeout=30
            )
        except RunnerError:
            return "stopped"
        state = json.loads(output)
        if state.get("Running"):
            return "running"
        return "failed" if state.get("ExitCode") else "stopped"

    def logs(self, container_id: str, tail: int = 200) -> str:
        try:
            return _run([self.docker, "logs", "--tail", str(tail), container_id], timeout=60)
        except RunnerError as exc:
            return f"取日志失败：{exc}"
