"""compose 支持：校验、改写、真起一组容器。

校验部分是纯函数，覆盖得密一些——这些错误要在**上传那一刻**就报给作者，
而不是等他传完、等审核、点启动才失败，那样一轮要半天。
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import yaml

from fde_asset.modules.app import bundle as bundle_module
from fde_asset.modules.app import compose as compose_module
from fde_asset.modules.app.compose import ComposeError, dump, validate_and_rewrite
from fde_asset.platform.runner.compose_runner import LocalComposeRunner

COMPOSE_RUNNER = LocalComposeRunner()
HAS_COMPOSE = COMPOSE_RUNNER.available()

BASIC = """
services:
  web:
    image: demo/web:1
    ports: ['8080:8080']
  db:
    image: postgres:16
    ports: ['5432:5432']
"""


def _rewrite(text: str = BASIC, **kwargs):
    params = {
        "app_name": "demo",
        "main_service": "web",
        "container_port": 8080,
        "host_port": 19000,
    }
    params.update(kwargs)
    return validate_and_rewrite(text, **params)


# —— 校验：错误要说人话，并指明哪个服务 ——


def test_main_service_must_be_declared() -> None:
    with pytest.raises(ComposeError) as exc:
        _rewrite(main_service="")
    assert "main_service" in str(exc.value)
    assert "web" in str(exc.value) and "db" in str(exc.value), "要告诉作者有哪些服务可选"


def test_main_service_must_exist() -> None:
    with pytest.raises(ComposeError) as exc:
        _rewrite(main_service="frontend")
    assert "没有这个服务" in str(exc.value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("privileged", True),
        ("pid", "host"),
        ("ipc", "host"),
        ("devices", ["/dev/sda:/dev/sda"]),
        ("cap_add", ["SYS_ADMIN"]),
    ],
)
def test_forbidden_keys_are_rejected_by_name(key, value) -> None:
    """禁止项直接报错并说明原因，不静默删除——偷偷改掉用户写的比拒绝更糟。"""
    document = yaml.safe_load(BASIC)
    document["services"]["web"][key] = value
    with pytest.raises(ComposeError) as exc:
        _rewrite(yaml.safe_dump(document))
    assert key in str(exc.value)
    assert "web" in str(exc.value)


def test_host_network_mode_is_rejected() -> None:
    document = yaml.safe_load(BASIC)
    document["services"]["web"]["network_mode"] = "host"
    with pytest.raises(ComposeError, match="network_mode"):
        _rewrite(yaml.safe_dump(document))


def test_build_without_image_tells_author_what_to_do() -> None:
    document = yaml.safe_load(BASIC)
    document["services"]["web"] = {"build": "."}
    with pytest.raises(ComposeError) as exc:
        _rewrite(yaml.safe_dump(document))
    assert "本地 build" in str(exc.value)


# —— 改写：只开主服务的口子，强制安全参数 ——


def test_only_the_main_service_is_published() -> None:
    plan = _rewrite()
    web = plan.document["services"]["web"]
    db = plan.document["services"]["db"]
    assert web["ports"] == ["127.0.0.1:19000:8080"]
    assert "ports" not in db, "数据库不该对外开端口"


def test_security_options_are_forced_on_every_service() -> None:
    plan = _rewrite()
    for name, service in plan.document["services"].items():
        assert service["security_opt"] == ["no-new-privileges:true"], name
        assert service["cap_drop"] == ["ALL"], name
        assert service["cap_add"] == ["NET_BIND_SERVICE"], name
        assert service["mem_limit"] and service["cpus"]


def test_project_name_avoids_collisions() -> None:
    assert _rewrite().project_name == "fde-demo-demo"


def test_dump_has_no_yaml_anchors() -> None:
    """生成的 compose 要能直接给人看，别带 &id001 这种锚点。"""
    assert "&id" not in dump(_rewrite())


# —— 挂载：自己的工作空间随便挂，越界拒绝 ——


def test_workspace_mount_is_allowed_and_made_absolute(tmp_path) -> None:
    workspace = tmp_path / "chen"
    (workspace / "data").mkdir(parents=True)
    document = yaml.safe_load(BASIC)
    document["services"]["web"]["volumes"] = ["./data:/app/data"]
    plan = _rewrite(yaml.safe_dump(document), workspace=workspace)
    volume = plan.document["services"]["web"]["volumes"][0]
    assert volume.startswith(str(workspace.resolve()))
    assert volume.endswith(":ro"), "默认只读"


def test_mount_outside_my_workspace_is_rejected(tmp_path) -> None:
    workspace = tmp_path / "chen"
    workspace.mkdir(parents=True)
    document = yaml.safe_load(BASIC)
    document["services"]["web"]["volumes"] = ["../wang/secret:/x"]
    with pytest.raises(ComposeError) as exc:
        _rewrite(yaml.safe_dump(document), workspace=workspace)
    assert "工作空间" in str(exc.value)


@pytest.mark.parametrize("path", ["/var/run/docker.sock", "/etc/shadow", "/proc", "/root"])
def test_dangerous_mounts_are_always_rejected(tmp_path, path) -> None:
    workspace = tmp_path / "chen"
    workspace.mkdir(parents=True)
    document = yaml.safe_load(BASIC)
    document["services"]["web"]["volumes"] = [f"{path}:/x"]
    with pytest.raises(ComposeError, match="不允许挂载"):
        _rewrite(yaml.safe_dump(document), workspace=workspace)


def test_named_volumes_need_no_approval(tmp_path) -> None:
    """容器之间共享数据用 named volume，不碰宿主机，默认放行。"""
    document = yaml.safe_load(BASIC)
    document["services"]["db"]["volumes"] = ["pgdata:/var/lib/postgresql/data"]
    plan = _rewrite(yaml.safe_dump(document), workspace=tmp_path)
    assert plan.document["services"]["db"]["volumes"] == ["pgdata:/var/lib/postgresql/data"]


# —— 上传包：认得出两种，挡得住路径穿越 ——


def test_plain_image_tar_is_recognised_as_single(tmp_path) -> None:
    tar_path = tmp_path / "image.tar"
    with tarfile.open(tar_path, "w") as archive:
        info = tarfile.TarInfo("manifest.json")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"[]"))
    assert bundle_module.unpack(tar_path, tmp_path / "w").kind == "single"


def test_bundle_without_images_tells_author_what_is_missing(tmp_path) -> None:
    tar_path = tmp_path / "bundle.tar"
    with tarfile.open(tar_path, "w") as archive:
        info = tarfile.TarInfo("docker-compose.yml")
        payload = BASIC.encode()
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(bundle_module.BundleError, match="images.tar"):
        bundle_module.unpack(tar_path, tmp_path / "w")


def test_path_traversal_in_bundle_is_blocked(tmp_path) -> None:
    tar_path = tmp_path / "evil.tar"
    with tarfile.open(tar_path, "w") as archive:
        for name in ("docker-compose.yml", "../../etc/evil"):
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(bundle_module.BundleError, match="越界"):
        bundle_module.unpack(tar_path, tmp_path / "w")


# —— 真跑一组容器 ——


@pytest.mark.skipif(not HAS_COMPOSE, reason="本机没有 docker compose")
def test_real_two_container_app_serves(tmp_path) -> None:
    """两个服务：一个对外的 web，一个只在内部网络的 sidecar。只有 web 能被访问到。"""
    work = Path(tempfile.mkdtemp())
    (work / "Dockerfile").write_text(
        "FROM busybox\n"
        'RUN mkdir -p /site && echo "<h1>compose demo ok</h1>" > /site/index.html\n'
        "WORKDIR /site\n"
        'CMD ["httpd", "-f", "-p", "8080", "-h", "/site"]\n',
        encoding="utf-8",
    )
    tag = "fde-compose-test:latest"
    build = subprocess.run(
        ["docker", "build", "-t", tag, str(work)], capture_output=True, text=True, timeout=600
    )
    if build.returncode != 0:
        pytest.skip(f"构建镜像失败：{build.stderr[-300:]}")

    text = f"""
