"""给平台（fde-server）用的内部接口：Agent 能用哪些资产。

平台给 Agent 组装上下文、给"Agent 预设"列可选技能时，资产以这里为准。
只有持共享服务密钥的调用方能访问；入口网关不转发 `/internal/*`。
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import and_, or_, select

from fde_asset.api.deps import ServiceContext, get_context
from fde_asset.core.db import assets
from fde_asset.modules.asset.manifest import KIND_RULES
from fde_asset.platform.identity import PlatformUnavailable, PrincipalNotFound
from fde_asset.platform.repo.ports import RepoRef

# 内部接口不进对外的接口契约
router = APIRouter(prefix="/internal/v1", include_in_schema=False)

# Agent 那条链路目前只认公司级和项目级，类型只认这几种；其余的不给
AGENT_SCOPES = ("company", "engagement")
AGENT_KINDS = ("Skill", "Case", "Rule")
SERVICE_KEY_HEADER = "X-FDE-Service-Key"


def require_service_key(
    context: ServiceContext = Depends(get_context),
    provided: str | None = Header(default=None, alias=SERVICE_KEY_HEADER),
) -> None:
    expected = context.settings.server_service_key
    # 没配密钥就是没开这项对接；不拿空串去比
    if not expected or not hmac.compare_digest((provided or "").encode(), expected.encode()):
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/agent-catalog", dependencies=[Depends(require_service_key)])
def agent_catalog(
    username: str = Query(min_length=1, max_length=80),
    engagement_slug: str = Query(default="", max_length=120),
    context: ServiceContext = Depends(get_context),
) -> dict[str, Any]:
    """某个用户（在某个项目里）的 Agent 能用的资产：公司级的，加上他参与的项目的。

    指定了项目就只带那个项目的；他不是成员的项目一律不带。
    """
    try:
        principal = context.directory.resolve(username)
    except PrincipalNotFound:
        raise HTTPException(status_code=404, detail="没有这个用户") from None
    except PlatformUnavailable:
        raise HTTPException(status_code=503, detail="暂时无法向平台确认身份") from None

    slugs = principal.engagement_slugs
    if engagement_slug:
        slugs = slugs & {engagement_slug}
    visible = [assets.c.scope == "company"]
    if slugs:
        visible.append(
            and_(assets.c.scope == "engagement", assets.c.engagement_slug.in_(sorted(slugs)))
        )
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(assets)
            .where(
                assets.c.deleted_at.is_(None),
                assets.c.valid.is_(True),
                assets.c.scope.in_(AGENT_SCOPES),
                assets.c.kind.in_(AGENT_KINDS),
                or_(*visible),
            )
            .order_by(assets.c.kind, assets.c.name)
        ).all()

    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for row in rows:
        main_file = KIND_RULES[row.kind].main_file
        # 单文件资产（如规范）的路径就是那个文件；目录型资产读目录下的主文件
        file_path = row.path if row.path.endswith(".md") else f"{row.path}/{main_file}"
        repo = RepoRef(
            name=row.repo,
            scope=row.scope,
            department_code=row.department_code or "",
            customer_code=row.customer_code or "",
            engagement_slug=row.engagement_slug or "",
        )
        try:
            # 按索引时的那次提交读，内容和 commit_sha 才对得上
            content = context.repo_port.read_file(repo, file_path, ref=row.commit_sha or "")
        except FileNotFoundError:
            skipped.append({"asset_id": row.asset_id, "reason": "main_file_missing"})
            continue
        items.append(
            {
                "asset_id": row.asset_id,
                "scope": row.scope,
                "engagement_slug": row.engagement_slug or "",
                "kind": row.kind,
                "name": row.name,
                "version": row.version or "",
                "lifecycle": row.lifecycle,
                "title": row.title or row.name,
                "summary": row.summary or "",
                "repo": row.repo,
                "commit_sha": row.commit_sha or "",
                "path": row.path,
                "main_file": file_path.rsplit("/", 1)[-1],
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "byte_size": len(content),
            }
        )
    return {"username": principal.user_id, "items": items, "skipped": skipped}
