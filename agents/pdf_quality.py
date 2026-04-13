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
import gc

import time
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
    width  = float(page.width  or 0)
    # Crop to footer strip before extract_words so pdfplumber
    # does not parse image data from the full page
    footer_band = page.crop((0, height * 0.88, width, height))
    words = footer_band.extract_words(
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
    NEAR_MONTH_RE = re.compile(r"\b([a-z]{3,12})\s+(\d{4})\b", re.IGNORECASE)
    misspelled = []
    for f in footers:
        if f.has_footer_signal and not f.month_year:
            for m in NEAR_MONTH_RE.finditer(f.footer_text):
                word = m.group(1)
                import difflib
                close = difflib.get_close_matches(
                    word.casefold(),
                    [
                        "january","february","march","april","may","june",
                        "july","august","september","october","november","december",
                        "jan","feb","mar","apr","jun","jul","aug","sep","sept","oct","nov","dec",
                    ],
                    n=1, cutoff=0.8,
                )
                if close:
                    misspelled.append((f, m.group(0)))
                    break
    misspelled_pages = {f.page for f, _ in misspelled}
    missing = [
        f for f in footers
        if f.has_footer_signal
        and not f.month_year
        and f.page not in misspelled_pages
    ]
    distinct = sorted({m.casefold() for _, m in month_pages})

    if misspelled:
        status = "fail"
    for footer, bad_word in misspelled:
        findings.append(
            Finding(
                page=footer.page,
                severity="error",
                message=f'Footer month/year may contain a spelling error: "{bad_word}". Please check.',
                evidence=footer.footer_text,
            )
        )
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
    data = pix.tobytes("png")
    pix = None
    return data


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
    # First try subdirectory structure
    brand_dir = LOGO_REFS_DIR / brand
    if brand_dir.is_dir():
        refs = []
        for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            refs.extend(sorted(brand_dir.glob(pattern)))
        return refs
    
    # Fallback: look for {brand}.png in root
    logo_file = LOGO_REFS_DIR / f"{brand}.png"
    if logo_file.is_file():
        return [logo_file]
    
    return []


# Dispenser brands take priority — Invenco is subordinate when co-present
_DISPENSER_BRANDS = {"gilbarco", "gasboy"}

def _detect_expected_brand(
    doc_title: str,
    rules: dict,
    log: list[str],
) -> str | None:
    # Brand keyword matching runs against the footer title ONLY.
    # Do not include page body text — it causes false matches.
    haystack = _normalize_text(doc_title)
    brand_rules = rules.get("brand_rules") or {}

    matched: dict[str, str] = {}  # brand -> matched keyword
    for brand, meta in brand_rules.items():
        for keyword in meta.get("keywords", []):
            if _normalize_text(keyword) in haystack:
                matched[brand] = keyword
                break  # first keyword match is enough per brand

    if not matched:
        _log(log, "[QUALITY] expected brand unresolved")
        return None

    dispenser_matches = {b: kw for b, kw in matched.items() if b in _DISPENSER_BRANDS}

    if dispenser_matches:
        if len(dispenser_matches) == 1:
            brand = next(iter(dispenser_matches))
        else:
            brand = max(
                dispenser_matches,
                key=lambda b: sum(
                    1 for kw in (brand_rules.get(b) or {}).get("keywords", [])
                    if _normalize_text(kw) in haystack
                ),
            )
        if "invenco" in matched:
            _log(log, f"[QUALITY] invenco keywords present but subordinate to dispenser brand={brand}")
    else:
        brand = next(iter(matched))

    _log(log, f"[QUALITY] expected brand={brand} via keyword={matched[brand]!r} all_matched={list(matched)}")
    return brand


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


def _brand_above_vontier(image_bytes: bytes, lang: str, log: list[str]) -> str:
    """
    Use pytesseract word-level data to find the text on the
    line immediately above "Powered by Vontier".

    Returns the detected brand name text (lowercased, stripped)
    or "" if not found.
    """
    try:
        import pytesseract
        from PIL import Image as _Image
        img = _Image.open(io.BytesIO(image_bytes))
        data = pytesseract.image_to_data(
            img,
            lang=lang,
            output_type=pytesseract.Output.DICT,
        )
    except Exception as exc:
        _log(log, f"[QUALITY] pytesseract word data failed: {exc}")
        return ""

    # Collect words with their top Y coordinate
    words = [
        {
            "text": data["text"][i].strip(),
            "top":  data["top"][i],
            "conf": int(data["conf"][i]),
        }
        for i in range(len(data["text"]))
        if data["text"][i].strip() and int(data["conf"][i]) > 20
    ]

    # Find Y of "Powered" (start of "Powered by Vontier")
    vontier_top = None
    for w in words:
        if w["text"].lower() in ("powered", "vontier"):
            vontier_top = w["top"]
            break

    if vontier_top is None:
        _log(log, "[QUALITY] 'Powered by Vontier' not found in word data")
        return ""

    # Collect words on the line immediately above — within 40px above
    # and no more than 80px above vontier_top
    above_words = [
        w["text"] for w in words
        if vontier_top - 80 <= w["top"] < vontier_top - 5
    ]

    brand_text = " ".join(above_words).strip().lower()
    _log(log, f"[QUALITY] text above 'Powered by Vontier': {brand_text!r}")
    return brand_text


def _brand_logo_check(doc, footers: list[FooterInfo], page_texts: list[tuple[int, str]], rules: dict, log: list[str]) -> CheckResult:
    # Use the footer title from page 1 — same source as bookmark check.
    # Fall back to TOC title only if footer title is unavailable.
    footer_title = next(
        (f.title_text for f in footers if f.page == 1 and f.title_text),
        ""
    )
    if not footer_title:
        footer_titles = [f.title_text for f in footers if f.title_text]
        footer_title = Counter(footer_titles).most_common(1)[0][0] if footer_titles else ""
    if not footer_title:
        toc = doc.get_toc(simple=True)
        footer_title = toc[0][1].strip() if toc else ""
    _log(log, f"[QUALITY] brand detection using title={footer_title!r}")
    expected_brand = _detect_expected_brand(footer_title, rules, log)
    if not expected_brand:
        return CheckResult(
            id="brand_logo",
            title="Brand Logo Check",
            status="not_checked",
            summary="Could not determine the expected brand from document text.",
        )

    dpi = int(((rules.get("ocr") or {}).get("dpi", 200)))
    lang = (rules.get("ocr") or {}).get("language", "eng")
    ocr_ok = _ocr_available(rules, log)

    findings: list[Finding] = []
    status = "pass"
    summary_parts: list[str] = [f"Expected brand: {expected_brand}."]

    # ── First page — full top strip ───────────────────────────────────────────
    first_page = doc[0]
    first_rect = first_page.rect
    header_clip = first_rect.__class__(0, 0, first_rect.width, first_rect.height * 0.22)
    header_png = _render_region(doc, 1, header_clip, dpi)
    region_mode = _region_color_mode(header_png)

    if ocr_ok:
        brand_above = _brand_above_vontier(header_png, lang, log)
        brand_found = bool(brand_above and expected_brand.casefold() in brand_above)
        _log(log, f"[QUALITY] text above vontier on first page: {brand_above!r} brand_found={brand_found}")
        if brand_found:
            summary_parts.append(f'First page: logo "{expected_brand}" confirmed via OCR.')
        else:
            status = "fail"
            summary_parts.append(f'First page: logo "{expected_brand}" not detected in top header region.')
            findings.append(
                Finding(
                    page=1,
                    severity="error",
                    message=f'Expected brand name "{expected_brand}" not found in first page header.',
                    evidence=f"brand_above={brand_above!r}",
                )
            )
    else:
        status = "not_checked"
        summary_parts.append("OCR unavailable; first page logo check skipped.")

    # ── Last page — bottom-left quadrant ─────────────────────────────────────
    last_page_num = len(doc)
    last_page = doc[last_page_num - 1]
    last_rect = last_page.rect
    last_clip = last_rect.__class__(
        0,
        last_rect.height * 0.78,
        last_rect.width * 0.30,
        last_rect.height,
    )
    last_png = _render_region(doc, last_page_num, last_clip, dpi)

    if ocr_ok:
        last_brand_above = _brand_above_vontier(last_png, lang, log)
        last_brand_found = bool(last_brand_above and expected_brand.casefold() in last_brand_above)
        _log(log, f"[QUALITY] text above vontier on last page: {last_brand_above!r} brand_found={last_brand_found}")
        if last_brand_found:
            summary_parts.append(f'Last page: logo "{expected_brand}" confirmed via OCR.')
        else:
            if status == "pass":
                status = "fail"
            summary_parts.append(f'Last page: logo "{expected_brand}" not detected in bottom-left region.')
            findings.append(
                Finding(
                    page=last_page_num,
                    severity="error",
                    message=f'Expected brand name "{expected_brand}" not found in last page bottom-left.',
                    evidence=f"brand_above={last_brand_above!r}",
                )
            )

    # ── Tagline check (first page + last page) ──────────────────────────────
    tagline_required = " ".join((rules.get("required_taglines") or {}).get("all", []))

    if tagline_required:
        for _page_num, _page_label, _page_ocr_bytes in [
            (1,            "First page", header_png),
            (last_page_num, "Last page",  last_png),
        ]:
            _tagline_present = tagline_required in _normalize_text(
                " ".join(text for _, text in page_texts[:2])
            ) if _page_num == 1 else False
            _source = "pdf_text" if _tagline_present else "ocr"
            if not _tagline_present and ocr_ok:
                _tagline_ocr = _normalize_text(_ocr_image_bytes(_page_ocr_bytes, lang, log))
                _tagline_present = bool(tagline_required and tagline_required in _tagline_ocr)
            if _tagline_present:
                summary_parts.append(f'"{tagline_required}" detected on {_page_label} via {_source}.')
            else:
                if status not in {"not_checked"}:
                    status = "fail"
                findings.append(
                    Finding(
                        page=_page_num,
                        severity="error" if status == "fail" else "warning",
                        message=f'Required "Powered by Vontier" tagline not detected on {_page_label}.',
                        evidence=f"source={_source}",
                    )
                )

    # ── Color mode check ──────────────────────────────────────────────────────
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
        if status == "pass":
            status = "warn"
        findings.append(
            Finding(
                page=1,
                severity="warning",
                message="Angi branding did not show obvious blue in the detected logo/header region.",
                evidence=f"detected_color_mode={region_mode}",
            )
        )

    _log(log, f"[QUALITY] brand/logo: {status} brand={expected_brand} color={region_mode}")
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
    _speed = rules.get("_speed") or {}
    _sub = (_speed.get("sub_progress") or (lambda d: None))
    _total_pages = len(doc)
    for page_num in range(1, _total_pages + 1):
        _sub(f"reading page {page_num}/{_total_pages}")
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
        _speed       = rules.get("_speed") or {}
        dpi          = 100 if _speed.get("fast_dpi") else int(((rules.get("ocr") or {}).get("dpi", 200)))
        lang         = (rules.get("ocr") or {}).get("language", "eng")
        early_exit   = _speed.get("early_exit", False)
        all_pages    = list(range(1, len(doc) + 1))
        if _speed.get("sample_pages") and len(all_pages) > 6:
            step       = max(1, len(all_pages) // 5)
            scan_pages = sorted(set([all_pages[0], all_pages[-1]] + all_pages[1:-1:step]))
        else:
            scan_pages = all_pages
        _sub = (_speed.get("sub_progress") or (lambda d: None))
        for page_num in scan_pages:
            _sub(f"scanning page {page_num}/{len(doc)}")
            page = doc[page_num - 1]
            _img = _render_region(doc, page_num, page.rect, dpi)
            ocr_text = _normalize_text(_ocr_image_bytes(_img, lang, log))
            _img = None
            for key in keywords:
                if _normalize_text(key) in ocr_text:
                    source = "ocr"
                    findings.append(
                        Finding(page=page_num, severity="info", message=f"Potential watermark detected via OCR keyword: {key}", evidence=key)
                    )
                    if early_exit:
                        break
                    break
            if early_exit and findings:
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
    _speed = rules.get("_speed") or {}
    _sub = (_speed.get("sub_progress") or (lambda d: None))
    _total_pages = len(doc)
    for page_num in range(1, _total_pages + 1):
        _sub(f"checking vectors page {page_num}/{_total_pages}")
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
    _speed = rules.get("_speed") or {}
    if not candidate_pages and not _speed.get("skip_raster_change_bar"):
        dpi        = 100 if _speed.get("fast_dpi") else int(((rules.get("ocr") or {}).get("dpi", 200)))
        dark_ratio = float(((rules.get("matching_thresholds") or {}).get("dark_margin_ratio", 0.55)))
        early_exit = _speed.get("early_exit", False)
        scan_pages = list(range(1, len(doc) + 1))
        _sub = (_speed.get("sub_progress") or (lambda d: None))
        for page_num in scan_pages:
            _sub(f"scanning page {page_num}/{len(doc)}")
            page = doc[page_num - 1]
            png = _render_region(doc, page_num, page.rect, dpi)
            _hit = _has_dark_margin_bar(png, "left", dark_ratio) or _has_dark_margin_bar(png, "right", dark_ratio)
            png = None
            if _hit:
                source = "raster"
                candidate_pages.add(page_num)
                findings.append(
                    Finding(page=page_num, severity="info", message="Potential change bar detected from raster margin analysis.", evidence="dark margin stripe")
                )
                if early_exit:
                    break

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
    progress_callback=None,
    fast_dpi: bool = False,
    skip_raster_change_bar: bool = False,
    early_exit: bool = False,
    sample_pages: bool = False,
) -> QualityReport:
    """
    Run document quality checks for a text-based PDF.
    """
    log = debug_log if debug_log is not None else []
    _log(log, f"[QUALITY] starting quality check file_bytes={len(file_bytes)} page_range={page_range!r}")

    import fitz  # type: ignore
    import pdfplumber  # type: ignore

    rules = _load_quality_rules(log)
    rules["_speed"] = {
        "fast_dpi":              fast_dpi,
        "skip_raster_change_bar": skip_raster_change_bar,
        "early_exit":            early_exit,
        "sample_pages":          sample_pages,
        "sub_progress":          None,
    }
    _log(log, f"[QUALITY] speed options: fast_dpi={fast_dpi} skip_raster_change_bar={skip_raster_change_bar} early_exit={early_exit} sample_pages={sample_pages}")
    checks: list[CheckResult] = []
    footers: list[FooterInfo] = []
    page_texts: list[tuple[int, str]] = []

    _extract_start = time.time()
    import fitz as _fitz_pre  # type: ignore

    # Phase 1 — fast text extraction via fitz (handles image-heavy pages well)
    _fitz_doc = _fitz_pre.open(stream=file_bytes, filetype="pdf")
    total_pages = len(_fitz_doc)
    page_indices = _parse_page_range(page_range, total_pages)
    pages_checked = (
        [idx + 1 for idx in sorted(page_indices)]
        if page_indices is not None
        else list(range(1, total_pages + 1))
    )
    total_chars = 0
    _fitz_texts: dict[int, str] = {}
    for zero_idx in range(total_pages):
        page_num = zero_idx + 1
        if progress_callback is not None:
            progress_callback(-1, total_pages, "Pre-extraction", None, detail=f"extracting text page {page_num}/{total_pages}")
        _fpage = _fitz_doc[zero_idx]
        text = _fpage.get_text("text") or ""
        total_chars += len(text)
        _fitz_texts[page_num] = text
    _fitz_doc.close()

    if total_chars < 50:
        raise ExtractorError(
            "No extractable text found. This appears to be a scanned PDF. "
            "Please supply a text-based (digital) PDF."
        )

    # Phase 2 — footer extraction via pdfplumber (needs extract_words bounding boxes)
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for zero_idx, page in enumerate(pdf.pages):
            page_num = zero_idx + 1
            if page_indices is not None and zero_idx not in page_indices:
                continue
            if progress_callback is not None:
                progress_callback(-1, total_pages, "Pre-extraction", None, detail=f"extracting footer page {page_num}/{total_pages}")
            page_texts.append((page_num, _fitz_texts.get(page_num, "")))
            footers.append(_extract_footer_info(page, page_num))

    _extract_elapsed = time.time() - _extract_start
    _log(log, f"[QUALITY] pre-extraction complete: {total_pages} pages in {_extract_elapsed:.2f}s")

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        if pages_checked:

            _ORDERED_CHECKS = [
                ("Footer Month/Year Consistency",    lambda: _footer_consistency_check(footers, log)),
                ("Bookmark Title Matches Footer",     lambda: _bookmark_footer_check(doc, footers, log)),
                ("Brand Logo Check",                  lambda: _brand_logo_check(doc, footers, page_texts, rules, log)),
                ("Curly Quotes Check",                lambda: _straight_quotes_check(page_texts, log)),
                ("Watermark Check",                   lambda: _watermark_check(doc, rules, log)),
                ("Change Bar Check",                  lambda: _change_bar_check(doc, rules, log)),
                ("Even Page Count",                   lambda: _even_page_count_check(len(pages_checked), log)),
                ("Blank Page Notice",                 lambda: _blank_page_notice_check(page_texts, pages_checked[-1], log)),
            ]
            _total = len(_ORDERED_CHECKS)
            for _i, (_check_title, _check_fn) in enumerate(_ORDERED_CHECKS):
                if progress_callback is not None:
                    progress_callback(_i, _total, _check_title, None)
                if progress_callback is not None:
                    def _make_sub(i=_i, t=_check_title):
                        def _sub(detail: str):
                            progress_callback(_i, _total, t, None, detail=detail)
                        return _sub
                    rules["_speed"]["sub_progress"] = _make_sub()
                _result = _check_fn()
                checks.append(_result)
                gc.collect()
                if progress_callback is not None:
                    progress_callback(_i, _total, _check_title, _result)
    finally:
        doc.close()

    report = QualityReport(
        overall_status=_overall_status(checks),
        checks=checks,
        page_count=len(pages_checked),
        pages_checked=pages_checked,
        debug_log=log,
    )
    _total_elapsed = time.time() - _extract_start
    _log(log, f"[QUALITY] complete overall={report.overall_status} checks={len(checks)} total_time={_total_elapsed:.2f}s")
    return report
