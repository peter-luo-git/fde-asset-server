"""应用资产：登记、网络类型、探活四态、进推荐。"""

from __future__ import annotations

from fde_asset.modules.app import health
from fde_asset.modules.asset.manifest import parse_manifest, validate_asset
from tests.conftest import as_user

APP_YAML = """apiVersion: fde.asset/v1
kind: Application
metadata:
  name: demo-app
  title: 演示应用
  summary: 一句话结论
spec:
  owner: user:chen
  applicability:
    suitable: 适用
    notSuitable: 不适用
  sourceType: {source_type}
  maturity: {maturity}
  runtime:
    type: {runtime_type}
  demo:
    network: {network}
    url: {url}
"""

SECTIONS = "## 结论\n有\n## 怎么跑起来\n有\n## 演示入口\n有\n## 已知限制\n有"


def _validate(**kwargs):
    defaults = {
        "source_type": "fcp",
        "maturity": "pilot",
        "runtime_type": "url",
        "network": "intranet",
        "url": "http://demo.internal/x",
    }
    manifest, findings = parse_manifest(APP_YAML.format(**{**defaults, **kwargs}))
    assert findings == [], findings
    return validate_asset(
        manifest, main_text=SECTIONS, scope="department", directory_name="demo-app"
    )


def test_registered_app_passes() -> None:
    assert _validate().valid


def test_demo_url_without_network_is_rejected() -> None:
    """登记了地址不写网络类型，别人只会点到一个打不开的链接。"""
    parsed = _validate(network="")
    assert "app_network_missing" in [f.code for f in parsed.findings]


def test_app_must_have_either_demo_or_container() -> None:
    """A 档登记地址，B 档容器化，两样都没有就不是能跑的东西。"""
    parsed = _validate(url="", runtime_type="url")
    assert "app_no_entry" in [f.code for f in parsed.findings]
    # B 档：没有演示地址但声明了容器化，可以
    assert _validate(url="", runtime_type="compose").valid


def test_enum_values_are_checked() -> None:
    assert "app_source_invalid" in [f.code for f in _validate(source_type="随便").findings]
    assert "app_maturity_invalid" in [f.code for f in _validate(maturity="随便").findings]


def test_seeded_apps_show_up_in_the_market(client) -> None:
    apps = as_user(client, "chen").get("/api/v1/apps").json()
    names = {item["name"] for item in apps["items"]}
    assert {"policy-import-console", "quick-labeler"} <= names
    console = next(item for item in apps["items"] if item["name"] == "policy-import-console")
    assert console["source_type"] == "fcp"
    assert console["demo"]["network"] == "intranet"
    labeler = next(item for item in apps["items"] if item["name"] == "quick-labeler")
    assert labeler["source_type"] == "external", "外部自研的项目也要收"


def test_probe_distinguishes_offline_from_unreachable(client, indexed) -> None:
    """探不到不等于挂了：公网应用探不通是离线，内网应用更可能是平台够不着。"""
    engine = indexed.engine

    def always_fail(url: str):
        raise OSError("connection refused")

    results = {r.asset_id: r for r in health.probe_all(engine, fetch=always_fail)}
    statuses = {r.status for r in results.values()}
    # 种子里有内网应用和本机应用
    assert "unreachable" in statuses, "内网应用探不通应标为够不着，不能说成离线"
    assert "skipped" in statuses, "本机应用不该去探"


def test_probe_records_online_and_last_seen(client, indexed) -> None:
    engine = indexed.engine
    results = health.probe_all(engine, fetch=lambda url: (200, ""))
    online = [r for r in results if r.status == "online"]
    assert online, "探通了就该是 online"
    recorded = health.health_map(engine)[online[0].asset_id]
    assert recorded["status"] == "online"
    assert recorded["last_online_at"]


def test_apps_appear_in_recommendations(client) -> None:
    """找"有没有人做过类似的东西"也该推应用。"""
    items = (
        as_user(client, "chen")
        .post(
            "/api/v1/recommend/compute",
            json={
                "target_type": "engagement",
                "target_id": "policy-import",
                "title": "保单导入控制台",
                "description": "需要一个盯导入进度和对账差异的面板",
                "industry": "insurance",
            },
        )
        .json()["items"]
    )
    assert any(item["kind"] == "Application" for item in items), [i["kind"] for i in items]
