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
            "kind": "Solution",
            "title": "坏文件",
            "filename": "broken.docx",
            "content_base64": base64.b64encode(b"not a docx").decode(),
            "scope": "company",
        },
    )
    assert response.status_code == 200
    assert response.json()["extraction"]["status"] == "failed"


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
