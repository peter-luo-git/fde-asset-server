"""使用识别、复用计量、护照与复用通知。"""

from __future__ import annotations

from fde_asset.modules.asset import catalog, usage
from fde_asset.platform.identity import Membership, Principal

CHEN = Principal(
    "chen",
    department_code="data-intel",
    memberships=(Membership("policy-import", "finance"), Membership("core-migration", "finance")),
)


def _asset_id(context, name: str) -> str:
    result = catalog.search(context.engine, CHEN, catalog.CatalogQuery(q=name, limit=10))
    return next(item["asset_id"] for item in result["items"] if item["name"] == name)


def test_loaded_and_read_events(indexed) -> None:
    result = usage.record_usages(
        indexed.engine,
        [
            usage.UsageInput(
                ref="skill/insurance-policy-import",
                event="loaded",
                session_id="s1",
                engagement_slug="core-migration",
            ),
            usage.UsageInput(
                ref="case/oracle-to-pg-sequence-gap",
                event="read",
                session_id="s1",
                engagement_slug="core-migration",
            ),
        ],
    )
    assert result["accepted"] == 2 and not result["unknown"]


def test_same_session_same_event_is_idempotent(indexed) -> None:
    payload = [
        usage.UsageInput(
            ref="skill/insurance-policy-import",
            event="loaded",
            session_id="s1",
            engagement_slug="core-migration",
        )
    ]
    usage.record_usages(indexed.engine, payload)
    assert usage.record_usages(indexed.engine, payload)["duplicated"] == 1


def test_unknown_reference_does_not_block(indexed) -> None:
    result = usage.record_usages(
        indexed.engine, [usage.UsageInput(ref="case/does-not-exist", event="read", session_id="s2")]
    )
    assert result["accepted"] == 0 and result["unknown"][0]["reason"] == "asset_not_found"


def test_unsupported_event_rejected(indexed) -> None:
    result = usage.record_usages(
        indexed.engine,
        [usage.UsageInput(ref="skill/insurance-policy-import", event="viewed", session_id="s3")],
    )
    assert result["unknown"][0]["reason"] == "event_unsupported"


def test_project_level_asset_wins_over_company(indexed) -> None:
    usage.record_usages(
        indexed.engine,
        [
            usage.UsageInput(
                ref="case/import-over-10k-rows-timeout",
                event="read",
                session_id="s4",
                engagement_slug="policy-import",
            )
        ],
    )
    asset_id = _asset_id(indexed, "import-over-10k-rows-timeout")
    passport = catalog.passport(indexed.engine, CHEN, asset_id)
    assert passport["stamps"][0]["engagement_slug"] == "policy-import"


def test_reuse_notification_only_on_first_cross_project_delivery(indexed) -> None:
    first = usage.record_usages(
        indexed.engine,
        [
            usage.UsageInput(
                ref="skill/insurance-policy-import",
                event="loaded",
                session_id="s5",
                engagement_slug="core-migration",
                work_item_id="301",
                delivered_pr=True,
            )
        ],
    )
    assert len(first["notifications"]) == 1
    second = usage.record_usages(
        indexed.engine,
        [
            usage.UsageInput(
                ref="skill/insurance-policy-import",
                event="loaded",
                session_id="s6",
                engagement_slug="core-migration",
                work_item_id="302",
                delivered_pr=True,
            )
        ],
    )
    assert not second["notifications"]


def test_no_notification_without_delivery(indexed) -> None:
    result = usage.record_usages(
        indexed.engine,
        [
            usage.UsageInput(
                ref="skill/insurance-policy-import",
                event="loaded",
                session_id="s7",
                engagement_slug="core-migration",
                delivered_pr=False,
            )
        ],
    )
    assert not result["notifications"]


def test_source_project_usage_is_not_reuse(indexed) -> None:
    usage.record_usages(
        indexed.engine,
        [
            usage.UsageInput(
                ref="skill/insurance-policy-import",
                event="loaded",
                session_id="s8",
                engagement_slug="policy-import",
                delivered_pr=True,
            )
        ],
    )
    asset_id = _asset_id(indexed, "insurance-policy-import")
    asset = catalog.get_asset(indexed.engine, CHEN, asset_id)
    assert asset["reuse_engagement_count"] == 0


def test_reference_counts_as_reuse(indexed) -> None:
    asset_id = _asset_id(indexed, "insurance-policy-import")
    assert usage.record_reference(
        indexed.engine,
        asset_id=asset_id,
        source_type="message",
        source_id="m1",
        engagement_slug="core-migration",
    )
    asset = catalog.get_asset(indexed.engine, CHEN, asset_id)
    assert asset["reuse_engagement_count"] == 1


def test_reference_is_idempotent(indexed) -> None:
    asset_id = _asset_id(indexed, "insurance-policy-import")
    usage.record_reference(indexed.engine, asset_id=asset_id, source_type="message", source_id="m2")
    assert not usage.record_reference(
        indexed.engine, asset_id=asset_id, source_type="message", source_id="m2"
    )


def test_passport_counts_delivered_work_items(indexed) -> None:
    usage.record_usages(
        indexed.engine,
        [
            usage.UsageInput(
                ref="skill/insurance-policy-import",
                event="loaded",
                session_id="s9",
                engagement_slug="core-migration",
                work_item_id="401",
                delivered_pr=True,
            )
        ],
    )
    asset_id = _asset_id(indexed, "insurance-policy-import")
    passport = catalog.passport(indexed.engine, CHEN, asset_id)
    assert passport["stats"]["helped_work_items"] == 1
    assert passport["birthplace"]["engagement_slug"] == "policy-import"
