"""系统配置：存库、改完即生效，不用重启。

只收**运营参数**（大小上限、开关、间隔），不收密钥——密钥走环境变量，
免得写进数据库再被导出来。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from fde_asset.core.db import system_settings


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    kind: str  # int | bool | str
    default: Any
    unit: str = ""
    hint: str = ""


#: 页面上能改的全部配置项。加一项只要在这里加一行。
SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "app_image_size_limit_mb",
        "应用镜像大小上限",
        "int",
        5120,
        "MB",
        "同事上传的容器镜像超过这个大小会被拒绝",
    ),
    SettingSpec("attachment_size_limit_mb", "附件大小上限", "int", 50, "MB", "单个资产附件的上限"),
    SettingSpec(
        "knowledge_index_limit", "知识索引预算", "int", 30000, "字符", "注入会话的 INDEX.md 上限"
    ),
    SettingSpec("sop_budget", "SOP 步骤摘要预算", "int", 8192, "字符", "单步摘要写进提示词的上限"),
    SettingSpec(
        "rerank_enabled", "推荐启用内容理解重排", "bool", False, "", "关掉就退回关键词排序"
    ),
    SettingSpec("rerank_model", "重排模型", "str", "", "", "留空则用环境变量里的默认模型"),
    SettingSpec(
        "search_min_relevance",
        "语义相关度门槛",
        "int",
        5,
        "分（满分 100）",
        "按问题检索和精确推荐里，重排模型打分低于它的资产不显示；调高更准，调低更全",
    ),
    SettingSpec(
        "app_probe_interval_minutes", "应用探活间隔", "int", 10, "分钟", "多久探一次演示地址"
    ),
)

SPEC_BY_KEY = {spec.key: spec for spec in SETTING_SPECS}


class SettingError(ValueError):
    pass


def _cast(spec: SettingSpec, raw: str) -> Any:
    if spec.kind == "int":
        return int(raw)
    if spec.kind == "bool":
        return raw.lower() in ("1", "true", "yes", "on")
    return raw


def all_settings(engine: Engine) -> dict[str, Any]:
    """返回全部配置项的当前值（没存过就用默认值）。"""
    with engine.connect() as conn:
        stored = {row.key: row.value for row in conn.execute(select(system_settings))}
    values: dict[str, Any] = {}
    for spec in SETTING_SPECS:
        raw = stored.get(spec.key)
        values[spec.key] = _cast(spec, raw) if raw is not None else spec.default
    return values


def get(engine: Engine, key: str) -> Any:
    return all_settings(engine)[key]


def describe(engine: Engine) -> list[dict[str, Any]]:
    values = all_settings(engine)
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "kind": spec.kind,
            "unit": spec.unit,
            "hint": spec.hint,
            "default": spec.default,
            "value": values[spec.key],
        }
        for spec in SETTING_SPECS
    ]


def update(engine: Engine, changes: dict[str, Any], *, updated_by: str = "") -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        for key, value in changes.items():
            spec = SPEC_BY_KEY.get(key)
            if spec is None:
                raise SettingError(f"没有这个配置项：{key}")
            if spec.kind == "int":
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    raise SettingError(f"{spec.label} 要填整数") from None
                if number <= 0:
                    raise SettingError(f"{spec.label} 必须大于 0")
                text = str(number)
            elif spec.kind == "bool":
                text = "true" if value in (True, "true", "1", 1, "on") else "false"
            else:
                text = str(value)
            statement = sqlite_insert(system_settings).values(
                key=key, value=text, updated_by=updated_by, updated_at=now
            )
            conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[system_settings.c.key],
                    set_={"value": text, "updated_by": updated_by, "updated_at": now},
                )
            )
    return all_settings(engine)


def json_dump(engine: Engine) -> str:  # pragma: no cover - 调试用
    return json.dumps(all_settings(engine), ensure_ascii=False, indent=2)