services:
  web:
    image: {tag}
    ports: ['8080:8080']
  sidecar:
    image: {tag}
    command: ['sleep', '600']
"""
    plan = compose_module.validate_and_rewrite(
        text,
        app_name="composetest",
        main_service="web",
        container_port=8080,
        host_port=LocalComposeRunner.pick_port(),
    )
    compose_file = work / "fde-compose.yml"
    compose_file.write_text(dump(plan), encoding="utf-8")
    published = plan.document["services"]["web"]["ports"][0].split(":")[1]

    COMPOSE_RUNNER.up(str(compose_file), plan.project_name)
    try:
        assert COMPOSE_RUNNER.status(str(compose_file), plan.project_name) == "running"
        body = ""
        for _ in range(20):
            try:
                body = httpx.get(f"http://127.0.0.1:{published}", timeout=2).text
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        assert "compose demo ok" in body, "主服务该能访问到"
        logs = COMPOSE_RUNNER.logs(str(compose_file), plan.project_name)
        assert isinstance(logs, str)
    finally:
        COMPOSE_RUNNER.down(str(compose_file), plan.project_name)
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, timeout=120)

    assert COMPOSE_RUNNER.status(str(compose_file), plan.project_name) == "stopped"


@pytest.mark.parametrize(
    "source",
    ["../wang/secret", "./../../etc", "~/.ssh", "data/../../wang", "/tmp/elsewhere"],
)
def test_traversal_variants_are_all_rejected(tmp_path, source) -> None:
    """路径穿越有很多写法，逐一挡住——这一条曾经被 lstrip 洗白过。"""
    workspace = tmp_path / "chen"
    (workspace / "data").mkdir(parents=True)
    document = yaml.safe_load(BASIC)
    document["services"]["web"]["volumes"] = [f"{source}:/x"]
    with pytest.raises(ComposeError):
        _rewrite(yaml.safe_dump(document), workspace=workspace)
