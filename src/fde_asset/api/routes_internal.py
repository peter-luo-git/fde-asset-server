"""给平台（fde-server）用的内部接口：Agent 能用哪些资产。

平台给 Agent 组装上下文、给"Agent 预设"列可选技能时，资产以这里为准。
只有持共享服务密钥的调用方能访问；入口网关不转发 `/internal/*`。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select

from fde_asset.api.deps import ServiceContext, get_context
from fde_asset.api.work_item_access import require_binding_project, require_work_item
from fde_asset.core.db import asset_audiences, assets
from fde_asset.modules.asset import agent_bundle as agent_bundle_module
from fde_asset.modules.asset import catalog
from fde_asset.modules.asset import sop_progress
from fde_asset.modules.asset import usage as usage_module
from fde_asset.modules.asset.visibility import audience_clause
from fde_asset.modules.asset.manifest import KIND_RULES
from fde_asset.platform.identity import PlatformUnavailable, PrincipalNotFound
from fde_asset.platform.repo.ports import RepoRef

# 内部接口不进对外的接口契约
logger = logging.getLogger(__name__)

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
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(assets)
            .where(
                assets.c.deleted_at.is_(None),
                assets.c.valid.is_(True),
                assets.c.kind.in_(AGENT_KINDS),
                # 全公司可见的，加上可见范围里点到他参与的项目的
                audience_clause(engagement_slugs=slugs),
            )
            .order_by(assets.c.kind, assets.c.name)
        ).all()
        reach: dict[str, list[tuple[str, str]]] = {}
        for item in conn.execute(
            select(asset_audiences).where(
                asset_audiences.c.asset_id.in_([row.asset_id for row in rows] or [""]),
                asset_audiences.c.audience.in_(AGENT_SCOPES),
            )
        ):
            reach.setdefault(item.asset_id, []).append((item.audience, item.code))

    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for row in rows:
        main_file = KIND_RULES[row.kind].main_file
        file_path = f"{row.path}/{main_file}"
        repo = RepoRef(name=row.repo)
        try:
            # 按索引时的那次提交读，内容和 commit_sha 才对得上
            content = context.repo_port.read_file(repo, file_path, ref=row.commit_sha or "")
        except FileNotFoundError:
            skipped.append({"asset_id": row.asset_id, "reason": "main_file_missing"})
            continue
        # 平台那边只分「公司的」和「某个项目的」两种：全公司可见就算公司的，
        # 否则算到他参与的、可见范围点到的那个项目头上
        audiences_here = reach.get(row.asset_id, [])
        via_company = ("company", "") in audiences_here
        via_slug = min(
            (
                code
                for audience, code in audiences_here
                if audience == "engagement" and code in slugs
            ),
            default="",
        )
        if not via_company and not via_slug:
            continue
        items.append(
            {
                "asset_id": row.asset_id,
                "scope": "company" if via_company else "engagement",
                "engagement_slug": "" if via_company else via_slug,
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


_push_lock = threading.Lock()
_push_state = {"running": False, "pending": False}


def _refresh_after_push(context: ServiceContext) -> None:
    """Gitea 说资产仓库有新推送：强制同步，库里还不是这个版本就重建索引。期间又来推送就跑完再补一次。"""
    from fde_asset.modules.asset.indexer import index_all

    while True:
        try:
            repo = RepoRef()
            sync = getattr(context.repo_port, "sync", None)
            if callable(sync):
                sync(repo, force=True)
            head = context.repo_port.get_head(repo)
            with context.engine.connect() as conn:
                indexed = conn.execute(
                    select(assets.c.asset_id).where(
                        assets.c.repo == repo.name, assets.c.commit_sha == head
                    )
                ).first()
            if indexed is None:
                index_all(
                    context.engine,
                    context.repo_port,
                    context.repos(),
                    text_limit=context.settings.index_text_limit,
                    owner_of=context.target_owner,
                    file_store=context.file_store,
                )
        except Exception:  # noqa: BLE001 - 失败了还有定时轮询兜底
            logger.exception("Gitea 推送回调触发的同步失败，等定时轮询兜底")
        with _push_lock:
            if not _push_state["pending"]:
                _push_state["running"] = False
                return
            _push_state["pending"] = False


@router.post("/gitea-push", status_code=202)
async def gitea_push(
    request: Request, context: ServiceContext = Depends(get_context)
) -> dict[str, Any]:
    """Gitea 的推送回调（B9）：资产仓库主分支有新提交就马上同步、重建索引，不用等轮询。

    用 X-Gitea-Signature 验签，密钥由服务密钥派生（settings.gitea_webhook_secret）。
    """
    secret = context.settings.gitea_webhook_secret()
    if not secret:
        raise HTTPException(status_code=404)
    body = await request.body()
    if len(body) > 1024 * 1024:
        raise HTTPException(status_code=413)
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("x-gitea-signature", ""), expected):
        raise HTTPException(status_code=403, detail="签名不对")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=422, detail="不是 JSON") from None
    name = ((payload.get("repository") or {}).get("name")) if isinstance(payload, dict) else None
    if name != RepoRef().name or not str(payload.get("ref", "")).startswith("refs/heads/"):
        return {"accepted": False}
    with _push_lock:
        if _push_state["running"]:
            _push_state["pending"] = True
            return {"accepted": True, "queued": True}
        _push_state["running"] = True
    threading.Thread(target=_refresh_after_push, args=(context,), daemon=True).start()
    return {"accepted": True}


def _sop_step_skills(context, principal, engagement_slug: str, work_item_ids: Any) -> list[str]:
    """这一轮涉及的任务当前流程步骤点名要用的技能（SOP「技能：」）。拿不到的任务跳过。"""
    if not isinstance(work_item_ids, list):
        return []
    names: list[str] = []
    for work_item_id in work_item_ids[:3]:
        if not isinstance(work_item_id, str):
            continue
        try:
            if require_work_item(context, work_item_id, principal) != engagement_slug:
                continue
        except HTTPException:
            continue
        with context.engine.connect() as conn:
            row = sop_progress.binding_row(conn, work_item_id)
        if row is None:
            continue
        step = sop_progress.agent_step(context.engine, row, "")
        for name in step.get("skills", []) if step.get("found") else []:
            if name not in names:
                names.append(name)
    return sorted(names)[:20]


@router.post("/agent-bundle", dependencies=[Depends(require_service_key)])
def agent_bundle(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
) -> dict[str, Any]:
    """某个用户在某个项目里启动 Agent 时该带的资产，拼成平台认的目录形状。

    给了 `revision` 就原样返回以前拼好的那一份（平台重试同一次运行时用）；
    没给就按现在的资产和关联关系新拼一份。
    带了 `known_revision`（平台上一轮拿到的版本号）且现在该带的还是这一份，只回
    `{"revision": ..., "unchanged": true}`，不带文件。身份和项目成员照常每次核对。
    `personal: true`：这一轮是这个人和 Agent 的私聊，个人必选资产也带上。
    """
    revision = str(payload.get("revision") or "")
    if revision:
        try:
            return agent_bundle_module.load_bundle(
                context.settings.bundle_dir, revision
            ).as_payload()
        except agent_bundle_module.BundleNotFound:
            raise HTTPException(status_code=404, detail="没有这个版本的资产包") from None

    username = str(payload.get("username") or "")
    engagement_slug = str(payload.get("engagement_slug") or "")
    if not username or not engagement_slug:
        raise HTTPException(status_code=422, detail="要给出用户和项目")
    try:
        principal = context.directory.resolve(username)
    except PrincipalNotFound:
        raise HTTPException(status_code=404, detail="没有这个用户") from None
    except PlatformUnavailable:
        raise HTTPException(status_code=503, detail="暂时无法向平台确认身份") from None
    membership = next(
        (m for m in principal.memberships if m.engagement_slug == engagement_slug), None
    )
    if membership is None and not principal.is_admin:
        raise HTTPException(status_code=403, detail="这个用户不在这个项目里")
    project = context.directory.engagements().get(engagement_slug, {})
    request = {
        # 项目归哪个部门、哪个客户，决定了部门级和客户级资产带不带
        "department_code": str(project.get("department_code", ""))
        or (membership.department_code if membership else ""),
        "customer_code": membership.customer_code if membership else "",
        "engagement_slug": engagement_slug,
        "target_type": "engagement",
        "target_id": engagement_slug,
        "agent_key": str(payload.get("agent_key") or ""),
    }
    if payload.get("personal") is True:
        # 平台说这一轮是这个人和 Agent 的私聊：带上他的个人必选资产。共享会话不带
        request["username"] = principal.user_id
    skills = _sop_step_skills(context, principal, engagement_slug, payload.get("work_item_ids"))
    if skills:
        request["sop_skills"] = ",".join(skills)
    known = str(payload.get("known_revision") or "")
    if known and agent_bundle_module.unchanged(context.engine, context.repo_port, known, **request):
        return {"revision": known, "unchanged": True}
    bundle = agent_bundle_module.build_bundle(
        context.engine,
        context.repo_port,
        bundle_root=context.settings.bundle_dir,
        file_store=context.file_store,
        **request,
    )
    return bundle.as_payload()


def _usage(
    payload: dict[str, Any], revision: str, asset_id: str, event: str
) -> usage_module.UsageInput:
    return usage_module.UsageInput(
        asset_id=asset_id,
        event=event,
        session_id=str(payload.get("session_id") or ""),
        engagement_slug=str(payload.get("engagement_slug") or ""),
        work_item_id=str(payload.get("work_item_id") or ""),
        snapshot_sha=revision,
        actor_type="agent",
        actor_id=str(payload.get("agent_key") or ""),
    )


@router.post("/agent-usage", dependencies=[Depends(require_service_key)])
def agent_usage(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
) -> dict[str, Any]:
    """平台报上来：某次 Agent 执行读了资产包里的哪些文件，交付摘要里又说参考了哪些。

    `paths`：平台放行读取时记下的路径，客观，记为「加载」或「读取」。
    `refs`：Agent 自己在交付摘要里写的引用，记为 cited；这是它自己说的，分开存，
    等级不按它算。
    平台只知道「哪个版本的资产包」，这里对回是哪份资产。同一次执行对同一份资产的
    同一种事件只记一次。
    """
    revision = str(payload.get("revision") or "")
    try:
        bundle = agent_bundle_module.load_bundle(context.settings.bundle_dir, revision)
    except agent_bundle_module.BundleNotFound:
        raise HTTPException(status_code=404, detail="没有这个版本的资产包") from None
    items: list[usage_module.UsageInput] = []
    unknown: list[str] = []
    for path in payload.get("paths") or []:
        found = agent_bundle_module.resolve_path(bundle, str(path))
        if found is None:
            unknown.append(str(path))
            continue
        items.append(_usage(payload, revision, *found))
    refs = payload.get("refs") or []
    titles: dict[str, str] = {}
    if refs:
        # 有的引用直接写标题（[[Solution/银行核心迁移方案]]），按包里资产的标题再对一次
        ids = [str(item.get("asset_id", "")) for item in bundle.assets]
        with context.engine.connect() as conn:
            titles = {
                row.asset_id: row.title
                for row in conn.execute(
                    select(assets.c.asset_id, assets.c.title).where(assets.c.asset_id.in_(ids))
                )
            }
    for ref in refs:
        asset_id = agent_bundle_module.resolve_ref(bundle, str(ref), titles)
        if asset_id is None:
            unknown.append(str(ref))
            continue
        items.append(_usage(payload, revision, asset_id, "cited"))
    result = usage_module.record_usages(context.engine, items) if items else {}
    return {**result, "ignored": unknown}


def _internal_work_item(
    context: ServiceContext, work_item_id: str, username: str, engagement_slug: str
) -> tuple[Any, str]:
    try:
        principal = context.directory.resolve(username)
    except PrincipalNotFound:
        raise HTTPException(status_code=404, detail="没有这个用户") from None
    except PlatformUnavailable:
        raise HTTPException(status_code=503, detail="暂时无法向平台确认身份") from None
    actual_slug = require_work_item(context, work_item_id, principal)
    if actual_slug != engagement_slug:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    return principal, actual_slug


@router.get("/work-items/{work_item_id}/sop-step", dependencies=[Depends(require_service_key)])
def work_item_sop_step(
    work_item_id: str,
    username: str = Query(min_length=1, max_length=80),
    engagement_slug: str = Query(min_length=1, max_length=120),
    context: ServiceContext = Depends(get_context),
) -> dict[str, Any]:
    """这个任务现在按哪条流程走到第几步，以及给 Agent 看的这一步的说明。

    平台每次让 Agent 执行这个任务时来取：当前这一步 + 全流程提纲 + 这一步要用的技能 +
    怎么回报检查点，另给结构化的检查点（fde-server 核对命令类、交付物类要用）。
    没绑、流程已下架、步骤不存在，都返回 found=false。
    """
    principal, actual_slug = _internal_work_item(context, work_item_id, username, engagement_slug)
    from fde_asset.api.routes_work_items import apply_default

    apply_default(context, work_item_id, actual_slug)
    with context.engine.connect() as conn:
        row = sop_progress.binding_row(conn, work_item_id)
        asset = sop_progress.usable_asset(conn, row.asset_id) if row is not None else None
    if row is None or asset is None:
        return {"found": False, "work_item_id": work_item_id}
    require_binding_project(row, actual_slug)
    if catalog.get_asset(context.engine, principal, row.asset_id) is None:
        return {"found": False, "work_item_id": work_item_id}
    summary = sop_progress.agent_step(context.engine, row, asset.title)
    if not summary.get("found"):
        return {"found": False, "work_item_id": work_item_id}
    return {
        **summary,
        "work_item_id": work_item_id,
        "asset_id": row.asset_id,
        "title": asset.title,
    }


@router.post("/work-items/{work_item_id}/sop-report", dependencies=[Depends(require_service_key)])
def work_item_sop_report(
    work_item_id: str,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
) -> dict[str, Any]:
    """Agent 收尾时逐条回报当前步骤的检查点（fde-server 已核对过命令类和交付物类）。

    payload：username、engagement_slug、step、results[{index, status, evidence, note,
    verified, verify_detail}]、agent、turn_id。规则见 sop_progress.judge_agent_claim。
    """
    username = str(payload.get("username") or "")
    engagement_slug = str(payload.get("engagement_slug") or "")
    results = payload.get("results")
    step = payload.get("step")
    if (
        not username
        or not engagement_slug
        or type(step) is not int
        or not isinstance(results, list)
        or not 1 <= len(results) <= 50
        or not all(isinstance(item, dict) for item in results)
    ):
        raise HTTPException(status_code=422, detail="回报格式不对")
    _, actual_slug = _internal_work_item(context, work_item_id, username, engagement_slug)
    with context.engine.connect() as conn:
        row = sop_progress.binding_row(conn, work_item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="这个任务没按流程执行")
    require_binding_project(row, actual_slug)
    try:
        outcome = sop_progress.apply_report(
            context.engine,
            work_item_id=work_item_id,
            step=step,
            results=results,
            agent=str(payload.get("agent") or "agent")[:80],
            turn_id=str(payload.get("turn_id") or "")[:64],
        )
    except sop_progress.SopProgressError as error:
        raise HTTPException(
            status_code=error.status,
            detail={"code": error.code, "message": str(error)} if error.code else str(error),
        ) from None
    # 按流程执行了一次，算这条流程用了一次（同一轮只计一次）
    usage_module.record_usages(
        context.engine,
        [
            usage_module.UsageInput(
                asset_id=row.asset_id,
                event="applied",
                session_id=str(payload.get("turn_id") or ""),
                engagement_slug=actual_slug,
                work_item_id=work_item_id,
                actor_type="agent",
                actor_id=str(payload.get("agent") or ""),
            )
        ],
    )
    return outcome


@router.get("/skill-deps/wanted", dependencies=[Depends(require_service_key)])
def skill_deps_wanted(context: ServiceContext = Depends(get_context)) -> dict[str, Any]:
    """已入库技能声明的依赖清单（去重）：worker 评审通过后就预装。"""
    from fde_asset.modules.asset import skill_deps

    return {"items": skill_deps.wanted(context.engine)}


@router.post("/skill-deps/status", dependencies=[Depends(require_service_key)])
def skill_deps_report(
    payload: dict[str, Any] = Body(...), context: ServiceContext = Depends(get_context)
) -> dict[str, Any]:
    """worker 回报依赖安装状态（就绪、失败原因、大小）。"""
    from fde_asset.modules.asset import skill_deps

    items = payload.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="items 要是列表")
    return {"saved": skill_deps.record_status(context.engine, items)}


@router.post("/skill-trials/claim", dependencies=[Depends(require_service_key)])
def claim_skill_trial(context: ServiceContext = Depends(get_context)) -> dict[str, Any]:
    """fde-server worker 领一个待试跑的技能：技能目录（base64）和要跑的命令。没有就是 {}。"""
    from fde_asset.modules.asset import skill_checks

    return skill_checks.claim(context.engine) or {}


@router.post("/skill-trials/{trial_id}/result", dependencies=[Depends(require_service_key)])
def finish_skill_trial(
    trial_id: int,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
) -> dict[str, Any]:
    from fde_asset.modules.asset import skill_checks

    try:
        return skill_checks.finish(context.engine, trial_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
