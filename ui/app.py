"""
ui/app.py
DITA Converter Tool — Streamlit UI

Dark theme based on the Image Processor tool design language:
  bg #0c0c0c · surface #111111 · accent #c8ff00 · font DM Mono

Single-page layout: options on the left, results on the right.
All widget values are read directly at conversion time — no step
transitions, no session-state timing issues.
"""

from __future__ import annotations

import io
import re
import sys
import zipfile
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as _stc

_ROOT     = Path(__file__).parent.parent
_COMP_DIR = Path(__file__).parent / "crop_component"

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_crop_widget = _stc.declare_component("crop_widget", path=str(_COMP_DIR))

from agents.extractor import extract_pdf, extract_docx, ExtractorError  # noqa
from agents.mapper    import Mapper                                       # noqa
from agents.generator import Generator                                    # noqa
from agents.validator import Validator                                    # noqa
from agents.image_processor import (                                      # noqa
    process_image, IMAGE_SCALE_PRESETS, SCALE_PRESET_LABELS,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="DITA Converter",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown(
        '<div style="font-family:sans-serif;font-size:20px;font-weight:800;'
        'color:#fff;letter-spacing:0.04em;margin-bottom:2px;">⬡ DITA Converter</div>'
        '<div style="font-size:10px;color:#555;letter-spacing:0.12em;">PDF · DOCX → DITA 2.0</div>',
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown("""
**Mapping profile:** Gilbarco
**DITA version:** 2.0
**Multi-topic:** enabled
**Map types:** `.ditamap` · `.bookmap`
""")
    st.divider()
    st.markdown("""
`[EXTRACTOR]` → Parse structure
`[MAPPER]` → Apply YAML rules
`[GENERATOR]` → Build DITA 2.0 XML
`[VALIDATOR]` → Check & report
""")
    st.divider()
    st.caption("Supported: `.pdf`, `.docx`")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BADGE_COLORS = {
    "task":      ("rgba(200,255,0,0.12)",  "#c8ff00"),
    "concept":   ("rgba(99,195,255,0.12)", "#63c3ff"),
    "reference": ("rgba(255,160,80,0.12)", "#ffa050"),
    "topic":     ("rgba(140,140,160,0.12)","#8c8ca0"),
}
_BADGE_BASE = "border-radius:4px;padding:2px 8px;font-size:0.72em;font-weight:600;font-family:monospace;"

def _badge(ttype: str) -> str:
    bg, color = _BADGE_COLORS.get(ttype, _BADGE_COLORS["topic"])
    return f'<span style="background:{bg};color:{color};{_BADGE_BASE}">{ttype}</span>'

def _topic_type_from_xml(xml_str: str) -> str:
    _VALID = {"concept", "task", "reference", "topic"}
    try:
        from lxml import etree as _et
        clean = "\n".join(
            l for l in xml_str.splitlines()
            if not l.strip().startswith("<?") and not l.strip().startswith("<!DOCTYPE")
        )
        root = _et.fromstring(clean.encode())
        local = _et.QName(root.tag).localname
        return local if local in _VALID else "topic"
    except Exception:
        return "topic"

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

def _apply_manual_crop(
    img_bytes: bytes,
    top: int, bottom: int, left: int, right: int,
) -> bytes:
    """
    Trim the given number of pixels from each edge of img_bytes.
    Returns original bytes unchanged if all values are 0.
    Returns original bytes unchanged if crop would produce empty image.
    """
    if top == 0 and bottom == 0 and left == 0 and right == 0:
        return img_bytes
    from PIL import Image as _pil
    import io as _io
    img = _pil.open(_io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    l = left
    t = top
    r = max(l + 1, w - right)
    b = max(t + 1, h - bottom)
    if r <= l or b <= t:
        return img_bytes   # invalid crop — return original
    cropped = img.crop((l, t, r, b))
    buf = _io.BytesIO()
    cropped.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()

def _get_fig_index(blocks: list[dict], target: dict) -> int:
    fig_blocks = [
        b for b in blocks
        if b.get("type") == "figure"
        and b.get("metadata", {}).get("image_bytes_raw")
    ]
    try:
        return fig_blocks.index(target)
    except ValueError:
        return -1

# ---------------------------------------------------------------------------
# Build media dict — called directly at conversion time with widget values
# ---------------------------------------------------------------------------

def _build_media(
    blocks: list[dict],
    crop: bool,
    padding_px: int,
    border_px: int,
    border_colour: str,
    pad_colour: str,
    scale_preset: str,
    convert_format: str,
) -> dict[str, bytes]:
    """
    For every figure block that has image_bytes in its metadata:
      - Process the image with the supplied settings
      - Set block["metadata"]["image_href"] = "media/{filename}"
    Returns {filename: bytes}.
    """
    media: dict[str, bytes] = {}
    seen:  dict[str, int]   = {}

    target_w, target_h = 0, 0
    if scale_preset != "no_scale" and scale_preset in IMAGE_SCALE_PRESETS:
        target_w, target_h = IMAGE_SCALE_PRESETS[scale_preset]

    for block in blocks:
        if block.get("type") != "figure":
            continue
        meta      = block.get("metadata", {})
        img_bytes = meta.get("image_bytes")
        img_ext   = meta.get("image_ext", ".png")
        if not img_bytes:
            continue

        # Apply any manual crop from the Image Review editor.
        # Always start from image_bytes_raw so re-applying crops is idempotent.
        fig_idx = _get_fig_index(blocks, block)
        crops   = st.session_state.get("image_crops", {}).get(fig_idx, {})
        if crops and any(crops.get(k, 0) > 0 for k in ("top", "bottom", "left", "right")):
            img_bytes = _apply_manual_crop(
                meta.get("image_bytes_raw", img_bytes),
                crops.get("top",    0),
                crops.get("bottom", 0),
                crops.get("left",   0),
                crops.get("right",  0),
            )

        base_slug = _slugify(block.get("text", "")) or "image"
        if base_slug in seen:
            seen[base_slug] += 1
            base_slug = f"{base_slug}_{seen[base_slug]}"
        else:
            seen[base_slug] = 0

        processed, final_ext = process_image(
            img_bytes, img_ext,
            crop=crop,
            padding_px=padding_px,
            border_px=border_px,
            border_colour=border_colour,
            pad_colour=pad_colour,
            target_width_px=target_w,
            target_height_px=target_h,
            convert_format=convert_format,
        )
        filename = base_slug + final_ext
        media[filename] = processed
        meta["image_href"] = f"media/{filename}"

    return media

# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

if "results" not in st.session_state:
    st.session_state.results = None
if "image_crops" not in st.session_state:
    st.session_state.image_crops = {}

# ---------------------------------------------------------------------------
# Crop dialog — modal overlay, opened when user clicks a thumbnail's Crop button.
# @st.dialog reruns are scoped to the dialog: no page-wide overlay during drag.
# st.rerun() inside closes the dialog and does a full app rerun (Apply/Reset only).
# ---------------------------------------------------------------------------

@st.dialog("✏️ Crop image", width="large")
def _crop_dialog(fig_idx: int, caption: str, raw_bytes: bytes,
                 orig_w: int, orig_h: int) -> None:
    import base64 as _b64

    st.caption(f"{orig_w}×{orig_h}px  ·  {caption}")

    _dc = st.session_state.image_crops.get(fig_idx, {})
    _ds = st.session_state.get(f"crop_{fig_idx}_top",    _dc.get("top",    0))
    _db = st.session_state.get(f"crop_{fig_idx}_bottom", _dc.get("bottom", 0))
    _dl = st.session_state.get(f"crop_{fig_idx}_left",   _dc.get("left",   0))
    _dr = st.session_state.get(f"crop_{fig_idx}_right",  _dc.get("right",  0))

    _dresult = _crop_widget(
        img_b64=_b64.b64encode(raw_bytes).decode(),
        top=_ds, bottom=_db, left=_dl, right=_dr,
        key=f"dlg_cropper_{fig_idx}",
        default=None,
    )
    if _dresult is not None:
        _ds = int(_dresult.get("top",    _ds))
        _db = int(_dresult.get("bottom", _db))
        _dl = int(_dresult.get("left",   _dl))
        _dr = int(_dresult.get("right",  _dr))
        st.session_state[f"crop_{fig_idx}_top"]    = _ds
        st.session_state[f"crop_{fig_idx}_bottom"] = _db
        st.session_state[f"crop_{fig_idx}_left"]   = _dl
        st.session_state[f"crop_{fig_idx}_right"]  = _dr

    _dkw = max(0, orig_w - _dl - _dr)
    _dkh = max(0, orig_h - _ds - _db)
    if _dkw <= 0 or _dkh <= 0:
        st.warning("⚠️ Crop values eliminate the image.")
    else:
        _dcomm = st.session_state.image_crops.get(fig_idx, {})
        _dapp  = (
            f"  ·  applied "
            f"{orig_w - _dcomm.get('left',0) - _dcomm.get('right',0)}"
            f"×{orig_h - _dcomm.get('top',0) - _dcomm.get('bottom',0)}px"
            if any(_dcomm.get(k, 0) > 0 for k in ("top","bottom","left","right")) else ""
        )
        st.caption(f"Staged → {_dkw}×{_dkh}px{_dapp}")

    _da, _db2 = st.columns(2)
    with _da:
        if st.button("✓ Apply crop", type="primary",
                     use_container_width=True, key=f"dlg_apply_{fig_idx}"):
            st.session_state.image_crops[fig_idx] = {
                "top": _ds, "bottom": _db, "left": _dl, "right": _dr,
            }
            st.rerun()
    with _db2:
        if st.button("↺ Reset", use_container_width=True,
                     key=f"dlg_reset_{fig_idx}"):
            for _e in ("top", "bottom", "left", "right"):
                st.session_state.pop(f"crop_{fig_idx}_{_e}", None)
            st.session_state.image_crops[fig_idx] = {
                "top": 0, "bottom": 0, "left": 0, "right": 0,
            }
            st.rerun()


# ---------------------------------------------------------------------------
# Images tab fragment — thumbnail grid only; crop opens via _crop_dialog modal.
# @st.fragment scopes non-dialog reruns to this function alone.
# ---------------------------------------------------------------------------

@st.fragment
def _images_tab(res: dict) -> None:
    import io as _io
    import base64 as _b64
    from PIL import Image as _pil_img

    if not res.get("extract_images") or not any(
        b.get("type") == "figure" and b.get("metadata", {}).get("image_bytes_raw")
        for b in res["blocks"]
    ):
        st.info("No images extracted. Enable 'Extract images' and convert again.")
        return

    _all_figs = [
        b for b in res["blocks"]
        if b.get("type") == "figure"
        and b.get("metadata", {}).get("image_bytes_raw")
    ]
    st.caption(f"{len(_all_figs)} image(s) extracted  ·  click ✏️ Crop to edit")

    _COLS = 4
    for _rs in range(0, len(_all_figs), _COLS):
        _tcols = st.columns(_COLS)
        for _ci, _fig in enumerate(_all_figs[_rs : _rs + _COLS]):
            _fi     = _rs + _ci
            _meta   = _fig.get("metadata", {})
            _raw    = _meta.get("image_bytes_raw", _meta.get("image_bytes"))
            _cap    = _fig.get("text", "")
            _cshort = (_cap[:28] + "…") if len(_cap) > 28 else _cap

            _crops     = st.session_state.image_crops.get(_fi, {})
            _is_cropped = any(_crops.get(k, 0) > 0 for k in ("top","bottom","left","right"))
            _tsrc = _apply_manual_crop(
                _raw,
                _crops.get("top", 0), _crops.get("bottom", 0),
                _crops.get("left", 0), _crops.get("right", 0),
            ) if _is_cropped else _raw

            try:
                _pt    = _pil_img.open(_io.BytesIO(_tsrc))
                _ratio = 160 / max(_pt.width, 1)
                _pt    = _pt.resize((160, int(_pt.height * _ratio)), _pil_img.LANCZOS)
                _tb    = _io.BytesIO()
                _pt.save(_tb, format="PNG")
                _tw, _th = _pt.size
                _tb64  = _b64.b64encode(_tb.getvalue()).decode()
                _ow, _oh = _pil_img.open(_io.BytesIO(_raw)).size
            except Exception:
                _tw, _th, _tb64, _ow, _oh = 0, 0, "", 1, 1

            with _tcols[_ci]:
                if _tb64:
                    st.markdown(
                        f'<img src="data:image/png;base64,{_tb64}" width="160" '
                        f'style="border-radius:4px;display:block;margin-bottom:4px;">',
                        unsafe_allow_html=True,
                    )
                st.caption(f"{_cshort}\n{_tw}×{_th}px" + (" ✂️" if _is_cropped else ""))
                if st.button("✏️ Crop", key=f"crop_btn_{_fi}",
                             use_container_width=True):
                    _crop_dialog(_fi, _cap, _raw, _ow, _oh)


# ---------------------------------------------------------------------------
# Layout: left column (options) | right column (output)
# ---------------------------------------------------------------------------

left, right = st.columns([1, 1.7])

# ============================================================================
# LEFT COLUMN — all options
# ============================================================================

with left:
    st.markdown(
        '<div style="font-size:22px;font-weight:800;color:#fff;margin-bottom:4px;">'
        'DITA 2.0 Converter</div>'
        '<div style="font-size:12px;color:#555;margin-bottom:16px;">'
        'Upload a PDF or DOCX to convert to DITA 2.0 XML</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div style="font-size:10px;color:#c8ff00;letter-spacing:0.12em;'
        'margin-bottom:8px;font-family:monospace;">v1.5.5 · substep-support</div>',
        unsafe_allow_html=True,
    )

    # ── File upload ───────────────────────────────────────────────────────────
    uploaded_file = st.file_uploader(
        "Select a PDF or DOCX file",
        type=["pdf", "docx"],
        help="Text-based (digital) PDFs only. Scanned PDFs are not supported.",
    )

    is_pdf = uploaded_file is not None and uploaded_file.name.lower().endswith(".pdf")

    st.divider()

    # ── Document type ─────────────────────────────────────────────────────────
    output_type = st.radio(
        "Document type",
        options=["Map (Kit documents)", "Bookmap (Book documents)"],
        horizontal=True,
    )
    is_bookmap = output_type == "Bookmap (Book documents)"

    # ── Page range (PDF only) ─────────────────────────────────────────────────
    page_range = st.text_input(
        "Page range (PDF only, optional)",
        placeholder="e.g. 1-5, 8, 12-15  (leave blank for all)",
    )
    if page_range and not re.match(r"^[\d\s,\-]+$", page_range):
        st.warning("⚠️ Invalid format — use numbers, commas and hyphens only.")

    st.divider()

    # ── Image options ─────────────────────────────────────────────────────────
    if is_pdf:
        extract_images = st.toggle("Extract images from PDF", value=False)
    else:
        extract_images = False

    if extract_images:
        scale_keys = list(SCALE_PRESET_LABELS.keys())
        scale_preset = st.radio(
            "Scale to width",
            options=scale_keys,
            format_func=lambda k: SCALE_PRESET_LABELS[k],
            horizontal=True,
        )
        fmt_keys   = ["keep", "png", "jpeg", "webp"]
        fmt_labels = ["Keep original", "PNG", "JPEG", "WEBP"]
        convert_format = st.radio(
            "Convert format",
            options=fmt_keys,
            format_func=lambda f: fmt_labels[fmt_keys.index(f)],
            horizontal=True,
        )
        apply_border_padding = st.checkbox("Apply border and padding", value=False)
        if apply_border_padding:
            c1, c2 = st.columns(2)
            with c1:
                padding_px    = st.number_input("Padding (px)",  min_value=0, max_value=100, value=5,  step=1)
                border_px     = st.number_input("Border (px)",   min_value=0, max_value=40,  value=2,  step=1)
            with c2:
                border_colour = st.color_picker("Border colour", value="#000000")
                pad_colour    = st.color_picker("Padding colour", value="#ffffff")
        else:
            padding_px    = 0
            border_px     = 0
            border_colour = "#000000"
            pad_colour    = "#ffffff"
    else:
        scale_preset         = "no_scale"
        apply_border_padding = False
        padding_px           = 0
        border_px            = 0
        border_colour        = "#000000"
        pad_colour           = "#ffffff"
        convert_format       = "keep"

    # ── DOCX image folder ─────────────────────────────────────────────────────
    if not is_pdf and uploaded_file is not None:
        st.divider()
        with st.expander("ℹ️ How to provide DOCX images"):
            st.markdown("""
1. Copy your `.docx` → rename to `.zip` → extract
2. Navigate to extracted folder → `word/` → `media/`
3. Paste the full path below
""")
        image_folder = st.text_input(
            "Media folder path",
            placeholder="D:\\path\\to\\extracted\\word\\media",
        )
        if image_folder and not Path(image_folder).is_dir():
            st.warning("⚠️ Folder not found — images will be skipped.")
            image_folder = ""
    else:
        image_folder = ""

    st.divider()

    run_button = st.button(
        "▶  Convert to DITA 2.0",
        type="primary",
        disabled=uploaded_file is None,
        use_container_width=True,
    )

# ============================================================================
# RIGHT COLUMN — pipeline + results
# ============================================================================

with right:
    # ── Run pipeline ──────────────────────────────────────────────────────────
    if run_button and uploaded_file is not None:
        file_bytes = uploaded_file.read()
        file_name  = uploaded_file.name
        status_box = st.empty()

        ph_extractor = st.empty()
        ph_mapper    = st.empty()
        ph_generator = st.empty()
        ph_validator = st.empty()

        def _stage(ph, label, icon, detail=""):
            ph.markdown(f"`[{label}]` {icon} {detail}")

        # Reset manual crop editor state for the new conversion
        st.session_state.image_crops = {}

        try:
            t0 = time.time()
            debug_log: list[str] = [
                f"=== DITA Converter Debug Log ===",
                f"file: {file_name}  size: {len(file_bytes)} bytes",
                f"is_pdf: {is_pdf}  extract_images: {extract_images}  page_range: {page_range!r}",
                f"is_bookmap: {is_bookmap}",
            ]

            _stage(ph_extractor, "EXTRACTOR", "⏳", "Parsing document…")
            blocks = (
                extract_pdf(file_bytes, page_range=page_range,
                            extract_images=extract_images,
                            debug_log=debug_log)
                if is_pdf
                else extract_docx(file_bytes, image_folder=image_folder)
            )
            n_imgs = sum(1 for b in blocks if b.get("metadata", {}).get("image_bytes"))
            debug_log.append(f"[UI] after extract: {len(blocks)} blocks, {n_imgs} with image_bytes")
            _stage(ph_extractor, "EXTRACTOR", "✅",
                   f"{len(blocks)} blocks · {n_imgs} image(s) found")

            # Block type breakdown for diagnosis
            from collections import Counter as _Counter
            _btype_counts = _Counter(b.get("type") for b in blocks)
            _meta_kinds   = _Counter(
                b.get("metadata", {}).get("list_kind")
                for b in blocks if b.get("type") == "list_item"
            )
            debug_log.append(f"[BLOCKS] type breakdown: {dict(_btype_counts)}")
            debug_log.append(f"[BLOCKS] list_item kinds: {dict(_meta_kinds)}")

            # Sample paragraphs that look like numbered items (digit at start)
            _numbered_paras = [
                b.get("text", "")[:80]
                for b in blocks
                if b.get("type") == "paragraph"
                and b.get("text", "")[:4].strip().rstrip(".)").isdigit()
            ]
            if _numbered_paras:
                debug_log.append(
                    f"[BLOCKS] paragraphs starting with digit "
                    f"({len(_numbered_paras)} found — likely undetected steps):"
                )
                for _t in _numbered_paras[:15]:
                    debug_log.append(f"  → {_t!r}")
            else:
                debug_log.append("[BLOCKS] no paragraphs starting with digit found")

            # Sample of all list_item blocks
            _list_items = [b for b in blocks if b.get("type") == "list_item"]
            if _list_items:
                debug_log.append(f"[BLOCKS] list_item sample (first 10):")
                for _b in _list_items[:10]:
                    _kind = _b.get("metadata", {}).get("list_kind", "?")
                    debug_log.append(f"  [{_kind}] {_b.get('text','')[:70]!r}")
            else:
                debug_log.append("[BLOCKS] no list_item blocks found at all")

            _stage(ph_mapper, "MAPPER", "⏳", "Applying YAML mapping rules…")
            blocks = Mapper().map(blocks)
            _stage(ph_mapper, "MAPPER", "✅", "Topic type detected")

            _stage(ph_generator, "GENERATOR", "⏳", "Building DITA 2.0 XML…")

            # Preserve raw render bytes before any processing
            # (only on first run — never overwrite the original)
            if extract_images and n_imgs > 0:
                for _b in blocks:
                    if _b.get("type") == "figure":
                        _m = _b.get("metadata", {})
                        if _m.get("image_bytes") and "image_bytes_raw" not in _m:
                            _m["image_bytes_raw"] = _m["image_bytes"]

            # Process images and set image_href on figure blocks
            media: dict[str, bytes] = {}
            debug_log.append(f"[UI] before _build_media: extract_images={extract_images} n_imgs={n_imgs}")
            if extract_images and n_imgs > 0:
                media = _build_media(
                    blocks,
                    crop=apply_border_padding,
                    padding_px=padding_px,
                    border_px=border_px,
                    border_colour=border_colour,
                    pad_colour=pad_colour,
                    scale_preset=scale_preset,
                    convert_format=convert_format,
                )
                debug_log.append(f"[UI] _build_media produced {len(media)} file(s): {list(media.keys())}")

            gen         = Generator()
            topic_files = gen.generate(blocks, debug_log=debug_log)
            map_title   = Path(file_name).stem.replace("_", " ").replace("-", " ").title()

            map_str  = (gen.generate_bookmap(topic_files, map_title=map_title)
                        if is_bookmap else
                        gen.generate_ditamap(topic_files, map_title=map_title))
            map_name = Path(file_name).stem + ".ditamap"

            _stage(ph_generator, "GENERATOR", "✅",
                   f"{len(topic_files)} topic(s) · {len(media)} media file(s)")

            _stage(ph_validator, "VALIDATOR", "⏳", "Validating XML…")
            validator = Validator()
            validation_results = [
                (fname, xml_str, validator.validate(xml_str, blocks, filename=fname))
                for fname, xml_str in topic_files
            ]
            total_errors   = sum(len(vr.errors)   for _, _, vr in validation_results)
            total_warnings = sum(len(vr.warnings)  for _, _, vr in validation_results)
            elapsed = time.time() - t0
            _stage(ph_validator, "VALIDATOR",
                   "✅" if total_errors == 0 else "⚠️",
                   f"{total_errors} errors · {total_warnings} warnings · {elapsed:.2f}s")

            debug_log.append(f"[UI] pipeline complete in {elapsed:.2f}s")

            # Write log file
            _log_path = _ROOT / "dita_converter_debug.log"
            _log_path.write_text("\n".join(debug_log), encoding="utf-8")

            st.session_state.results = {
                "topic_files":     validation_results,
                "ditamap_str":     map_str,
                "ditamap_name":    map_name,
                "n_topics":        len(topic_files),
                "source_name":     file_name,
                "map_title":       map_title,
                "elapsed":         elapsed,
                "blocks":          blocks,
                "is_bookmap":      is_bookmap,
                "media":           media,
                "debug_log":       debug_log,
                "extract_images":  extract_images,
                "img_build_args":  {
                    "crop":           apply_border_padding,
                    "padding_px":     padding_px,
                    "border_px":      border_px,
                    "border_colour":  border_colour,
                    "pad_colour":     pad_colour,
                    "scale_preset":   scale_preset,
                    "convert_format": convert_format,
                },
            }

        except ExtractorError as exc:
            debug_log.append(f"[UI] ExtractorError: {exc}")
            _log_path = _ROOT / "dita_converter_debug.log"
            _log_path.write_text("\n".join(debug_log), encoding="utf-8")
            ph_extractor.error(f"❌ Extraction failed: {exc}")
            st.info("💡 Only text-based (digital) PDFs are supported.")
            st.session_state.results = None
        except Exception as exc:
            import traceback
            debug_log.append(f"[UI] Exception: {exc}\n{traceback.format_exc()}")
            _log_path = _ROOT / "dita_converter_debug.log"
            _log_path.write_text("\n".join(debug_log), encoding="utf-8")
            status_box.error(f"❌ Unexpected error: {exc}")
            st.session_state.results = None

    # ── Results ───────────────────────────────────────────────────────────────
    if st.session_state.results:
        res          = st.session_state.results
        topic_files  = res["topic_files"]
        ditamap_str  = res["ditamap_str"]
        ditamap_name = res["ditamap_name"]
        n_topics     = res["n_topics"]
        map_title    = res["map_title"]
        is_bookmap   = res.get("is_bookmap", False)
        map_label    = "bookmap" if is_bookmap else "ditamap"

        # Rebuild media if the user has applied manual crops since the last run
        _has_manual_crops = any(
            any(v.get(k, 0) > 0 for k in ("top", "bottom", "left", "right"))
            for v in st.session_state.get("image_crops", {}).values()
        )
        if res.get("extract_images") and _has_manual_crops and res.get("img_build_args"):
            media = _build_media(res["blocks"], **res["img_build_args"])
        else:
            media = res.get("media", {})

        total_errors = sum(len(vr.errors) for _, _, vr in topic_files)
        if total_errors == 0:
            st.markdown(
                '<span style="background:#1c2200;color:#c8ff00;border:1px solid #2e4000;'
                'border-radius:20px;padding:4px 16px;font-size:11px;letter-spacing:0.08em;">'
                '● VALID</span>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<span style="background:#2a0a00;color:#ff6b35;border:1px solid #4a1a00;'
                'border-radius:20px;padding:4px 16px;font-size:11px;letter-spacing:0.08em;">'
                f'● INVALID — {total_errors} error(s)</span>',
                unsafe_allow_html=True,
            )

        st.divider()
        tabs = st.tabs(["🗺️ DITA Map", "📄 Topic XML", "✅ Validation", "📊 Stats", "🖼️ Images", "🪲 Debug Log"])

        # ── TAB 1: DITA Map ──────────────────────────────────────────────────
        with tabs[0]:
            st.subheader(f"📋 {map_title}")
            st.caption(f"{n_topics} topic(s) — check boxes to select, then export")

            selected_indices: list[int] = []
            for i, (fname, xml_str, vr) in enumerate(topic_files):
                ttype       = _topic_type_from_xml(xml_str)
                title       = vr.stats.get("title", fname.replace(".dita", ""))
                words       = vr.stats.get("word_count", 0)
                secs        = vr.stats.get("sections", 0)
                errs        = len(vr.errors)
                warns       = len(vr.warnings)
                status_icon = "🔴" if errs else ("🟡" if warns else "🟢")

                col_chk, col_info = st.columns([0.07, 0.93])
                with col_chk:
                    checked = st.checkbox(
                        label="select", key=f"chk_{i}",
                        value=False, label_visibility="collapsed",
                    )
                with col_info:
                    st.markdown(
                        f'<div style="border:1px solid #2a2a2a;border-radius:8px;'
                        f'padding:10px 14px;margin-bottom:6px;background:#111111;">'
                        f'{status_icon}&nbsp; {_badge(ttype)}&nbsp; '
                        f'<strong>{title}</strong><br/>'
                        f'<small style="opacity:0.5;">'
                        f'{fname} &nbsp;·&nbsp; {words} words'
                        f'{f" &nbsp;·&nbsp; {secs} sections" if secs else ""}'
                        f'</small></div>',
                        unsafe_allow_html=True,
                    )
                if checked:
                    selected_indices.append(i)

            st.divider()
            col_map, col_sel, col_all = st.columns(3)

            with col_map:
                st.download_button(
                    f"⬇ .{map_label}",
                    data=ditamap_str.encode("utf-8"),
                    file_name=ditamap_name,
                    mime="application/xml",
                    use_container_width=True,
                )

            with col_sel:
                n_sel = len(selected_indices)
                if n_sel == 1:
                    i = selected_indices[0]
                    fname, xml_str, _ = topic_files[i]
                    st.download_button(
                        f"⬇ Export {n_sel} topic",
                        data=xml_str.encode("utf-8"),
                        file_name=fname,
                        mime="application/xml",
                        use_container_width=True,
                        type="primary",
                    )
                elif n_sel > 1:
                    sel_files  = [topic_files[i] for i in selected_indices]
                    sel_tuples = [(f, x) for f, x, _ in sel_files]
                    gen_sel    = Generator()
                    scoped_map = (
                        gen_sel.generate_bookmap(sel_tuples, map_title=f"{map_title} (selection)")
                        if is_bookmap else
                        gen_sel.generate_ditamap(sel_tuples, map_title=f"{map_title} (selection)")
                    )
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        for fname, xml_str, _ in sel_files:
                            zf.writestr(fname, xml_str.encode("utf-8"))
                        zf.writestr(
                            ditamap_name.replace(".ditamap", "_selection.ditamap"),
                            scoped_map.encode("utf-8"),
                        )
                        for mname, mbytes in media.items():
                            zf.writestr(f"media/{mname}", mbytes)
                    buf.seek(0)
                    st.download_button(
                        f"⬇ Export {n_sel} topics",
                        data=buf,
                        file_name=ditamap_name.replace(".ditamap", f"_sel{n_sel}.zip"),
                        mime="application/zip",
                        use_container_width=True,
                        type="primary",
                    )
                else:
                    st.button("⬇ Export selected", disabled=True, use_container_width=True)

            with col_all:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for fname, xml_str, _ in topic_files:
                        zf.writestr(fname, xml_str.encode("utf-8"))
                    zf.writestr(ditamap_name, ditamap_str.encode("utf-8"))
                    for mname, mbytes in media.items():
                        zf.writestr(f"media/{mname}", mbytes)
                buf.seek(0)
                zip_name = Path(res["source_name"]).stem + "_dita.zip"
                dl_label = f"⬇ Export all (ZIP){f' + {len(media)} images' if media else ''}"
                st.download_button(
                    dl_label, data=buf, file_name=zip_name,
                    mime="application/zip", use_container_width=True,
                )

            with st.expander("📄 View .ditamap XML"):
                st.code(ditamap_str, language="xml")

        # ── TAB 2: Topic XML ──────────────────────────────────────────────────
        with tabs[1]:
            if n_topics == 1:
                _, xml_str, _ = topic_files[0]
                display = (xml_str if len(xml_str) <= 50_000
                           else xml_str[:50_000] + "\n<!-- truncated -->")
                st.code(display, language="xml")
            else:
                names = [fname for fname, _, _ in topic_files]
                sel   = st.selectbox("Select topic to preview:", names)
                for fname, xml_str, _ in topic_files:
                    if fname == sel:
                        display = (xml_str if len(xml_str) <= 50_000
                                   else xml_str[:50_000] + "\n<!-- truncated -->")
                        st.code(display, language="xml")
                        break

        # ── TAB 3: Validation ─────────────────────────────────────────────────
        with tabs[2]:
            for fname, _, vr in topic_files:
                with st.expander(f"📄 {fname}", expanded=(n_topics == 1)):
                    if vr.errors:
                        for e in vr.errors:
                            st.error(e)
                    if vr.warnings:
                        for w in vr.warnings:
                            st.warning(w)
                    if not vr.errors and not vr.warnings:
                        st.success("Clean — no errors or warnings.")
                    st.code(vr.report, language="text")

        # ── TAB 4: Stats ──────────────────────────────────────────────────────
        with tabs[3]:
            all_stats      = [vr.stats for _, _, vr in topic_files]
            total_words    = sum(s.get("word_count", 0) for s in all_stats)
            total_sections = sum(s.get("sections",   0) for s in all_stats)
            total_notes    = sum(s.get("notes",       0) for s in all_stats)
            total_steps    = sum(s.get("steps",       0) for s in all_stats)
            total_tables   = sum(s.get("tables",      0) for s in all_stats)
            total_figs     = sum(s.get("figures",     0) for s in all_stats)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Topics",   n_topics)
            m2.metric("Words",    total_words)
            m3.metric("Sections", total_sections)
            m4.metric("Notes",    total_notes)

            m5, m6, m7, m8 = st.columns(4)
            m5.metric("Steps",    total_steps)
            m6.metric("Tables",   total_tables)
            m7.metric("Figures",  total_figs)
            m8.metric("Time (s)", f"{res['elapsed']:.2f}")

            if media:
                st.info(f"📎 {len(media)} image(s) included in ZIP under `media/`.")

            if n_topics > 1:
                st.subheader("Per-topic breakdown")
                for fname, xml_str, vr in topic_files:
                    ttype = _topic_type_from_xml(xml_str)
                    s = vr.stats
                    st.markdown(
                        f"**{fname}** `{ttype}` — "
                        f"{s.get('word_count',0)} words · "
                        f"{s.get('sections',0)} sections · "
                        f"{s.get('steps',0)} steps · "
                        f"{s.get('notes',0)} notes"
                    )

            if res["blocks"]:
                fb = res["blocks"][0].get("metadata", {}).get("fallback_count", 0)
                if fb > 0:
                    st.warning(f"⚠️ {fb} block(s) used fallback `<p>`.")

        # ── TAB 5: Images ────────────────────────────────────────────────────
        with tabs[4]:
            _images_tab(res)

        # ── TAB 6: Debug Log ─────────────────────────────────────────────────
        with tabs[5]:
            log_lines = res.get("debug_log", [])
            log_text  = "\n".join(log_lines) if log_lines else "(no log — run a conversion first)"
            st.code(log_text, language="text")
            _log_path = _ROOT / "dita_converter_debug.log"
            st.caption(f"Log also written to: `{_log_path}`")
            st.download_button(
                "⬇ Download log",
                data=log_text.encode("utf-8"),
                file_name="dita_converter_debug.log",
                mime="text/plain",
            )
