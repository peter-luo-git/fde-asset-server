"""asset.yaml 模型、7 种 kind 的注册表与校验规则。

结构沿用规格附录 E.3（借鉴 Backstage）：apiVersion / kind / metadata / spec。
新增一种资产类型 = 注册一个 KindRule，不改索引流水线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

API_VERSION = "fde.asset/v1"
NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
OWNER_PATTERN = re.compile(r"^(department|user):[A-Za-z0-9._-]+$")

Scope = Literal["company", "department", "engagement"]
Lifecycle = Literal["draft", "experimental", "stable", "deprecated", "archived"]
Quality = Literal["bronze", "silver", "gold"]
Nature = Literal["fact", "rule", "judgment", "skill"]


class Applicability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suitable: str = ""
    notSuitable: str = ""


class AssetSource(BaseModel):
    model_config = ConfigDict(extra="allow")

    origin: str = ""  # 空 = 平台活动产生；legacy = 历史导入
    customerCode: str = ""
    engagementSlug: str = ""
    workItemId: str = ""
    sessionId: str = ""
    note: str = ""


class AssetRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "relatedTo"
    target: str


class AssetMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    title: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if not NAME_PATTERN.match(value) or len(value) > 64:
            raise ValueError("metadata.name 必须是 kebab-case 且不超过 64 字符")
        return value

    @field_validator("title")
    @classmethod
    def check_title(cls, value: str) -> str:
        if not value.strip() or len(value) > 50:
            raise ValueError("metadata.title 必填且不超过 50 字符")
        return value


class AssetSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    owner: str
    lifecycle: Lifecycle = "experimental"
    quality: Quality = "bronze"
    nature: Nature | str = ""
    industry: list[str] = Field(default_factory=list)
    stack: list[str] = Field(default_factory=list)
    scenario: list[str] = Field(default_factory=list)
    applicability: Applicability = Field(default_factory=Applicability)
    version: str = "0.1.0"
    replacedBy: str = ""
    restricted: bool = False
    effectiveDate: str = ""
    source: AssetSource = Field(default_factory=AssetSource)
    relations: list[AssetRelation] = Field(default_factory=list)

    @field_validator("owner")
    @classmethod
    def check_owner(cls, value: str) -> str:
        if not OWNER_PATTERN.match(value):
            raise ValueError("spec.owner 必须是 department:<代号> 或 user:<用户名>")
        return value

    @field_validator("version")
    @classmethod
    def check_version(cls, value: str) -> str:
        if not SEMVER_PATTERN.match(value):
            raise ValueError("spec.version 必须是语义化版本，例如 0.1.0")
        return value


class AssetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apiVersion: str
    kind: str
    metadata: AssetMetadata
    spec: AssetSpec

    @field_validator("apiVersion")
    @classmethod
    def check_api_version(cls, value: str) -> str:
        if value != API_VERSION:
            raise ValueError(f"apiVersion 必须是 {API_VERSION}")
        return value


@dataclass(frozen=True)
class KindRule:
    """一种资产类型的规则：主文件名、必需章节、专属必填字段、默认知识性质。"""

    kind: str
    label: str
    main_file: str
    directory: str
    required_sections: tuple[str, ...] = ()
    required_spec_fields: tuple[str, ...] = ()
    nature: str = ""
    requires_summary: bool = True
    requires_applicability: bool = True
    company_required_sections: tuple[str, ...] = ()
    knowledge: bool = False


KIND_RULES: dict[str, KindRule] = {
    "Rule": KindRule(
        kind="Rule",
        label="规范",
        main_file="",
        directory="rules",
        nature="rule",
        requires_summary=False,
        requires_applicability=False,
    ),
    "Sop": KindRule(
        kind="Sop",
        label="标准作业程序",
        main_file="SOP.md",
        directory="sops",
        required_sections=("结论与目的", "适用范围", "步骤"),
        required_spec_fields=("layer",),
        nature="rule",
    ),
    "Skill": KindRule(
        kind="Skill",
        label="技能",
        main_file="SKILL.md",
        directory="skills",
        required_sections=("适用场景", "步骤"),
        nature="skill",
    ),
    "Solution": KindRule(
        kind="Solution",
        label="方案",
        main_file="README.md",
        directory="knowledge/solutions",
        required_sections=("结论", "客户类型与场景", "方案概述", "适用边界"),
        nature="fact",
        knowledge=True,
    ),
    "Implementation": KindRule(
        kind="Implementation",
        label="实施",
        main_file="README.md",
        directory="knowledge/implementations",
        required_sections=("结论", "环境与前置条件", "实施步骤", "验证方法"),
        company_required_sections=("回滚预案",),
        nature="fact",
        knowledge=True,
    ),
    "Case": KindRule(
        kind="Case",
        label="问题",
        main_file="README.md",
        directory="knowledge/cases",
        required_sections=("结论", "现象", "根因", "解决方法"),
        required_spec_fields=("caseType",),
        nature="judgment",
        knowledge=True,
    ),
    "Experience": KindRule(
        kind="Experience",
        label="经验",
        main_file="README.md",
        directory="knowledge/experiences",
        required_sections=("结论", "做对了什么", "踩了什么坑", "如果重来"),
        nature="fact",
        knowledge=True,
    ),
    # 应用：项目本身就是资产。别的资产拿来读，应用拿来跑——核心是能演示。
    "Application": KindRule(
        kind="Application",
        label="应用",
        main_file="README.md",
        directory="applications",
        required_sections=("结论", "怎么跑起来", "演示入口", "已知限制"),
        required_spec_fields=("sourceType", "maturity"),
        nature="fact",
        knowledge=True,
    ),
}

#: 应用资产的枚举取值
APP_SOURCE_TYPES = {"fcp", "external"}
APP_MATURITY = {"poc", "pilot", "production"}
#: 演示地址的网络类型：公网能直接点开，内网要在公司网里，VPN 另说
APP_NETWORKS = {"internet", "intranet", "vpn", "local"}
APP_RUNTIME_TYPES = {"url", "static", "container", "compose"}

CASE_TYPES = {"fault", "faq", "pitfall", "rejection", "counterexample", "edge"}
SOP_LAYERS = {"L1", "L2", "L3"}
KIND_BY_SHORT = {
    "rule": "Rule",
    "sop": "Sop",
    "skill": "Skill",
    "solution": "Solution",
    "impl": "Implementation",
    "case": "Case",
    "exp": "Experience",
}
SHORT_BY_KIND = {v: k for k, v in KIND_BY_SHORT.items()}


@dataclass
class Finding:
    code: str
    message: str
    severity: str = "error"


@dataclass
class ParsedAsset:
    manifest: AssetManifest
    kind_rule: KindRule
    sections: dict[str, str] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)


def split_sections(markdown_text: str) -> dict[str, str]:
    """按二级标题切分正文，返回 {章节名: 正文}。"""
    sections: dict[str, str] = {}
    current = ""
    buffer: list[str] = []
    for line in markdown_text.splitlines():
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(buffer).strip()
            current = line[3:].strip()
            buffer = []
        elif current:
            buffer.append(line)
    if current:
        sections[current] = "\n".join(buffer).strip()
    return sections


def parse_manifest(raw_yaml: str) -> tuple[AssetManifest | None, list[Finding]]:
    try:
        data = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - 异常分支
        return None, [Finding("yaml_invalid", f"asset.yaml 解析失败：{exc}")]
    if not isinstance(data, dict):
        return None, [Finding("yaml_invalid", "asset.yaml 顶层必须是对象")]
    try:
        manifest = AssetManifest.model_validate(data)
    except ValidationError as exc:
        findings = [
            Finding("manifest_invalid", f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}")
            for err in exc.errors()
        ]
        return None, findings
    return manifest, []


def validate_asset(
    manifest: AssetManifest,
    *,
    main_text: str,
    scope: str,
    directory_name: str,
) -> ParsedAsset:
    """按 kind 校验主文件章节与专属字段，返回带 findings 的解析结果。"""
    rule = KIND_RULES.get(manifest.kind)
    if rule is None:
        empty = KindRule(kind=manifest.kind, label=manifest.kind, main_file="", directory="")
        return ParsedAsset(
            manifest, empty, {}, [Finding("kind_unsupported", f"不支持的 kind：{manifest.kind}")]
        )

    findings: list[Finding] = []
    if directory_name != manifest.metadata.name:
        findings.append(
            Finding(
                "name_mismatch",
                f"目录名 {directory_name} 与 metadata.name {manifest.metadata.name} 不一致",
            )
        )

    if rule.requires_summary and not manifest.metadata.summary.strip():
        findings.append(Finding("summary_missing", "metadata.summary（一句话结论）必填"))
    if len(manifest.metadata.summary) > 120:
        findings.append(Finding("summary_too_long", "metadata.summary 不超过 120 字"))
    if rule.requires_applicability:
        app = manifest.spec.applicability
        if not app.suitable.strip() or not app.notSuitable.strip():
            findings.append(
                Finding(
                    "applicability_missing", "spec.applicability 的 suitable 与 notSuitable 都必填"
                )
            )

    sections = split_sections(main_text)
    for section in rule.required_sections:
        if section not in sections or not sections[section].strip():
            findings.append(Finding("section_missing", f"缺少必需章节：{section}"))
    if scope == "company":
        for section in rule.company_required_sections:
            if section not in sections or not sections[section].strip():
                findings.append(
                    Finding("section_missing_company", f"公司级资产缺少必需章节：{section}")
                )

    extra = manifest.spec.model_extra or {}
    for field_name in rule.required_spec_fields:
        if not extra.get(field_name):
            findings.append(Finding("spec_field_missing", f"spec.{field_name} 必填"))
    if manifest.kind == "Case":
        case_type = extra.get("caseType", "")
        if case_type and case_type not in CASE_TYPES:
            findings.append(Finding("case_type_invalid", f"caseType 取值非法：{case_type}"))
        if case_type == "fault" and not extra.get("severity"):
            findings.append(Finding("severity_missing", "故障类 Case 需要 spec.severity"))
    if manifest.kind == "Application":
        source_type = extra.get("sourceType", "")
        if source_type and source_type not in APP_SOURCE_TYPES:
            findings.append(
                Finding("app_source_invalid", f"sourceType 只能是 fcp 或 external：{source_type}")
            )
        maturity = extra.get("maturity", "")
        if maturity and maturity not in APP_MATURITY:
            findings.append(Finding("app_maturity_invalid", f"maturity 取值非法：{maturity}"))
        demo = extra.get("demo") or {}
        runtime = extra.get("runtime") or {}
        network = demo.get("network", "")
        if demo.get("url") and network not in APP_NETWORKS:
            findings.append(
                Finding(
                    "app_network_missing",
                    "登记了演示地址就必须写 demo.network（internet / intranet / vpn / local），"
                    "否则别人只会点到一个打不开的链接",
                )
            )
        runtime_type = runtime.get("type", "")
        if runtime_type and runtime_type not in APP_RUNTIME_TYPES:
            findings.append(
                Finding("app_runtime_invalid", f"runtime.type 取值非法：{runtime_type}")
            )
        if not demo.get("url") and runtime_type not in ("container", "compose"):
            findings.append(
                Finding(
                    "app_no_entry",
                    "应用要么登记演示地址（A 档），要么声明容器化运行方式（B 档），不能两样都没有",
                )
            )

    if manifest.kind == "Sop":
        layer = extra.get("layer", "")
        if layer and layer not in SOP_LAYERS:
            findings.append(Finding("sop_layer_invalid", f"layer 取值非法：{layer}"))
        if layer in {"L2", "L3"} and not extra.get("extends"):
            findings.append(Finding("sop_extends_missing", f"{layer} 层 SOP 必须声明 spec.extends"))
        if layer == "L1" and extra.get("extends"):
            findings.append(Finding("sop_extends_unexpected", "L1 层 SOP 不应声明 extends"))
    if manifest.spec.lifecycle == "deprecated" and not manifest.spec.replacedBy:
        findings.append(Finding("replaced_by_missing", "废弃资产必须填写 spec.replacedBy"))

    return ParsedAsset(manifest, rule, sections, findings)


def kind_spec_fields(manifest: AssetManifest) -> dict[str, Any]:
    """返回 kind 专属字段（spec 里除通用字段以外的部分）。"""
    return dict(manifest.spec.model_extra or {})
