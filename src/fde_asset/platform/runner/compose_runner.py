"""多容器演示：docker compose 起停。

与单容器实现共用一套安全边界，差别只在"起的是一组容器"。compose 文件由
`modules/app/compose` 改写过，这里只负责执行。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fde_asset.platform.runner.docker_runner import NETWORK, _run, free_port
from fde_asset.platform.runner.ports import RunnerError


class LocalComposeRunner:
    def __init__(self, docker: str = "docker") -> None:
        self.docker = docker

    def available(self) -> bool:
        try:
            _run([self.docker, "compose", "version"], timeout=20)
            return True
        except Exception:  # noqa: BLE001
            return False

    def load_images(self, tar_path: str) -> list[str]:
        """把打包的镜像一次性 load 进来，返回认出的标签。"""
        path = Path(tar_path)
        if not path.exists():
            raise RunnerError(f"镜像包不存在：{tar_path}")
        output = _run([self.docker, "load", "-i", str(path)], timeout=900)
        tags = []
        for line in output.splitlines():
            if "Loaded image:" in line:
                tags.append(line.split("Loaded image:", 1)[1].strip())
        return tags

    def up(self, compose_file: str, project: str) -> str:
        """起一组容器。失败时把 compose 的报错原样带出去，作者才知道哪错了。"""
        try:
            _run(
                [self.docker, "compose", "-p", project, "-f", compose_file, "up", "-d"],
                timeout=600,
            )
        except RunnerError as exc:
            # 起失败也要把残留清掉，不然下次起会撞名字
            self.down(compose_file, project)
            raise RunnerError(f"compose 启动失败：{exc}") from None
        return project

    def down(self, compose_file: str, project: str) -> None:
        try:
            _run(
                [
                    self.docker,
                    "compose",
                    "-p",
                    project,
                    "-f",
                    compose_file,
                    "down",
                    "-v",
                    "--remove-orphans",
                ],
                timeout=300,
            )
        except RunnerError:
            # 本来就没起来的时候 down 会报错，忽略
            pass

    def status(self, compose_file: str, project: str) -> str:
        try:
            output = _run(
                [
                    self.docker,
                    "compose",
                    "-p",
                    project,
                    "-f",
                    compose_file,
                    "ps",
                    "--format",
                    "json",
                ],
                timeout=60,
            )
        except RunnerError:
            return "stopped"
        running = 0
        total = 0
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            if str(item.get("State", "")).lower() == "running":
                running += 1
        if total == 0:
            return "stopped"
        return "running" if running == total else "failed"

    def logs(self, compose_file: str, project: str, tail: int = 200) -> str:
        try:
            return _run(
                [
                    self.docker,
                    "compose",
                    "-p",
                    project,
                    "-f",
                    compose_file,
                    "logs",
                    "--tail",
                    str(tail),
                ],
                timeout=120,
            )
        except RunnerError as exc:
            return f"取日志失败：{exc}"

    @staticmethod
    def pick_port() -> int:
        return free_port()

    @staticmethod
    def network_name() -> str:
        return NETWORK


def compose_available(docker: str = "docker") -> bool:  # pragma: no cover - 环境探测
    return subprocess.run([docker, "compose", "version"], capture_output=True).returncode == 0
