"""应用容器化演示：上传镜像 → 审核 → 手动启停。

按 2026-10-01 的确认：**应用所有人自己构建镜像并上传**，管理员审核通过后
才允许运行；不做自动回收，启停都由人点按钮。页面上给出访问方式和接入说明。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from fde_asset.core.db import app_deployments, assets, record_event
from fde_asset.platform.blobs import BlobStore, safe_name
from fde_asset.platform.runner.ports import DemoRunner, RunLimits, RunnerError

#: 镜像统一存在这个 scope 下，和草稿附件分开
IMAGE_SCOPE = "app-images"


class DeployError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _asset(engine: Engine, asset_id: str):
    with engine.connect() as conn:
        row = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
    if row is None or row.kind != "Application":
        raise DeployError("只有应用类资产可以上传镜像")
    return row


def _spec(row: Any) -> dict[str, Any]:
    return json.loads(row.kind_spec_json or "{}")


def container_port_of(row: Any) -> int:
    runtime = _spec(row).get("runtime") or {}
    ports = runtime.get("ports") or []
    if not ports:
        raise DeployError("资产里没声明 runtime.ports，不知道该发布哪个端口")
    return int(ports[0])


def _upsert(engine: Engine, asset_id: str, values: dict[str, Any]) -> dict[str, Any]:
    with engine.begin() as conn:
        statement = sqlite_insert(app_deployments).values(asset_id=asset_id, **values)
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=[app_deployments.c.asset_id], set_=values
            )
        )
    return get(engine, asset_id)


def get(engine: Engine, asset_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(
            select(app_deployments).where(app_deployments.c.asset_id == asset_id)
        ).first()
    if row is None:
        return {
            "asset_id": asset_id,
            "review_status": "none",
            "run_status": "stopped",
            "image_file": "",
            "access_url": "",
        }
    data = dict(row._mapping)
    for key in ("uploaded_at", "reviewed_at", "started_at"):
        data[key] = data[key].isoformat() if data[key] else ""
    return data


def upload_image(
    engine: Engine,
    *,
    asset_id: str,
    filename: str,
    content: bytes,
    store: BlobStore,
    uploaded_by: str,
    size_limit_bytes: int,
) -> dict[str, Any]:
    """所有人自己 `docker save` 出来的 tar 传上来，等审核。"""
    _asset(engine, asset_id)
    if len(content) > size_limit_bytes:
        limit_gb = size_limit_bytes / 1024 / 1024 / 1024
        raise DeployError(f"镜像超过 {limit_gb:.1f}GB 上限，先瘦身再传")
    name = f"{asset_id}-{safe_name(filename)}"
    store.put(IMAGE_SCOPE, name, content)
    result = _upsert(
        engine,
        asset_id,
        {
            "image_file": name,
            "image_bytes": len(content),
            "uploaded_by": uploaded_by,
            "uploaded_at": _now(),
            # 重新上传要重新审核，否则审核就白做了
            "review_status": "pending",
            "reviewed_by": "",
            "reviewed_at": None,
            "review_note": "",
            "run_status": "stopped",
            "container_id": "",
            "access_url": "",
            "last_error": "",
        },
    )
    with engine.begin() as conn:
        record_event(
            conn,
            "app.image_uploaded",
            {"asset_id": asset_id, "uploaded_by": uploaded_by, "bytes": len(content)},
        )
    return result


def review(
    engine: Engine, *, asset_id: str, approve: bool, reviewed_by: str, note: str = ""
) -> dict[str, Any]:
    current = get(engine, asset_id)
    if not current.get("image_file"):
        raise DeployError("还没有上传镜像")
    if not approve and not note.strip():
        raise DeployError("驳回要写原因")
    result = _upsert(
        engine,
        asset_id,
        {
            "review_status": "approved" if approve else "rejected",
            "reviewed_by": reviewed_by,
            "reviewed_at": _now(),
            "review_note": note,
        },
    )
    with engine.begin() as conn:
        record_event(
            conn,
            "app.image_reviewed",
            {"asset_id": asset_id, "approve": approve, "reviewed_by": reviewed_by},
        )
    return result


def start(
    engine: Engine,
    *,
    asset_id: str,
    runner: DemoRunner,
    store: BlobStore,
    started_by: str,
    limits: RunLimits | None = None,
    public_host: str = "127.0.0.1",
) -> dict[str, Any]:
    row = _asset(engine, asset_id)
    current = get(engine, asset_id)
    if current.get("review_status") != "approved":
        raise DeployError("镜像还没通过审核，不能运行")
    if current.get("run_status") == "running":
        return current

    tar_path = store.path(IMAGE_SCOPE, current["image_file"])
    port = container_port_of(row)
    try:
        image = runner.load_image(str(tar_path))
        container = runner.run(
            image.tag,
            port,
            name=f"fde-demo-{row.name}"[:60],
            limits=limits or RunLimits(),
        )
    except RunnerError as exc:
        _upsert(engine, asset_id, {"run_status": "failed", "last_error": str(exc)[:500]})
        raise DeployError(f"启动失败：{exc}") from None

    url = f"http://{public_host}:{container.host_port}"
    result = _upsert(
        engine,
        asset_id,
        {
            "image_tag": image.tag,
            "run_status": "running",
            "container_id": container.container_id,
            "host_port": container.host_port,
            "access_url": url,
            "started_by": started_by,
            "started_at": _now(),
            "last_error": "",
        },
    )
    with engine.begin() as conn:
        record_event(
            conn, "app.started", {"asset_id": asset_id, "started_by": started_by, "url": url}
        )
    return result


def stop(engine: Engine, *, asset_id: str, runner: DemoRunner, stopped_by: str) -> dict[str, Any]:
    current = get(engine, asset_id)
    if current.get("container_id"):
        runner.stop(current["container_id"])
    result = _upsert(
        engine,
        asset_id,
        {"run_status": "stopped", "container_id": "", "access_url": "", "host_port": 0},
    )
    with engine.begin() as conn:
        record_event(conn, "app.stopped", {"asset_id": asset_id, "by": stopped_by})
    return result


def logs(engine: Engine, *, asset_id: str, runner: DemoRunner, tail: int = 200) -> str:
    current = get(engine, asset_id)
    if not current.get("container_id"):
        return "容器没在跑，没有日志"
    return runner.logs(current["container_id"], tail=tail)
