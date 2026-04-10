"""
agents/pdf_quality.py
PDF quality checks for Gilbarco-style manuals.

This module is intentionally separate from the DITA extraction pipeline so the
main UI can offer a standalone "Check PDF Quality" flow.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import io
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

from agents.extractor import ExtractorError, _is_blank_page, _parse_page_range


ROOT = Path(__file__).resolve().parent.parent
QUALITY_RULES_PATH = ROOT / "config" / "pdf_quality_rules.yaml"
LOGO_REFS_DIR = ROOT / "config" / "logo_refs"

MONTH_YEAR_RE = re.compile(
    r"\b("
    r"january|february|march|april|may|june|july|august|"
    r"september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
    r")\s+\d{4}\b",
    re.IGNORECASE,
)
NOTICE_RE = re.compile(
    r"this\s+page\s+(is\s+)?intentionally\s+(left\s+)?blank",
    re.IGNORECASE,
)
STRAIGHT_QUOTES_RE = re.compile(r"[\"']")
FOOTER_PAGE_RE = re.compile(r"\bpage\s+\d+\b", re.IGNORECASE)
COPYRIGHT_RE = re.compile(r"^[\u00a9©Â]\s*\d{4}")
MDE_LINE_RE = re.compile(r"\bMDE-[A-Z0-9-]+\b", re.IGNORECASE)
FOOTER_SMALL_TEXT_MAX = 10.5
DEFAULT_WATERMARK_KEYWORDS = ("draft", "confidential", "preliminary", "sample")


@dataclass
class Finding:
    page: int | None
    severity: str
    message: str
    evidence: str = ""


@dataclass
class CheckResult:
    id: str
    title: str
    status: str
    summary: str
    findings: list[Finding] = field(default_factory=list)


@dataclass
class QualityReport:
    overall_status: str
    checks: list[CheckResult]
    page_count: int
    pages_checked: list[int]
    debug_log: list[str] = field(default_factory=list)


@dataclass
class FooterInfo:
    page: int
    lines: list[str]
    footer_text: str
    month_year: str | None
    title_text: str
    has_footer_signal: bool


def _log(log: list[str] | None, message: str) -> None:
    if log is not None:
        log.append(message)


def _load_quality_rules(log: list[str]) -> dict:
    if not QUALITY_RULES_PATH.is_file():
        _log(log, f"[QUALITY] rules file missing: {QUALITY_RULES_PATH}")
        return {}
    try:
        data = yaml.safe_load(QUALITY_RULES_PATH.read_text(encoding="utf-8")) or {}
        _log(log, f"[QUALITY] loaded rules from {QUALITY_RULES_PATH.name}")
        return data
    except Exception as exc:
        _log(log, f"[QUALITY] failed to load rules: {exc}")
        return {}


def _normalize_text(text: str) -> str:
    text = text.casefold()
    text = MONTH_YEAR_RE.sub("", text)
    text = re.sub(r"\bpage\s+\d+\b", "", text)
    text = re.sub(r"[\u00b7\u2022]+", " ", text)
    text = re.sub(r"[\u2013\u2014\-_/|]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def _meaningful_footer_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if FOOTER_PAGE_RE.fullmatch(line.strip()):
            continue
        if COPYRIGHT_RE.search(line):
            continue
        lines.append(line)
    return lines


def _extract_footer_lines(page) -> list[str]:
    height = float(page.height or 0)
    words = page.extract_words(
        x_tolerance=3,
        y_tolerance=3,
        keep_blank_chars=False,
        use_text_flow=True,
        extra_attrs=["size"],
    )
    footer_words = [
        word for word in words
        if word.get("text", "").strip()
        and (
            float(word.get("top", 0)) >= height * 0.93
            or (
                float(word.get("top", 0)) >= height * 0.90
                and float(word.get("size", 99)) <= FOOTER_SMALL_TEXT_MAX
            )
        )
    ]

    if not footer_words:
        width = float(page.width or 0)
        bottom_band = page.crop((0, height * 0.95, width, height))
        return _meaningful_footer_lines(bottom_band.extract_text() or "")

    footer_words.sort(key=lambda w: (round(float(w.get("top", 0)) / 3), float(w.get("x0", 0))))
    grouped: list[list[dict]] = []
    for word in footer_words:
        top = float(word.get("top", 0))
        if not grouped:
            grouped.append([word])
            continue
        prev_top = float(grouped[-1][0].get("top", 0))
        if abs(top - prev_top) <= 3:
            grouped[-1].append(word)
        else:
            grouped.append([word])

    lines_with_top: list[tuple[float, str]] = []
    for line_words in grouped:
        ordered = sorted(line_words, key=lambda w: float(w.get("x0", 0)))
        text = " ".join(word.get("text", "").strip() for word in ordered if word.get("text", "").strip())
        top = min(float(word.get("top", 0)) for word in ordered)
        lines_with_top.append((top, text))

    lines_with_top.sort(key=lambda item: item[0])
    selected: list[tuple[float, str]] = []
    for top, text in reversed(lines_with_top):
        if not selected:
            selected.append((top, text))
            continue
        if abs(selected[-1][0] - top) <= 14 and len(selected) < 2:
            selected.append((top, text))
        else:
            break
    selected.reverse()
    return _meaningful_footer_lines("\n".join(text for _, text in selected))


def _extract_footer_info(page, page_num: int) -> FooterInfo:
    lines = _extract_footer_lines(page)
    joined = " | ".join(lines)
    month_match = MONTH_YEAR_RE.search(joined)
    month_year = month_match.group(0) if month_match else None
    has_footer_signal = any(
        MONTH_YEAR_RE.search(line)
        or MDE_LINE_RE.search(line)
        or FOOTER_PAGE_RE.search(line)
        or COPYRIGHT_RE.search(line)
        for line in lines
    )

    title_candidate = next((line for line in lines if MDE_LINE_RE.search(line)), "")
    if not title_candidate:
        title_candidates = [
            line for line in lines
            if not MONTH_YEAR_RE.search(line)
            and not FOOTER_PAGE_RE.fullmatch(line.strip())
            and re.search(r"[A-Za-z]", line)
            and not line.lower().startswith(("note:", "warning:", "caution:", "danger:"))
        ]
        if title_candidates:
            title_candidate = max(title_candidates, key=len)
    if not title_candidate and lines:
        title_candidate = max(lines, key=len)

    title_text = MONTH_YEAR_RE.sub("", title_candidate)
    title_text = FOOTER_PAGE_RE.sub("", title_text)
    title_text = re.sub(r"[\u00b7\u2022]+", " ", title_text)
    title_text = " ".join(title_text.replace("|", " ").split(" - "))
    title_text = " ".join(title_text.split())
    return FooterInfo(
        page=page_num,
        lines=lines,
        footer_text=joined,
        month_year=month_year,
        title_text=title_text.strip(),
        has_footer_signal=bool(has_footer_signal),
    )


def _status_rank(status: str) -> int:
    return {"fail": 3, "warn": 2, "not_checked": 1, "pass": 0}.get(status, 0)


def _overall_status(checks: list[CheckResult]) -> str:
    worst = max((_status_rank(check.status) for check in checks), default=0)
    for name, rank in {"fail": 3, "warn": 2, "not_checked": 1, "pass": 0}.items():
        if rank == worst:
            return name
    return "pass"


def _footer_consistency_check(footers: list[FooterInfo], log: list[str]) -> CheckResult:
    findings: list[Finding] = []
    month_pages = [(f.page, f.month_year) for f in footers if f.month_year]
    missing = [f for f in footers if f.has_footer_signal and not f.month_year]
    distinct = sorted({m.casefold() for _, m in month_pages})

    for footer in missing:
        findings.append(
            Finding(
                page=footer.page,
                severity="error",
                message="Footer text found but no month/year was detected.",
                evidence=footer.footer_text,
            )
        )

    if len(distinct) > 1:
        for page, month_year in month_pages:
            findings.append(
                Finding(
                    page=page,
                    severity="error",
                    message=f"Footer month/year differs on this page: {month_year}",
                    evidence=month_year or "",
                )
            )
        summary = "Footer month/year is inconsistent across pages."
        status = "fail"
    elif not distinct:
        summary = "No footer month/year was detected."
        status = "warn"
    else:
        detected = month_pages[0][1] or ""
        now = datetime.now()
        current_keys = {now.strftime("%B %Y").casefold(), now.strftime("%b %Y").casefold()}
        parsed_future = False
        try:
            parsed = datetime.strptime(detected, "%B %Y")
        except ValueError:
            try:
                parsed = datetime.strptime(detected, "%b %Y")
            except ValueError:
                parsed = None
        if parsed is not None:
            parsed_future = (parsed.year, parsed.month) > (now.year, now.month)
        if missing:
            summary = f"Most pages use {detected}, but some footer month/year values are missing."
            status = "fail"
        elif detected.casefold() in current_keys or parsed_future:
            summary = f"Footer month/year is consistent: {detected}."
            status = "pass"
        else:
            summary = (
                f"Footer month/year is consistent ({detected}) but it is neither the "
                "current month/year nor a future month/year."
            )
            status = "warn"

    _log(log, f"[QUALITY] footer consistency: {status} ({summary})")
    return CheckResult(
        id="footer_month_year",
        title="Footer Month/Year Consistency",
        status=status,
        summary=summary,
        findings=findings,
    )


def _bookmark_footer_check(doc, footers: list[FooterInfo], log: list[str]) -> CheckResult:
    toc = doc.get_toc(simple=True)
    if not toc:
        return CheckResult(
            id="bookmark_footer_title",
            title="Bookmark Title Matches Footer",
            status="not_checked",
            summary="The PDF has no bookmarks, so this check could not be run.",
        )

    bookmark_title = ""
    for level, title, _page in toc:
        if level == 1 and title.strip():
            bookmark_title = title.strip()
            break
    if not bookmark_title:
        bookmark_title = toc[0][1].strip()

    footer_titles = [f.title_text for f in footers if f.has_footer_signal and f.title_text]
    if not footer_titles:
        return CheckResult(
            id="bookmark_footer_title",
            title="Bookmark Title Matches Footer",
            status="warn",
            summary="Bookmarks were found, but no usable footer title could be extracted.",
        )

    footer_title = Counter(footer_titles).most_common(1)[0][0]
    lhs = _normalize_text(bookmark_title)
    rhs = _normalize_text(footer_title)
    matches = bool(lhs and rhs and (lhs == rhs or lhs in rhs or rhs in lhs))
    status = "pass" if matches else "fail"
    summary = (
        "Bookmark title matches the footer title after removing month/year."
        if matches
        else "Bookmark title does not match the footer title after normalization."
    )
    _log(log, f"[QUALITY] bookmark/footer title: {status} ({bookmark_title!r} vs {footer_title!r})")
    findings = []
    if not matches:
        findings.append(
            Finding(
                page=toc[0][2] if toc else None,
                severity="error",
                message="Bookmark title does not align with the footer title.",
                evidence=f"bookmark={bookmark_title!r} footer={footer_title!r}",
            )
        )
    return CheckResult(
        id="bookmark_footer_title",
        title="Bookmark Title Matches Footer",
        status=status,
        summary=summary,
        findings=findings,
    )


def _straight_quotes_check(page_texts: list[tuple[int, str]], log: list[str]) -> CheckResult:
    findings = []
    for page_num, text in page_texts:
        matches = STRAIGHT_QUOTES_RE.findall(text)
        if matches:
            findings.append(
                Finding(
                    page=page_num,
                    severity="warning",
                    message=f"Straight quote characters detected ({len(matches)} occurrence(s)).",
                    evidence="".join(matches[:10]),
                )
            )
    status = "warn" if findings else "pass"
    summary = (
        f"Straight quotes detected on {len(findings)} page(s)."
        if findings
        else "No straight single or double quotes detected in extracted text."
    )
    _log(log, f"[QUALITY] straight quotes: {status}")
    return CheckResult(
        id="straight_quotes",
        title="Curly Quotes Check",
        status=status,
        summary=summary,
        findings=findings,
    )


def _ocr_available(rules: dict, log: list[str]) -> bool:
    enabled = ((rules.get("ocr") or {}).get("enabled", True))
    available = enabled and bool(shutil.which("tesseract"))
    _log(log, f"[QUALITY] ocr available={available}")
    return available


def _render_region(doc, page_num: int, clip, dpi: int) -> bytes:
    import fitz  # type: ignore

    page = doc[page_num - 1]
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    return pix.tobytes("png")


def _ocr_image_bytes(image_bytes: bytes, lang: str, log: list[str]) -> str:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return ""
    with tempfile.TemporaryDirectory() as tmpdir:
        img_path = Path(tmpdir) / "ocr_input.png"
        out_base = Path(tmpdir) / "ocr_output"
        img_path.write_bytes(image_bytes)
        try:
            subprocess.run(
                [tesseract, str(img_path), str(out_base), "-l", lang],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            txt_path = out_base.with_suffix(".txt")
            text = txt_path.read_text(encoding="utf-8", errors="ignore") if txt_path.exists() else ""
            return " ".join(text.split())
        except Exception as exc:
            _log(log, f"[QUALITY] OCR failed: {exc}")
            return ""


def _average_hash(image_bytes: bytes) -> str:
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((8, 8))
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    return "".join("1" if px >= avg else "0" for px in pixels)


def _hash_distance(lhs: str, rhs: str) -> int:
    return sum(1 for a, b in zip(lhs, rhs) if a != b)


def _logo_refs_for_brand(brand: str) -> list[Path]:
    brand_dir = LOGO_REFS_DIR / brand
    if not brand_dir.is_dir():
        return []
    refs = []
    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        refs.extend(sorted(brand_dir.glob(pattern)))
    return refs


def _detect_expected_brand(doc_title: str, page_texts: list[tuple[int, str]], rules: dict, log: list[str]) -> str | None:
    haystack = _normalize_text(doc_title + " " + " ".join(text for _, text in page_texts[:3]))
    brand_rules = rules.get("brand_rules") or {}
    for brand, meta in brand_rules.items():
        for keyword in meta.get("keywords", []):
            if _normalize_text(keyword) in haystack:
                _log(log, f"[QUALITY] expected brand={brand} via keyword={keyword}")
                return brand
    _log(log, "[QUALITY] expected brand unresolved")
    return None


def _region_color_mode(image_bytes: bytes) -> str:
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    blue_pixels = 0
    color_pixels = 0
    total = 0
    for r, g, b in img.getdata():
        total += 1
        if abs(r - g) > 14 or abs(g - b) > 14 or abs(r - b) > 14:
            color_pixels += 1
        if b > r + 20 and b > g + 20:
            blue_pixels += 1
    if total == 0:
        return "unknown"
    if blue_pixels / total > 0.02:
        return "blue"
    if color_pixels / total > 0.04:
        return "color"
    return "monochrome"


def _brand_logo_check(doc, page_texts: list[tuple[int, str]], rules: dict, log: list[str]) -> CheckResult:
    toc = doc.get_toc(simple=True)
    doc_title = toc[0][1].strip() if toc else (page_texts[0][1].splitlines()[0] if page_texts and page_texts[0][1] else "")
    expected_brand = _detect_expected_brand(doc_title, page_texts, rules, log)
    if not expected_brand:
        return CheckResult(
            id="brand_logo",
            title="Brand Logo Check",
            status="not_checked",
            summary="Could not determine the expected brand from document text.",
        )

    page = doc[0]
    rect = page.rect
    header_clip = rect.__class__(0, 0, rect.width, rect.height * 0.22)
    dpi = int(((rules.get("ocr") or {}).get("dpi", 200)))
    header_png = _render_region(doc, 1, header_clip, dpi)
    region_mode = _region_color_mode(header_png)
    refs = _logo_refs_for_brand(expected_brand)
    findings: list[Finding] = []
    status = "pass"
    summary_parts: list[str] = [f"Expected brand: {expected_brand}."]

    if refs:
        target_hash = _average_hash(header_png)
        distances = []
        for ref in refs:
            try:
                distances.append((ref.name, _hash_distance(target_hash, _average_hash(ref.read_bytes()))))
            except Exception as exc:
                _log(log, f"[QUALITY] failed to hash logo ref {ref.name}: {exc}")
        if distances:
            best_name, best_dist = min(distances, key=lambda item: item[1])
            max_dist = int(((rules.get("matching_thresholds") or {}).get("logo_hash_max_distance", 18)))
            if best_dist > max_dist:
                status = "fail"
                findings.append(
                    Finding(
                        page=1,
                        severity="error",
                        message="Header/logo region does not match the expected approved logo reference.",
                        evidence=f"brand={expected_brand} ref={best_name} dist={best_dist}",
                    )
                )
            summary_parts.append(f"Best logo match: {best_name} (distance {best_dist}).")
    else:
        status = "not_checked"
        summary_parts.append("No reference logo assets are available yet for this brand.")

    tagline_required = " ".join((rules.get("required_taglines") or {}).get("all", []))
    tagline_present = tagline_required and tagline_required in _normalize_text(" ".join(text for _, text in page_texts[:2]))
    source = "pdf_text"
    if not tagline_present and _ocr_available(rules, log):
        source = "ocr"
        ocr_text = _normalize_text(_ocr_image_bytes(header_png, (rules.get("ocr") or {}).get("language", "eng"), log))
        tagline_present = bool(tagline_required and tagline_required in ocr_text)

    if tagline_required:
        if tagline_present:
            summary_parts.append(f'"{tagline_required}" detected via {source}.')
        else:
            status = "fail" if status != "not_checked" else "warn"
            findings.append(
                Finding(
                    page=1,
                    severity="error" if status == "fail" else "warning",
                    message='Required "Powered by Vontier" tagline was not detected.',
                    evidence=f"source={source}",
                )
            )

    expected_mode = ((rules.get("logo_color_rules") or {}).get(expected_brand, {}) or {}).get("mode")
    if expected_mode == "monochrome" and region_mode not in {"monochrome", "unknown"}:
        if status == "pass":
            status = "warn"
        findings.append(
            Finding(
                page=1,
                severity="warning",
                message="Logo/header region appears to contain color where monochrome is expected.",
                evidence=f"detected_color_mode={region_mode}",
            )
        )
    elif expected_mode == "allow_blue" and region_mode not in {"blue", "color", "unknown"}:
        findings.append(
            Finding(
                page=1,
                severity="warning",
                message="Angi branding did not show obvious blue in the detected logo/header region.",
                evidence=f"detected_color_mode={region_mode}",
            )
        )
        if status == "pass":
            status = "warn"

    _log(log, f"[QUALITY] brand/logo: {status} brand={expected_brand} color={region_mode} refs={len(refs)}")
    return CheckResult(
        id="brand_logo",
        title="Brand Logo Check",
        status=status,
        summary=" ".join(summary_parts),
        findings=findings,
    )


def _watermark_check(doc, rules: dict, log: list[str]) -> CheckResult:
    findings = []
    keywords = tuple((rules.get("matching_thresholds") or {}).get("watermark_keywords", list(DEFAULT_WATERMARK_KEYWORDS)))
    for page_num in range(1, len(doc) + 1):
        page = doc[page_num - 1]
        try:
            page_dict = page.get_text("dict")
        except Exception:
            page_dict = {"blocks": []}
        page_rect = page.rect
        for block in page_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    size = float(span.get("size", 0))
                    bbox = span.get("bbox", (0, 0, 0, 0))
                    if not text or size < 20:
                        continue
                    if not any(key in text.casefold() for key in keywords):
                        continue
                    x_mid = (bbox[0] + bbox[2]) / 2
                    y_mid = (bbox[1] + bbox[3]) / 2
                    if page_rect.width * 0.2 <= x_mid <= page_rect.width * 0.8 and page_rect.height * 0.2 <= y_mid <= page_rect.height * 0.8:
                        findings.append(
                            Finding(page=page_num, severity="info", message=f"Potential watermark text detected via PDF text: {text}", evidence=text)
                        )
                        break
                else:
                    continue
                break

    source = "pdf_text"
    if not findings and _ocr_available(rules, log):
        dpi = int(((rules.get("ocr") or {}).get("dpi", 200)))
        lang = (rules.get("ocr") or {}).get("language", "eng")
        for page_num in range(1, len(doc) + 1):
            page = doc[page_num - 1]
            ocr_text = _normalize_text(_ocr_image_bytes(_render_region(doc, page_num, page.rect, dpi), lang, log))
            for key in keywords:
                if _normalize_text(key) in ocr_text:
                    source = "ocr"
                    findings.append(
                        Finding(page=page_num, severity="info", message=f"Potential watermark detected via OCR keyword: {key}", evidence=key)
                    )
                    break

    if findings:
        status = "pass"
        summary = f"Potential watermark content detected on {len({f.page for f in findings})} page(s) via {source}."
    else:
        status = "warn"
        summary = "No obvious watermark text was detected through PDF text or OCR fallback."
    _log(log, f"[QUALITY] watermark: {status}")
    return CheckResult(
        id="watermark",
        title="Watermark Check",
        status=status,
        summary=summary,
        findings=findings,
    )


def _has_dark_margin_bar(image_bytes: bytes, side: str, threshold: float) -> bool:
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    w, h = img.size
    if w <= 0 or h <= 0:
        return False
    margin_w = max(3, int(w * 0.03))
    col_range = range(0, margin_w) if side == "left" else range(w - margin_w, w)
    dark_columns = 0
    for x in col_range:
        dark = 0
        for y in range(int(h * 0.15), int(h * 0.85)):
            if img.getpixel((x, y)) < 60:
                dark += 1
        if dark / max(1, int(h * 0.70)) >= threshold:
            dark_columns += 1
    return dark_columns >= max(1, margin_w // 4)


def _change_bar_check(doc, rules: dict, log: list[str]) -> CheckResult:
    findings = []
    candidate_pages = set()
    for page_num in range(1, len(doc) + 1):
        page = doc[page_num - 1]
        page_rect = page.rect
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        for drawing in drawings:
            rect = drawing.get("rect")
            if not rect:
                continue
            width = float(rect.width)
            height = float(rect.height)
            near_margin = rect.x0 <= page_rect.width * 0.12 or rect.x1 >= page_rect.width * 0.88
            if near_margin and width <= 6 and height >= page_rect.height * 0.2:
                candidate_pages.add(page_num)
                findings.append(
                    Finding(page=page_num, severity="info", message="Potential change bar detected from vector drawing near page margin.", evidence=f"rect={rect}")
                )
                break

    source = "vector"
    if not candidate_pages:
        dpi = int(((rules.get("ocr") or {}).get("dpi", 200)))
        dark_ratio = float(((rules.get("matching_thresholds") or {}).get("dark_margin_ratio", 0.55)))
        for page_num in range(1, len(doc) + 1):
            page = doc[page_num - 1]
            png = _render_region(doc, page_num, page.rect, dpi)
            if _has_dark_margin_bar(png, "left", dark_ratio) or _has_dark_margin_bar(png, "right", dark_ratio):
                source = "raster"
                candidate_pages.add(page_num)
                findings.append(
                    Finding(page=page_num, severity="info", message="Potential change bar detected from raster margin analysis.", evidence="dark margin stripe")
                )

    if candidate_pages:
        status = "pass"
        summary = f"Potential change bars detected on {len(candidate_pages)} page(s) via {source} analysis."
    else:
        status = "warn"
        summary = "No obvious change bars were detected through vector or raster margin analysis."
    _log(log, f"[QUALITY] change bars: {status}")
    return CheckResult(
        id="change_bar",
        title="Change Bar Check",
        status=status,
        summary=summary,
        findings=findings,
    )


def _even_page_count_check(total_pages: int, log: list[str]) -> CheckResult:
    is_even = total_pages % 2 == 0
    status = "pass" if is_even else "fail"
    summary = f"Page count is even ({total_pages})." if is_even else f"Page count is odd ({total_pages})."
    _log(log, f"[QUALITY] page count even: {status}")
    return CheckResult(
        id="even_page_count",
        title="Even Page Count",
        status=status,
        summary=summary,
    )


def _blank_page_notice_check(page_texts: list[tuple[int, str]], total_pages: int, log: list[str]) -> CheckResult:
    findings = []
    blank_pages = []
    for page_num, text in page_texts:
        if not _is_blank_page(text):
            continue
        blank_pages.append(page_num)
        has_notice = bool(NOTICE_RE.search(" ".join(text.split())))
        if page_num != total_pages and not has_notice:
            findings.append(
                Finding(
                    page=page_num,
                    severity="error",
                    message='Blank page is missing "this page is intentionally left blank".',
                    evidence=text.strip()[:200],
                )
            )
    if findings:
        status = "fail"
        summary = "One or more non-final blank pages are missing the required notice."
    elif blank_pages:
        status = "pass"
        summary = f"Blank page notice check passed on {len(blank_pages)} blank page(s)."
    else:
        status = "pass"
        summary = "No blank pages detected."
    _log(log, f"[QUALITY] blank pages: {status}")
    return CheckResult(
        id="blank_page_notice",
        title="Blank Page Notice",
        status=status,
        summary=summary,
        findings=findings,
    )


def check_pdf_quality(
    file_bytes: bytes,
    page_range: str = "",
    debug_log: list[str] | None = None,
) -> QualityReport:
    """
    Run document quality checks for a text-based PDF.
    """
    log = debug_log if debug_log is not None else []
    _log(log, f"[QUALITY] starting quality check file_bytes={len(file_bytes)} page_range={page_range!r}")

    import fitz  # type: ignore
    import pdfplumber  # type: ignore

    rules = _load_quality_rules(log)
    checks: list[CheckResult] = []
    footers: list[FooterInfo] = []
    page_texts: list[tuple[int, str]] = []

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        total_pages = len(pdf.pages)
        total_chars = sum(len(page.extract_text() or "") for page in pdf.pages)
        if total_chars < 50:
            raise ExtractorError(
                "No extractable text found. This appears to be a scanned PDF. "
                "Please supply a text-based (digital) PDF."
            )

        page_indices = _parse_page_range(page_range, total_pages)
        pages_checked = [idx + 1 for idx in sorted(page_indices)] if page_indices is not None else list(range(1, total_pages + 1))

        for zero_idx, page in enumerate(pdf.pages):
            if page_indices is not None and zero_idx not in page_indices:
                continue
            page_num = zero_idx + 1
            text = page.extract_text() or ""
            page_texts.append((page_num, text))
            footers.append(_extract_footer_info(page, page_num))

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        if pages_checked:
            checks.append(_footer_consistency_check(footers, log))
            checks.append(_bookmark_footer_check(doc, footers, log))
            checks.append(_brand_logo_check(doc, page_texts, rules, log))
            checks.append(_straight_quotes_check(page_texts, log))
            checks.append(_watermark_check(doc, rules, log))
            checks.append(_change_bar_check(doc, rules, log))
            checks.append(_even_page_count_check(len(pages_checked), log))
            checks.append(_blank_page_notice_check(page_texts, pages_checked[-1], log))
    finally:
        doc.close()

    report = QualityReport(
        overall_status=_overall_status(checks),
        checks=checks,
        page_count=len(pages_checked),
        pages_checked=pages_checked,
        debug_log=log,
    )
    _log(log, f"[QUALITY] complete overall={report.overall_status} checks={len(checks)}")
    return report
