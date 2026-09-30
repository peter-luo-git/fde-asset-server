"""沉淀线索：八条规则的命中与不命中、打分、忽略、可见范围。"""

from __future__ import annotations

from fde_asset.modules.leads.rules import EngagementFact, Lead, WorkItemFact, detect, refresh


class Source:
    def __init__(self, work_items=(), engagements=()) -> None:
        self._work_items = list(work_items)
        self._engagements = list(engagements)

    def work_items(self):
        return self._work_items

    def engagements(self):
        return self._engagements


def rules_of(leads: list[Lead]) -> set[str]:
    return {lead.rule for lead in leads}


def test_l1_done_without_asset() -> None:
    hit = Source(
        [
            WorkItemFact(
                "1",
                "导入优化",
                "p",
                "chen",
                delivered_pr=True,
                changed_lines=120,
                closed_at="2026-09-28T00:00:00Z",
            )
        ]
    )
    miss = Source(
        [
            WorkItemFact(
                "2",
                "导入优化",
                "p",
                "chen",
                delivered_pr=True,
                changed_lines=120,
                produced_asset=True,
            )
        ]
    )
    assert "L1" in rules_of(detect(hit)) and "L1" not in rules_of(detect(miss))


def test_l2_high_severity_issue_without_case() -> None:
    hit = Source(
        [WorkItemFact("3", "超时", "p", "chen", kind="issue", severity="S2", status="closed")]
    )
    miss = Source(
        [WorkItemFact("4", "文案错别字", "p", "chen", kind="issue", severity="S4", status="closed")]
    )
    assert "L2" in rules_of(detect(hit)) and "L2" not in rules_of(detect(miss))


def test_l3_three_similar_titles() -> None:
    hit = Source([WorkItemFact(str(i), "保单导入字段映射", "p", "chen") for i in range(3)])
    miss = Source(
        [WorkItemFact("1", "保单导入", "p", "chen"), WorkItemFact("2", "客户培训", "p", "chen")]
    )
    assert "L3" in rules_of(detect(hit)) and "L3" not in rules_of(detect(miss))


def test_l4_many_prompt_appends() -> None:
    assert "L4" in rules_of(detect(Source([WorkItemFact("1", "t", "p", "chen", prompt_appends=4)])))
    assert "L4" not in rules_of(
        detect(Source([WorkItemFact("1", "t", "p", "chen", prompt_appends=1)]))
    )


def test_l5_repeated_changes_requested() -> None:
    assert "L5" in rules_of(
        detect(Source([WorkItemFact("1", "t", "p", "chen", review_changes_requested=2)]))
    )
    assert "L5" not in rules_of(
        detect(Source([WorkItemFact("1", "t", "p", "chen", review_changes_requested=1)]))
    )


def test_l6_same_error_twice() -> None:
    hit = Source(
        [
            WorkItemFact("1", "a", "p", "chen", error_signature="ORA-01722"),
            WorkItemFact("2", "b", "p", "chen", error_signature="ORA-01722"),
        ]
    )
    miss = Source([WorkItemFact("1", "a", "p", "chen", error_signature="ORA-01722")])
    assert "L6" in rules_of(detect(hit)) and "L6" not in rules_of(detect(miss))


def test_l7_pattern_words() -> None:
    hit = Source([WorkItemFact("1", "t", "p", "chen", delivery_summary="注意必须先备份")])
    miss = Source([WorkItemFact("1", "t", "p", "chen", delivery_summary="完成了导入")])
    assert "L7" in rules_of(detect(hit)) and "L7" not in rules_of(detect(miss))


def test_l8_accepted_engagement() -> None:
    hit = Source(
        engagements=[EngagementFact("p", "wang", accepted=True, accepted_at="2026-09-28T00:00:00Z")]
    )
    miss = Source(engagements=[EngagementFact("p", "wang", accepted=False)])
    assert "L8" in rules_of(detect(hit)) and "L8" not in rules_of(detect(miss))


def test_score_decays_with_age() -> None:
    recent = detect(
        Source(
            [
                WorkItemFact(
                    "1",
                    "t",
                    "p",
                    "chen",
                    delivered_pr=True,
                    changed_lines=99,
                    closed_at="2026-09-29T00:00:00Z",
                )
            ]
        )
    )[0]
    old = detect(
        Source(
            [
                WorkItemFact(
                    "2",
                    "t",
                    "p",
                    "chen",
                    delivered_pr=True,
                    changed_lines=99,
                    closed_at="2026-01-01T00:00:00Z",
                )
            ]
        )
    )
    assert recent.score > (old[0].score if old else 0)


def test_existing_asset_lowers_score() -> None:
    source = Source(
        [
            WorkItemFact(
                "1",
                "保单流水导入",
                "p",
                "chen",
                delivered_pr=True,
                changed_lines=99,
                closed_at="2026-09-29T00:00:00Z",
            )
        ]
    )
    plain = detect(source)[0].score
    with_asset = detect(source, existing_asset_titles=["保单流水导入"])
    assert not with_asset or with_asset[0].score < plain


def test_refresh_is_idempotent(indexed) -> None:
    source = Source(
        [
            WorkItemFact(
                "1",
                "t",
                "p",
                "chen",
                delivered_pr=True,
                changed_lines=99,
                closed_at="2026-09-29T00:00:00Z",
            )
        ]
    )
    first = refresh(indexed.engine, source)
    second = refresh(indexed.engine, source)
    assert first["created"] == 1 and second["created"] == 0


def test_leads_visible_only_to_owner(client) -> None:
    client.headers.update({"X-FDE-User": "chen"})
    client.post("/api/v1/workbench/leads/refresh")
    mine = client.get("/api/v1/workbench").json()["leads"]
    assert mine
    client.headers.update({"X-FDE-User": "zhao"})
    assert client.get("/api/v1/workbench").json()["leads"] == []


def test_ignore_lead_requires_owner(client) -> None:
    client.headers.update({"X-FDE-User": "chen"})
    client.post("/api/v1/workbench/leads/refresh")
    lead_id = client.get("/api/v1/workbench").json()["leads"][0]["lead_id"]
    client.headers.update({"X-FDE-User": "zhao"})
    assert client.post(f"/api/v1/workbench/leads/{lead_id}/ignore").status_code == 403
    client.headers.update({"X-FDE-User": "chen"})
    assert client.post(f"/api/v1/workbench/leads/{lead_id}/ignore").json()["status"] == "ignored"
    assert lead_id not in [
        lead["lead_id"] for lead in client.get("/api/v1/workbench").json()["leads"]
    ]
