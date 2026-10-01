"""应用容器化演示的接口：上传镜像、审核、启停、看日志。"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.modules.app import deploy
from fde_asset.modules.asset import catalog
from fde_asset.platform import settings_store
from fde_asset.platform.blobs import BlobError
from fde_asset.platform.identity import Principal
from fde_asset.platform.runner.docker_runner import LocalDockerRunner
from fde_asset.platform.runner.ports import RunLimits

router = APIRouter(prefix="/api/v1", tags=["apps"])


def _runner(context: ServiceContext):
    runner = getattr(context, "demo_runner", None)
    return runner or LocalDockerRunner()


def _owned(context: ServiceContext, principal: Principal, asset_id: str) -> dict[str, Any]:
    asset = catalog.get_asset(context.engine, principal, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="应用不存在或无权访问")
    if asset["kind"] != "Application":
        raise HTTPException(status_code=422, detail="只有应用类资产可以部署")
    return asset


def _may_manage(principal: Principal, asset: dict[str, Any]) -> bool:
    if principal.is_admin:
        return True
    if asset["owner_kind"] == "user" and asset["owner_value"] == principal.user_id:
        return True
    return asset["owner_kind"] == "department" and asset["owner_value"] == principal.department_code


@router.get("/apps/{asset_id}/deployment")
def read_deployment(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _owned(context, principal, asset_id)
    return deploy.get(context.engine, asset_id)


@router.post("/apps/{asset_id}/image")
def upload_image(
    asset_id: str,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """所有人自己 `docker save -o app.tar <镜像>` 之后传上来。"""
    asset = _owned(context, principal, asset_id)
    if not _may_manage(principal, asset):
        raise HTTPException(status_code=403, detail="只有应用负责人或管理员能传镜像")
    limit_mb = int(settings_store.get(context.engine, "app_image_size_limit_mb"))
    try:
        return deploy.upload_image(
            context.engine,
            asset_id=asset_id,
            filename=payload.get("filename", "image.tar"),
            content=base64.b64decode(payload["content_base64"]),
            store=context.blob_store,
            uploaded_by=principal.user_id,
            size_limit_bytes=limit_mb * 1024 * 1024,
        )
    except (deploy.DeployError, BlobError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/apps/{asset_id}/review")
def review_image(
    asset_id: str,
    payload: dict[str, Any] = Body(default={}),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """审核：通过了才允许运行。按确认，现阶段由管理员一个人审。"""
    _owned(context, principal, asset_id)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="只有平台管理员能审核镜像")
    try:
        return deploy.review(
            context.engine,
            asset_id=asset_id,
            approve=bool(payload.get("approve", True)),
            reviewed_by=principal.user_id,
            note=payload.get("note", ""),
        )
    except deploy.DeployError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/apps/{asset_id}/start")
def start_app(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """任何看得到这个应用的人都能启动——互相演示才是目的。"""
    _owned(context, principal, asset_id)
    try:
        return deploy.start(
            context.engine,
            asset_id=asset_id,
            runner=_runner(context),
            store=context.blob_store,
            started_by=principal.user_id,
            limits=RunLimits(bind_host=context.settings.demo_bind_host),
            public_host=context.settings.demo_public_host,
        )
    except deploy.DeployError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/apps/{asset_id}/stop")
def stop_app(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    asset = _owned(context, principal, asset_id)
    if not _may_manage(principal, asset):
        raise HTTPException(status_code=403, detail="只有应用负责人或管理员能停")
    return deploy.stop(
        context.engine, asset_id=asset_id, runner=_runner(context), stopped_by=principal.user_id
    )


@router.get("/apps/{asset_id}/logs")
def app_logs(
    asset_id: str,
    tail: int = 200,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _owned(context, principal, asset_id)
    return {
        "logs": deploy.logs(context.engine, asset_id=asset_id, runner=_runner(context), tail=tail)
    }
