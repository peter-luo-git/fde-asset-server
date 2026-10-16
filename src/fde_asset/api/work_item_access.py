"""任务资产共享的授权边界：目录证明任务归属，存量绑定不能覆盖它。"""

from fastapi import HTTPException

from fde_asset.api.deps import ServiceContext
from fde_asset.platform.identity import Principal, WorkItemForbidden, WorkItemNotFound


def require_work_item(
    context: ServiceContext, work_item_id: str, principal: Principal, *, write: bool = False
) -> str:
    try:
        return context.directory.work_item_access(work_item_id, principal.user_id, write=write)
    except WorkItemNotFound:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问") from None
    except WorkItemForbidden:
        raise HTTPException(status_code=403, detail="无权修改该任务") from None


def require_binding_project(row, engagement_slug: str) -> None:
    # 旧版本没有保存项目：只在平台已核验真实任务归属后兼容读取，更新时补全。
    if row.engagement_slug and row.engagement_slug != engagement_slug:
        raise HTTPException(status_code=404, detail="任务关联不存在或无权访问")
