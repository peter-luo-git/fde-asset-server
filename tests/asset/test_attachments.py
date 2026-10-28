"""附件文本提取：四种格式 + 失败与不支持的处理。"""

from __future__ import annotations


from fde_asset.platform.extract import extract_text
from scripts.seed_assets import make_docx, make_pdf, make_pptx, make_xlsx


def test_pdf_text_layer() -> None:
    result = extract_text("a.pdf", make_pdf(["migration playbook", "freeze writes"]))
    assert result.status == "ok" and "migration playbook" in result.text


def test_docx_paragraphs() -> None:
    result = extract_text("a.docx", make_docx("标题", ["结论一", "结论二"]))
    assert result.status == "ok" and "结论二" in result.text


def test_xlsx_cells() -> None:
    result = extract_text("a.xlsx", make_xlsx([["参数", "值"], ["shared_buffers", "16GB"]]))
    assert result.status == "ok" and "shared_buffers" in result.text


def test_pptx_slides() -> None:
    result = extract_text("a.pptx", make_pptx("方案", ["双轨并行", "三轮比对"]))
    assert result.status == "ok" and "双轨并行" in result.text


def test_markdown_passthrough() -> None:
    assert extract_text("a.md", "# 标题".encode()).text == "# 标题"


def test_unsupported_format() -> None:
    result = extract_text("a.zip", b"PK\x03\x04")
    assert result.status == "unsupported"


def test_corrupted_file_is_failed_not_crash() -> None:
    result = extract_text("a.docx", b"not a docx")
    assert result.status == "failed" and result.detail


def test_text_is_truncated() -> None:
    result = extract_text("a.md", ("x" * 5000).encode(), limit=100)
    assert len(result.text) == 100


def test_upload_draft_keeps_asset_valid_when_extraction_fails(client) -> None:
    import base64

    client.headers.update({"X-FDE-User": "chen"})
    response = client.post(
        "/api/v1/harvest-candidates/from-upload",
        json={
            "kind": "Rule",
            "title": "坏文件",
            "filename": "broken.docx",
            "content_base64": base64.b64encode(b"not a docx").decode(),
            "scope": "company",
        },
    )
    assert response.status_code == 200
    assert response.json()["extraction"]["status"] == "failed"


def test_knowledge_kinds_are_not_imported_from_files(client) -> None:
    """方案、实施、问题、经验的正文按模板手写，历史文档作附件上传（2026-10-28）。"""
    import base64

    client.headers.update({"X-FDE-User": "chen"})
    for kind in ("Solution", "Implementation", "Case", "Experience"):
        response = client.post(
            "/api/v1/harvest-candidates/from-upload",
            json={
                "kind": kind,
                "title": "历史文档",
                "filename": "note.md",
                "content_base64": base64.b64encode("一些正文".encode()).decode(),
                "scope": "company",
            },
        )
        assert response.status_code == 422, kind
        assert "附件" in response.json()["detail"]


def test_rule_import_puts_full_text_in_body_and_keeps_no_attachment(client) -> None:
    import base64

    client.headers.update({"X-FDE-User": "chen"})
    text = "\n".join(f"第 {i} 条：变量名用 snake_case" for i in range(1, 400))
    response = client.post(
        "/api/v1/harvest-candidates/from-upload",
        json={
            "kind": "Rule",
            "title": "编码规范",
            "filename": "规范.md",
            "content_base64": base64.b64encode(text.encode()).decode(),
            "scope": "company",
        },
    )
    assert response.status_code == 200, response.text
    candidate = client.get(f"/api/v1/harvest-candidates/{response.json()['candidate_id']}").json()
    body = candidate["files"]["RULE.md"]
    assert "第 399 条" in body and "原文摘录" not in body and "<请用一句话" not in body
    assert not any(path.startswith("attachments/") for path in candidate["files"])

    # 规范不收附件：Agent 读不到，要遵守的写进正文
    attach = client.post(
        f"/api/v1/harvest-candidates/{candidate['candidate_id']}/attachments",
        json={"filename": "原件.md", "content_base64": base64.b64encode(b"x").decode()},
    )
    assert attach.status_code == 409 and "正文" in attach.json()["detail"]


def test_oversize_upload_rejected(client) -> None:
    import base64

    client.headers.update({"X-FDE-User": "chen"})
    huge = base64.b64encode(b"0" * (51 * 1024 * 1024)).decode()
    response = client.post(
        "/api/v1/harvest-candidates/from-upload",
        json={
            "kind": "Solution",
            "title": "超大文件",
            "filename": "big.pdf",
            "content_base64": huge,
            "scope": "company",
        },
    )
    assert response.status_code == 413


def test_owner_defaults_to_the_department_chosen_as_scope(client) -> None:
    """作用域选了某个部门，负责方默认就是那个部门，不是起草人自己的部门（2026-10-28）。"""
    import base64

    client.headers.update({"X-FDE-User": "chen"})  # chen 在 data-intel
    response = client.post(
        "/api/v1/harvest-candidates/from-upload",
        json={
            "kind": "Rule",
            "title": "平台部编码规范",
            "filename": "规范.md",
            "content_base64": base64.b64encode("变量名用 snake_case".encode()).decode(),
            "scope": "department",
            "department_code": "platform",
        },
    )
    assert response.status_code == 200, response.text
    candidate = client.get(f"/api/v1/harvest-candidates/{response.json()['candidate_id']}").json()
    assert "owner: department:platform" in candidate["files"]["asset.yaml"]

    blank = client.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Case",
            "title": "平台部的问题",
            "scope": "department",
            "department_code": "platform",
        },
    )
    assert blank.status_code in (200, 201), blank.text
    created = client.get(f"/api/v1/harvest-candidates/{blank.json()['candidate_id']}").json()
    assert "owner: department:platform" in created["files"]["asset.yaml"]
