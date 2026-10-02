"""compose 支持：解析、校验、改写。

设计目标是**让作者少写东西**——多数人已经有 docker-compose.yml，不该为平台再写一份。
所以：作者原样上传，平台负责改写成安全、不撞车、只对外开一个口子的样子。

改写做四件事：
1. 注入统一项目名，避免多个应用互相撞容器名
2. 只发布主服务的端口，数据库、缓存这些一律只在内部网络互通
3. 强制安全参数（禁止提权、丢 capability、资源上限）
4. 把工作空间挂载改写成宿主机上的绝对路径，并限制在作者自己的工作空间内

**禁止项直接报错，不静默删除**——偷偷改掉用户写的东西，比拒绝他更糟。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: 这些配置能让容器拿到宿主机控制权，一律拒绝，不接受申请
FORBIDDEN_SERVICE_KEYS = {
    "privileged": "privileged: true 等于把宿主机交出去",
    "pid": "pid: host 能看到并操作宿主机进程",
    "ipc": "ipc: host 能读宿主机共享内存",
    "userns_mode": "userns_mode 会绕开用户隔离",
    "devices": "devices 直接把宿主机设备给容器",
    "cap_add": "cap_add 会把丢掉的 capability 加回来",
}

FORBIDDEN_NETWORK_MODES = {"host", "none"}

#: 挂这些路径等于交出宿主机，不接受申请
FORBIDDEN_MOUNT_PREFIXES = ("/var/run/docker.sock", "/etc", "/proc", "/sys", "/root", "/boot")

SAFE_CAPS = ["NET_BIND_SERVICE"]


class ComposeError(ValueError):
    """校验不过时抛出，message 要能直接显示给作者看，并指明是哪个服务。"""


@dataclass
class ComposePlan:
    """改写后的结果。"""

    project_name: str
    main_service: str
    container_port: int
    document: dict[str, Any]
    images: list[str] = field(default_factory=list)
    mounts: list[str] = field(default_factory=list)


def parse(text: str) -> dict[str, Any]:
    try:
        document = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ComposeError(f"compose 文件不是合法的 YAML：{exc}") from None
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise ComposeError("compose 文件里没有 services")
    if not document["services"]:
        raise ComposeError("services 是空的")
    return document


def _check_mount(raw: str, *, service: str, workspace: Path | None) -> str:
    """挂载规则：named volume 随便用；宿主机路径必须落在自己的工作空间里。"""
    parts = raw.split(":")
    source = parts[0]
    # docker 的规矩：不含斜杠的才是 named volume（由 docker 管，不碰宿主机）。
    # 只看开头的 ./ 不够——"data/../../wang" 不以点开头，却是实打实的路径穿越。
    if "/" not in source and not source.startswith("~"):
        return raw

    if any(source.startswith(prefix) for prefix in FORBIDDEN_MOUNT_PREFIXES):
        raise ComposeError(f"服务 {service}：不允许挂载 {source}")
    if workspace is None:
        raise ComposeError(f"服务 {service}：挂了宿主机路径 {source}，但没有配置工作空间根目录")

    # 不能用 lstrip 去前缀：它按字符剥，"../wang" 会被剥成 "wang"，穿越就被洗白了
    candidate = Path(source).expanduser()
    expanded = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
    root = workspace.resolve()
    if root not in expanded.parents and expanded != root:
        raise ComposeError(
            f"服务 {service}：{source} 不在你自己的工作空间里。"
            f"只能挂 {root} 下面的目录，要挂别处得走申请评审"
        )
    rest = parts[1:] or ["/workspace"]
    if len(rest) == 1:
        rest.append("ro")  # 默认只读，要写在 compose 里显式写 rw
    return ":".join([str(expanded), *rest])


def validate_and_rewrite(
    text: str,
    *,
    app_name: str,
    main_service: str,
    container_port: int,
    workspace: Path | None = None,
    memory: str = "2g",
    cpus: str = "1",
    bind_host: str = "127.0.0.1",
    host_port: int = 0,
) -> ComposePlan:
    document = parse(text)
    services: dict[str, Any] = document["services"]

    if not main_service:
        raise ComposeError(
            "资产里要写 runtime.main_service：哪个服务是给人看的。"
            f"这个 compose 里有：{'、'.join(services)}"
        )
    if main_service not in services:
        raise ComposeError(
            f"main_service 写的是 {main_service}，但 compose 里没有这个服务。"
            f"可选：{'、'.join(services)}"
        )

    images: list[str] = []
    mounts: list[str] = []
    for name, service in services.items():
        if not isinstance(service, dict):
            raise ComposeError(f"服务 {name} 的配置不是对象")
        for key, why in FORBIDDEN_SERVICE_KEYS.items():
            if key in service:
                raise ComposeError(f"服务 {name} 用了 {key}：{why}")
        mode = service.get("network_mode")
        if mode in FORBIDDEN_NETWORK_MODES:
            raise ComposeError(f"服务 {name}：不允许 network_mode: {mode}")
        if service.get("build") and not service.get("image"):
            raise ComposeError(
                f"服务 {name} 只有 build 没有 image：演示环境不在平台上构建，"
                "请本地 build 之后给每个服务写明 image，再把镜像一起打包上传"
            )
        if service.get("image"):
            images.append(str(service["image"]))

        # 端口：只有主服务对外，其余服务之间走内部网络
        service.pop("ports", None)
        if name == main_service:
            published = host_port or container_port
            service["ports"] = [f"{bind_host}:{published}:{container_port}"]

        rewritten = []
        for item in service.get("volumes", []) or []:
            if isinstance(item, dict):
                raise ComposeError(f"服务 {name}：volumes 请用 '源:目标' 的短写法")
            value = _check_mount(str(item), service=name, workspace=workspace)
            rewritten.append(value)
            if value != str(item):
                mounts.append(value)
        if rewritten:
            service["volumes"] = rewritten

        # 安全参数强制加上
        service["security_opt"] = ["no-new-privileges:true"]
        service["cap_drop"] = ["ALL"]
        service["cap_add"] = list(SAFE_CAPS)  # 每个服务一份，避免 YAML 生成锚点引用
        service["restart"] = "no"
        service.setdefault("mem_limit", memory)
        service.setdefault("cpus", float(cpus))

    document["name"] = f"fde-demo-{app_name}"[:60]
    return ComposePlan(
        project_name=document["name"],
        main_service=main_service,
        container_port=container_port,
        document=document,
        images=images,
        mounts=mounts,
    )


def dump(plan: ComposePlan) -> str:
    return yaml.safe_dump(plan.document, allow_unicode=True, sort_keys=False)
