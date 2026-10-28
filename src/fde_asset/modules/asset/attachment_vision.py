"""附件里的图片转成文字说明，按原来的位置插进附件文字版（2026-10-28）。

知识类资产的附件（PDF、PPT、Word、图片）交给 Agent 的是文字版。架构图、数据图表、案例截图里的内容
以前全丢了。这里在附件上传后，于后台按三道筛选做图片理解：

1. 规则筛选（不调用模型）：反复出现的图（logo、页眉页脚、背景）、很小的图（图标）、
   细长条（分隔线、横幅）、贴在页面上下边缘的小图，直接跳过；
2. 大模型按文字挑（每份文档一次，只看文字和图片的大小、位置）：选出架构图、图表、截图这类
   真正有内容的页面或图片，封面、目录、致谢、纯装饰配图跳过；拿不准的保留；
3. 只对挑中的做图片理解：PDF 按整页渲染成图（需要 PyMuPDF，没有就退回取页面里最大的几张图），
   PPT、Word 按单张图，连同这一页的文字一起交给能看图的模型（和 Agent 同一套）。

结果按附件内容的 SHA-256 存在 attachment_texts 表里，同一份文件不重复做；入库、改版本都直接复用。
后台一次只处理一份（定时任务里逐个领），不压垮小机器。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import Engine

from fde_asset.core.db import attachment_texts, clean_text

logger = logging.getLogger(__name__)

#: 做图片理解的附件类型
VISION_SUFFIXES = {".pdf", ".pptx", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
#: 一份附件最多看多少次图，免得一份超大文件把模型额度用光
MAX_VISION_CALLS = 150
#: 送给模型的图片最长边
MAX_IMAGE_SIDE = 1400
#: 卡在 running 超过这么久就当进程死了，重新领
STALE_MINUTES = 30


def suffix_of(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def wanted(filename: str) -> bool:
    return suffix_of(filename) in VISION_SUFFIXES


# ---------------------------------------------------------------- 文档拆成页面和图片


@dataclass
class Picture:
    id: str
    sha: str
    bytes: int
    width: int = 0
    height: int = 0
    #: 占页面面积的比例（0~1），不知道时为 0
    area: float = 0.0
    #: 图片中心在页面上的纵向位置（0 顶部 ~ 1 底部），不知道时为 0.5
    center_y: float = 0.5
    load: Callable[[], bytes | None] = field(default=lambda: None, repr=False)


@dataclass
class Unit:
    """一页（PDF、PPT）或一段（Word）。"""

    id: str
    label: str
    text: str
    pictures: list[Picture] = field(default_factory=list)
    #: PDF 整页渲染成图（没有 PyMuPDF 时为空）
    render: Callable[[], bytes | None] | None = field(default=None, repr=False)


def outline(filename: str, data: bytes) -> list[Unit]:
    suffix = suffix_of(filename)
    if suffix == ".pptx":
        return _pptx(data)
    if suffix == ".pdf":
        return _pdf(data)
    if suffix == ".docx":
        return _docx(data)
    if suffix in IMAGE_SUFFIXES:
        sha = hashlib.sha1(data).hexdigest()
        width, height = _size(data)
        return [
            Unit(
                "u1",
                "图片",
                "",
                [Picture("u1-i1", sha, len(data), width, height, 1.0, 0.5, lambda: data)],
            )
        ]
    return []


def _size(data: bytes) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:  # noqa: BLE001 - EMF、WMF 之类 PIL 打不开
        return (0, 0)


def _pptx(data: bytes) -> list[Unit]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    presentation = Presentation(io.BytesIO(data))
    slide_w = presentation.slide_width or 1
    slide_h = presentation.slide_height or 1

    def walk(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from walk(shape.shapes)
            else:
                yield shape

    units: list[Unit] = []
    for number, slide in enumerate(presentation.slides, start=1):
        texts: list[str] = []
        pictures: list[Picture] = []
        for shape in walk(slide.shapes):
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                texts.append(shape.text_frame.text.strip())
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    texts.append(" | ".join(cell.text.strip() for cell in row.cells))
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    blob = shape.image.blob
                except Exception:  # noqa: BLE001 - 链接图片等取不到内容
                    continue
                width = shape.width or 0
                height = shape.height or 0
                top = shape.top or 0
                width_px, height_px = _size(blob)
                pictures.append(
                    Picture(
                        f"p{number}-i{len(pictures) + 1}",
                        hashlib.sha1(blob).hexdigest(),
                        len(blob),
                        width_px,
                        height_px,
                        min(1.0, (width * height) / (slide_w * slide_h)),
                        min(1.0, max(0.0, (top + height / 2) / slide_h)),
                        lambda blob=blob: blob,
                    )
                )
        units.append(Unit(f"p{number}", f"第 {number} 页", "\n".join(texts), pictures))
    return units


def _pdf(data: bytes) -> list[Unit]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return _pdf_without_render(data)
    document = fitz.open(stream=data, filetype="pdf")
    units: list[Unit] = []
    for index, page in enumerate(document, start=1):
        rect = page.rect
        page_area = max(1.0, rect.width * rect.height)
        pictures: list[Picture] = []
        for info in page.get_images(full=True):
            xref = info[0]
            try:
                boxes = page.get_image_rects(xref)
            except Exception:  # noqa: BLE001
                boxes = []
            box = boxes[0] if boxes else None
            area = (box.width * box.height / page_area) if box else 0.0
            center = ((box.y0 + box.y1) / 2 / max(1.0, rect.height)) if box else 0.5
            # 同一张图在多页出现时 xref 相同：用 xref 加尺寸当指纹，不用解码
            fingerprint = f"{xref}-{info[2]}x{info[3]}"
            pictures.append(
                Picture(
                    f"p{index}-i{len(pictures) + 1}",
                    fingerprint,
                    int(info[2]) * int(info[3]),
                    int(info[2]),
                    int(info[3]),
                    min(1.0, area),
                    min(1.0, max(0.0, center)),
                    lambda xref=xref: (document.extract_image(xref) or {}).get("image"),
                )
            )

        def render(page=page) -> bytes | None:
            zoom = MAX_IMAGE_SIDE / max(page.rect.width, page.rect.height)
            return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).tobytes("png")

        units.append(Unit(f"p{index}", f"第 {index} 页", page.get_text().strip(), pictures, render))
    return units


def _pdf_without_render(data: bytes) -> list[Unit]:
    """没有 PyMuPDF：文字照取，图片只按页面资源里的尺寸判断，理解时取这一页最大的几张图。"""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    units: list[Unit] = []
    for index, page in enumerate(reader.pages, start=1):
        pictures: list[Picture] = []
        try:
            resources = page.get("/Resources") or {}
            objects = resources.get("/XObject") if hasattr(resources, "get") else None
            objects = objects.get_object() if objects else {}
            for name in objects:
                obj = objects[name].get_object()
                if obj.get("/Subtype") != "/Image":
                    continue
                width, height = int(obj.get("/Width") or 0), int(obj.get("/Height") or 0)
                pictures.append(
                    Picture(
                        f"p{index}-i{len(pictures) + 1}",
                        f"{width}x{height}-{obj.get('/Length')}",
                        width * height,
                        width,
                        height,
                        0.0,
                        0.5,
                        lambda page=page, name=name: _pypdf_image(page, name),
                    )
                )
        except Exception:  # noqa: BLE001 - 资源表坏了就当这页没图
            pictures = []
        units.append(
            Unit(f"p{index}", f"第 {index} 页", (page.extract_text() or "").strip(), pictures)
        )
    return units


def _pypdf_image(page: Any, name: str) -> bytes | None:
    try:
        for image in page.images:
            if image.name.split(".")[0] == str(name).lstrip("/"):
                return image.data
    except Exception:  # noqa: BLE001
        return None
    return None


def _docx(data: bytes) -> list[Unit]:
    """Word 没有页：按图片切段，每张图带上它前面那一段文字当上下文。"""
    from docx import Document

    document = Document(io.BytesIO(data))
    parts = document.part.related_parts
    units: list[Unit] = []
    buffer: list[str] = []
    count = 0
    for paragraph in document.paragraphs:
        if paragraph.text.strip():
            buffer.append(paragraph.text.strip())
        for blip in paragraph._p.xpath(".//a:blip"):
            embed = blip.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
            )
            part = parts.get(embed)
            if part is None or not hasattr(part, "blob"):
                continue
            count += 1
            blob = part.blob
            width, height = _size(blob)
            units.append(
                Unit(
                    f"s{count}",
                    f"第 {count} 张图所在段落",
                    "\n".join(buffer[-6:]),
                    [
                        Picture(
                            f"s{count}-i1",
                            hashlib.sha1(blob).hexdigest(),
                            len(blob),
                            width,
                            height,
                            0.5,
                            0.5,
                            lambda blob=blob: blob,
                        )
                    ],
                )
            )
            buffer = []
    if buffer or not units:
        units.append(Unit(f"s{count + 1}", "正文", "\n".join(buffer), []))
    return units


# ---------------------------------------------------------------- 第一道：规则筛选


def rule_filter(units: list[Unit]) -> dict[str, str]:
    """返回 {图片 id: 跳过理由}。"""
    seen_on: dict[str, set[str]] = {}
    for unit in units:
        for picture in unit.pictures:
            seen_on.setdefault(picture.sha, set()).add(unit.id)
    repeat_floor = max(3, int(len(units) * 0.3))
    skipped: dict[str, str] = {}
    for unit in units:
        for picture in unit.pictures:
            ratio = (picture.width / picture.height) if picture.width and picture.height else 1.0
            if len(units) > 1 and len(seen_on[picture.sha]) >= repeat_floor:
                skipped[picture.id] = "多页重复出现（logo、页眉页脚或背景）"
            elif picture.width and picture.height and picture.width * picture.height < 120 * 120:
                skipped[picture.id] = "太小（图标、装饰）"
            elif not picture.width and picture.bytes < 8_000:
                skipped[picture.id] = "太小（图标、装饰）"
            elif ratio > 6 or ratio < 1 / 6:
                skipped[picture.id] = "细长条（分隔线、横幅）"
            elif (
                picture.area
                and picture.area < 0.15
                and (picture.center_y < 0.1 or picture.center_y > 0.9)
            ):
                skipped[picture.id] = "在页眉页脚位置"
    return skipped


# ---------------------------------------------------------------- 第二道：大模型按文字挑

TRIAGE_SYSTEM = (
    "你在帮一份企业文档挑出需要看图才能理解的页面。给你每一页的文字摘录和这一页剩下的图片（大小、位置、"
    "占页面的比例）。要选：架构图、流程图、数据图表、案例或产品截图、文字很少但图很大的页面（内容大概率在图里）。"
    "要跳过：封面、目录、章节过渡页、致谢页、纯装饰配图、和文字内容重复的示意图标。拿不准的保留。"
    '只返回 JSON：{"selected":[{"id":"页面或图片 id","reason":"一句话"}],'
    '"skipped":[{"id":"页面或图片 id","reason":"一句话"}]}。'
)


def triage(
    units: list[Unit], filename: str, skipped: dict[str, str], llm: Any, *, by_page: bool
) -> tuple[list[str], dict[str, str]]:
    """返回（要理解的 id 列表，{跳过的 id: 理由}）。by_page=True 时按整页选（PDF）。"""
    items = []
    candidates: list[str] = []
    for unit in units:
        left = [p for p in unit.pictures if p.id not in skipped]
        if by_page:
            scanned = len(unit.text.strip()) < 30 and unit.render is not None
            if not left and not scanned:
                continue
            candidates.append(unit.id)
        else:
            if not left:
                continue
            candidates.extend(p.id for p in left)
        items.append(
            {
                "id": unit.id,
                "label": unit.label,
                "text": unit.text[:400],
                "images": [
                    {
                        "id": p.id,
                        "size": f"{p.width}x{p.height}" if p.width else "",
                        "area": f"占页面 {round(p.area * 100)}%" if p.area else "",
                        "position": "上部"
                        if p.center_y < 0.33
                        else ("下部" if p.center_y > 0.66 else "中部"),
                    }
                    for p in left
                ],
            }
        )
    if not candidates:
        return [], {}
    if llm is None or not getattr(llm, "usable", True):
        return candidates[:MAX_VISION_CALLS], {}
    chosen: list[str] = []
    reasons: dict[str, str] = {}
    for start in range(0, len(items), 40):
        chunk = items[start : start + 40]
        try:
            result = llm.complete_json(
                TRIAGE_SYSTEM,
                json.dumps(
                    {"document": filename, "by_page": by_page, "pages": chunk}, ensure_ascii=False
                ),
            )
        except Exception:  # noqa: BLE001 - 挑不了就全看（宁多勿漏）
            logger.warning("attachment vision triage failed for %s", filename, exc_info=True)
            ids = [c for c in candidates if c.split("-")[0] in {item["id"] for item in chunk}]
            chosen.extend(ids)
            continue
        allowed = {c for c in candidates if c.split("-")[0] in {item["id"] for item in chunk}}
        picked = [
            str(entry.get("id"))
            for entry in result.get("selected") or []
            if isinstance(entry, dict) and str(entry.get("id")) in allowed
        ]
        if not by_page:
            # 模型选了整页时，这一页剩下的图都算选中
            pages = {pid for pid in picked if "-" not in pid}
            picked = [c for c in allowed if c in picked or c.split("-")[0] in pages]
        for entry in result.get("skipped") or []:
            if isinstance(entry, dict) and str(entry.get("id")) in allowed:
                reasons[str(entry.get("id"))] = str(entry.get("reason") or "大模型判断不需要看图")
        not_mentioned = allowed - set(picked) - set(reasons)
        # 没提到的按「拿不准保留」处理
        chosen.extend(sorted(set(picked) | not_mentioned, key=candidates.index))
    return chosen[:MAX_VISION_CALLS], reasons


# ---------------------------------------------------------------- 第三道：看图

VISION_SYSTEM = (
    "你在为企业知识库整理文档里的图片。根据图片和同页文字，用中文说清这张图表达的内容："
    "数据图表写出关键数据和结论；架构图、流程图写出组成部分和关系、走向；截图写出关键文字和界面要点；"
    "案例图写出客户、场景和效果。只说图里有的，不要编造，不要复述同页文字已经写了的内容。"
    '纯装饰（照片背景、花纹）返回 decorative=true。只返回 JSON：{"decorative":false,"description":"……"}。'
)


def _as_jpeg(data: bytes) -> bytes | None:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            out = io.BytesIO()
            image.save(out, "JPEG", quality=80)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - EMF、WMF 等 PIL 打不开
        return None


def describe(image: bytes, context: str, filename: str, label: str, llm: Any) -> dict[str, Any]:
    jpeg = _as_jpeg(image)
    if jpeg is None:
        return {"error": "图片格式无法识别（可能是 EMF、WMF 矢量图）"}
    content = [
        {
            "type": "text",
            "text": f"文档《{filename}》{label}。同页文字（节选）：\n{context[:800] or '（这一页没有文字）'}",
        },
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()},
        },
    ]
    result = llm.complete_json(VISION_SYSTEM, content)
    return result if isinstance(result, dict) else {}


# ---------------------------------------------------------------- 整份处理


def understand(
    filename: str,
    data: bytes,
    llm: Any,
    *,
    progress: Callable[[int, int], None] = lambda done, total: None,
    include: set[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """返回（带图片说明的文字版、报告）。报告记下选了哪些、跳过哪些和理由，页面上给人核对。"""
    units = outline(filename, data)
    by_page = suffix_of(filename) == ".pdf" and any(unit.render for unit in units)
    skipped = rule_filter(units)
    single_image = suffix_of(filename) in IMAGE_SUFFIXES
    if single_image:
        chosen, model_skips = [units[0].pictures[0].id], {}
    else:
        chosen, model_skips = triage(units, filename, skipped, llm, by_page=by_page)
    for forced in sorted(include or set()):
        if forced not in chosen:
            chosen.append(forced)
            model_skips.pop(forced, None)
    descriptions: dict[str, str] = {}
    failures: dict[str, str] = {}
    total = len(chosen)
    progress(0, total)
    units_by_id = {unit.id: unit for unit in units}
    for done, target in enumerate(chosen, start=1):
        unit = units_by_id.get(target.split("-")[0])
        if unit is None:
            continue
        try:
            if "-" not in target:  # 整页
                image = unit.render() if unit.render else None
                if image is None:
                    largest = sorted(unit.pictures, key=lambda p: p.width * p.height, reverse=True)
                    image = largest[0].load() if largest else None
            else:
                picture = next((p for p in unit.pictures if p.id == target), None)
                image = picture.load() if picture else None
            if not image:
                failures[target] = "取不到图片内容"
            else:
                result = describe(image, unit.text, filename, unit.label, llm)
                if result.get("error"):
                    failures[target] = str(result["error"])
                elif result.get("decorative"):
                    model_skips[target] = "看图后判断是装饰"
                elif str(result.get("description") or "").strip():
                    descriptions[target] = str(result["description"]).strip()
        except Exception as exc:  # noqa: BLE001 - 一张图失败不影响其他
            failures[target] = f"{type(exc).__name__}"
        progress(done, total)

    lines: list[str] = []
    for unit in units:
        if unit.text.strip() or single_image:
            lines.append(f"[{unit.label}]" if not single_image else "")
            if unit.text.strip():
                lines.append(unit.text.strip())
        page_note = descriptions.get(unit.id)
        if page_note:
            lines.append(f"【{unit.label}图片说明】{page_note}")
        for picture in unit.pictures:
            note = descriptions.get(picture.id)
            if note:
                lines.append(f"【{unit.label}图片说明】{note}")
        lines.append("")
    text = clean_text("\n".join(line for line in lines if line is not None).strip())
    report = {
        "pages": len(units),
        "pictures": sum(len(unit.pictures) for unit in units),
        "described": [{"id": key, "description": value} for key, value in descriptions.items()],
        "skipped": [
            {"id": key, "reason": value}
            for key, value in {**skipped, **model_skips}.items()
            if key not in descriptions
        ],
        "failed": [{"id": key, "reason": value} for key, value in failures.items()],
        "by_page": by_page,
        "calls": total,
    }
    return text, report


# ---------------------------------------------------------------- 存取与后台任务


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(engine: Engine, sha256: str, filename: str, *, force: bool = False) -> None:
    """排上一份；已经有结果就不动（force=True 时重新做，用于「重新理解」「这页也要理解」）。"""
    if not sha256 or not wanted(filename):
        return
    with engine.begin() as conn:
        row = conn.execute(
            select(attachment_texts.c.status).where(attachment_texts.c.sha256 == sha256)
        ).first()
        if row is None:
            conn.execute(
                attachment_texts.insert().values(
                    sha256=sha256,
                    filename=filename,
                    status="pending",
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
        elif force:
            conn.execute(
                update(attachment_texts)
                .where(attachment_texts.c.sha256 == sha256)
                .values(status="pending", done=0, total=0, error="", updated_at=_now())
            )


def include_target(engine: Engine, sha256: str, target: str) -> None:
    """人在页面上点「这页也要理解」：记下来，重新做一遍。"""
    with engine.begin() as conn:
        row = conn.execute(
            select(attachment_texts.c.include_json).where(attachment_texts.c.sha256 == sha256)
        ).first()
        if row is None:
            return
        include = set(json.loads(row.include_json or "[]")) | {target}
        conn.execute(
            update(attachment_texts)
            .where(attachment_texts.c.sha256 == sha256)
            .values(include_json=json.dumps(sorted(include)), status="pending", updated_at=_now())
        )


def get(engine: Engine, sha256: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(attachment_texts).where(attachment_texts.c.sha256 == sha256)
        ).first()
    if row is None:
        return None
    return {
        "sha256": row.sha256,
        "filename": row.filename,
        "status": row.status,
        "done": row.done,
        "total": row.total,
        "text": row.text or "",
        "report": json.loads(row.report_json or "{}"),
        "error": row.error or "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def finished_texts(engine: Engine, shas: list[str]) -> dict[str, str]:
    """这些附件里已经做完图片理解的：{sha256: 带图片说明的文字版}。"""
    if not shas:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            select(attachment_texts.c.sha256, attachment_texts.c.text).where(
                attachment_texts.c.sha256.in_(sorted(set(shas))),
                attachment_texts.c.status == "done",
            )
        ).fetchall()
    return {row.sha256: row.text for row in rows if row.text}


def claim(engine: Engine) -> dict[str, Any] | None:
    stale = _now() - timedelta(minutes=STALE_MINUTES)
    with engine.begin() as conn:
        row = conn.execute(
            select(attachment_texts)
            .where(
                or_(
                    attachment_texts.c.status == "pending",
                    and_(
                        attachment_texts.c.status == "running",
                        attachment_texts.c.claimed_at < stale,
                    ),
                )
            )
            .order_by(attachment_texts.c.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if row is None:
            return None
        conn.execute(
            update(attachment_texts)
            .where(attachment_texts.c.sha256 == row.sha256)
            .values(status="running", claimed_at=_now(), updated_at=_now())
        )
    return {
        "sha256": row.sha256,
        "filename": row.filename,
        "include": set(json.loads(row.include_json or "[]")),
    }


def run_one(engine: Engine, file_store: Any, llm_factory: Callable[[], Any]) -> dict[str, Any]:
    """领一份做完。没有活、或者没配能看图的模型时什么都不做（不领，免得反复读大文件）。"""
    llm = llm_factory()
    if llm is None or not getattr(llm, "usable", False):
        return {"status": "waiting_model"}
    job = claim(engine)
    if job is None:
        return {}
    sha256 = job["sha256"]

    def save(**values: Any) -> None:
        with engine.begin() as conn:
            conn.execute(
                update(attachment_texts)
                .where(attachment_texts.c.sha256 == sha256)
                .values(updated_at=_now(), **values)
            )

    data = file_store.get(sha256) if file_store is not None else None
    if data is None:
        save(status="failed", error="找不到附件原件")
        return {"sha256": sha256, "status": "failed"}
    try:
        text, report = understand(
            job["filename"],
            data,
            llm,
            progress=lambda done, total: save(done=done, total=total),
            include=job["include"],
        )
    except Exception as exc:  # noqa: BLE001 - 解析失败：记下来，附件仍按普通文字版给 Agent
        logger.exception("attachment vision failed for %s", job["filename"])
        save(status="failed", error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return {"sha256": sha256, "status": "failed"}
    save(status="done", text=text, report_json=json.dumps(report, ensure_ascii=False), error="")
    return {"sha256": sha256, "status": "done", "described": len(report["described"])}


def _attachments(rows: list[Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for row in rows:
        try:
            entries = json.loads(getattr(row, "attachments_json", None) or "[]")
        except ValueError:
            continue
        for entry in entries:
            sha = str(entry.get("sha256") or "")
            name = str(entry.get("name") or entry.get("path") or "").rsplit("/", 1)[-1]
            if sha and wanted(name):
                found.append((sha, name))
    return found


def texts_for(engine: Engine, rows: list[Any]) -> dict[str, str]:
    """给 Agent 拼资产时用：这些资产的附件里已做完图片理解的文字版；还没排上的顺手排上（以前入库的附件）。"""
    attachments = _attachments(rows)
    finished = finished_texts(engine, [sha for sha, _ in attachments])
    known = set(finished)
    if len(known) < len({sha for sha, _ in attachments}):
        with engine.connect() as conn:
            known |= {
                row.sha256
                for row in conn.execute(
                    select(attachment_texts.c.sha256).where(
                        attachment_texts.c.sha256.in_(sorted({sha for sha, _ in attachments}))
                    )
                )
            }
        for sha, name in attachments:
            if sha not in known:
                enqueue(engine, sha, name)
                known.add(sha)
    return finished


def done_marker(engine: Engine, rows: list[Any]) -> list[str]:
    """哪些附件已做完图片理解：资产包的「没变」判断要算上它，做完了要重新拼一份。"""
    return sorted(finished_texts(engine, [sha for sha, _ in _attachments(rows)]))


_WORKER_LOCK = threading.Lock()


def start_worker(engine: Engine, file_store: Any, on_done: Callable[[], Any] | None = None) -> bool:
    """没在做就起一个线程，把排着的一份份做完；已经在做就什么都不干。一次只做一份。

    on_done：这一批里有做完的，结束时调一次（重建检索，让图片说明能被搜到）。"""
    if not _WORKER_LOCK.acquire(blocking=False):
        return False

    def loop() -> None:
        from fde_asset.platform.llm.client import agent_model_client

        finished = False
        try:
            while True:
                result = run_one(engine, file_store, agent_model_client)
                if result.get("status") == "done":
                    finished = True
                if not result or result.get("status") == "waiting_model":
                    break
        except Exception:  # noqa: BLE001 - 下一轮定时任务会再起
            logger.exception("attachment vision worker stopped")
        finally:
            _WORKER_LOCK.release()
        if finished and on_done is not None:
            try:
                on_done()
            except Exception:  # noqa: BLE001 - 重建检索失败等下次仓库有提交时再建
                logger.exception("reindex after attachment vision failed")

    threading.Thread(target=loop, name="attachment-vision", daemon=True).start()
    return True
