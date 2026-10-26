"""任务资产关联的公开请求契约；工作项真实归属由平台核验。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class WorkItemAssetLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=64)
    role: Literal["reference", "sop_step"] = "reference"
    engagement_slug: str = Field(
        default="", max_length=120, description="可省略；如提供，必须与平台核验的任务所属项目一致。"
    )
    sop_step: StrictInt | None = Field(
        default=None, description="SOP 关联必须填写实际存在的整数步骤。"
    )
    force: bool = Field(
        default=False, description="往后推进时当前步骤还有没通过的检查点，确认仍然进入就传 true。"
    )


class SopCheckDecision(BaseModel):
    """人对一个检查点的决定：确认通过、退回（不通过）、清空重来。"""

    model_config = ConfigDict(extra="forbid")

    step: StrictInt
    index: StrictInt = Field(ge=0, description="检查点在这一步里的序号，从 0 开始。")
    decision: Literal["confirm", "reject", "reset"]
    note: str = Field(default="", max_length=2000)
