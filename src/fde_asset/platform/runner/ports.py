"""演示运行器接口。

宿主机是部署参数：开发阶段是本机或某台指定机器，正式版本是内网服务器。
资产服务只认这个接口，换机器、换实现（Docker / Podman / 远程 Runner）都不改业务代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class RunnerError(RuntimeError):
    pass


@dataclass
class LoadedImage:
    tag: str
    size_bytes: int


@dataclass
class RunningContainer:
    container_id: str
    host_port: int


@dataclass
class RunLimits:
    """容器安全边界。松了的话演示环境会变成横向移动的跳板。"""

    memory: str = "2g"
    cpus: str = "1"
    #: 绑定地址：默认只听本机，要远程演示时由部署参数改成 0.0.0.0
    bind_host: str = "127.0.0.1"
    #: 额外的 docker run 参数，默认已经关掉提权、丢掉所有 capability
    hardening: tuple[str, ...] = field(
        default_factory=lambda: (
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/tmp",
        )
    )


class DemoRunner(Protocol):
    def load_image(self, tar_path: str) -> LoadedImage: ...

    def run(
        self, tag: str, container_port: int, *, name: str, limits: RunLimits
    ) -> RunningContainer: ...

    def stop(self, container_id: str) -> None: ...

    def status(self, container_id: str) -> str: ...

    def logs(self, container_id: str, tail: int = 200) -> str: ...
