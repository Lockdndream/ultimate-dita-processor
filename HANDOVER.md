# HANDOVER.md — Ultimate DITA Processor

Current project state as of 2026-04-05.

---

## What This Project Is

A unified Streamlit web tool that:
1. Converts text-based PDF and DOCX files to **DITA 2.0 XML** (original DITA Converter)
2. Optionally extracts figure images from PDFs and processes them (padding, border, scaling, format conversion) before including them in the ZIP output

Target users: Technical documentation professionals at Gilbarco converting FrameMaker-generated manuals.

- **Live app**: https://dita-converter-testnaipat.streamlit.app/
- **Repo**: https://github.com/Lockdndream/dita-converter
- **Run locally**: `streamlit run ui/app.py` (use Python 3.11)

---

## Directory Structure

```
ultimate-Dita-Processor/
├── agents/
│   ├── extractor.py        # PDF/DOCX → Content Tree (1 084 lines)
│   ├── mapper.py           # Block annotation via YAML rules (279 lines)
│   ├── generator.py        # DITA 2.0 XML serialisation (815 lines)
│   ├── validator.py        # Well-formedness + stats (221 lines)
│   └── image_processor.py  # Image scale/pad/border/convert (182 lines)
├── config/
│   └── mapping_rules.yaml  # Gilbarco mapping profile
├── ui/
│   └── app.py              # Streamlit single-page UI (636 lines)
├── .streamlit/
│   └── config.toml         # Dark theme (#0c0c0c bg, #c8ff00 accent, monospace font)
├── image_processor_src/    # Original standalone image processor (reference only)
├── sample_outputs/         # Reference DITA output samples
├── requirements.txt
├── CLAUDE.md               # AI assistant context (locked architecture decisions)
└── COMMANDS.md             # Git and build quick-reference
```

---

## Pipeline Architecture

```
PDF/DOCX bytes
      │
      ▼
[EXTRACTOR] agents/extractor.py
  extract_pdf(file_bytes, page_range, extract_images, debug_log)
  extract_docx(file_bytes, image_folder)
      │
      │  List of block dicts:
      │  { type, text, level, is_header, rows, metadata, dita_element }
      │
      ▼
[MAPPER] agents/mapper.py
  Mapper().map(blocks)
      │
      │  Same list, every block now has dita_element set
      │
      ▼
[IMAGE PROCESSING] agents/image_processor.py  (PDF + extract_images only)
  _build_media(blocks, ...)  — called from ui/app.py
  Sets block["metadata"]["image_href"] = "media/{filename}"
  Returns { filename: processed_bytes }
      │
      ▼
[GENERATOR] agents/generator.py
  Generator().generate(blocks) → [(filename, xml_str), ...]
  Generator().generate_ditamap(topic_files, map_title)
  Generator().generate_bookmap(topic_files, map_title)
      │
      ▼
[VALIDATOR] agents/validator.py
  Validator().validate(xml_str, blocks, filename) → ValidationResult
```

---

## Key Technical Decisions (locked — see CLAUDE.md for full list)

| ID | Decision |
|---|---|
| D-001 | DITA 2.0 namespace: `https://docs.oasis-open.org/dita/ns/2.0` |
| D-003 | Rule-based pipeline — no LLM at runtime |
| D-007 | Per-topic splitting at H1 boundaries |
| D-012 | Non-intro topics → `<dita>` (ditabase) root |
| D-013 | Introduction topic → `<concept>` with `<section>` for H2/H3 |
| D-014 | Appendix topics → always `<reference>` |

**Never generate `<div>`. Never emit `<p>` for unknown blocks — drop them silently.**

---

## Image Extraction — How It Works

Controlled by the `extract_images: bool` parameter on `extract_pdf()`.

### Step 1 — Caption tagging (extractor.py)

During PDF extraction, every block matching `^Figure\s+\d[\d\-\.]*\s*:` is tagged with:
```python
metadata["_page_idx"] = page_idx   # pdfplumber page index (0-based)
metadata["_fig_top"]  = top        # Y coordinate of caption top (pt, from page top)
```
These keys are internal and cleaned up after image attachment.

Figure numbering formats handled:
- `Figure 1:` — map/kit documents
- `Figure 1-1:` — bookmap/book documents (chapter-numbered)

### Step 2 — Image region detection (`_attach_pdf_images`)

Called at the end of `extract_pdf()` when `extract_images=True`. Uses **PyMuPDF (fitz)**.

**Key insight**: FrameMaker documents are inconsistent — some place images above captions, others below. The function measures the empty gap on both sides of each caption and renders whichever is larger.

```
For each caption at (cap_y0, cap_y1):
  above_top = bottom of last non-blank text block before cap_y0
  below_bot = top of first non-blank text block after cap_y1
              (bounded by adjacent figure captions and page edges)

  gap_above = cap_y0 − above_top
  gap_below = below_bot − cap_y1

  if gap_above >= gap_below:  render (above_top+2, cap_y0-2)
  else:                       render (cap_y1+2, below_bot-2)

  minimum gap to render: 20pt
```

Render call: `page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=clip, alpha=False)`

Result stored as `metadata["image_bytes"]` (PNG bytes) and `metadata["image_ext"]` (`.png`).

### Step 3 — Image processing (`_build_media` in ui/app.py)

After extraction, `_build_media()` runs `process_image()` on each figure's bytes:
- Proportional scale to fit a preset canvas (portrait/landscape max/indent at 96 DPI)
- Add white padding (default 5px)
- Add black border (default 2px)
- Format convert (keep/PNG/JPEG/WEBP)

Sets `block["metadata"]["image_href"] = "media/{slug}.png"` in-place so the Generator picks it up.

### Step 4 — ZIP output

`media/{filename}` entries are written alongside `.dita` files in the download ZIP.
Generator emits `<image href="media/filename.png"/>` inside `<fig>` elements.

---

## Coordinate Systems

Both pdfplumber and PyMuPDF use **top-left origin, Y increases downward** — values are compatible. No coordinate conversion needed between the two libraries.

- `fig_top` from pdfplumber ≈ `y0` from `pg.get_text("blocks")` (within ~5pt)
- Page height: 792pt for US Letter

---

## UI (ui/app.py)

Single-page layout — **no multi-step wizard, no session state for widget values**.

All widget values are read as local Python variables at button-click time. This was a deliberate architectural choice after a multi-step wizard caused session state timing bugs where `extract_images` was not being passed correctly to `extract_pdf()`.

### Layout

```
[Left column]                    [Right column]
  File uploader                    Pipeline stage indicators
  Document type radio              Results tabs:
  Page range text input              🗺️ DITA Map   (topic list + export)
  Extract images toggle              📄 Topic XML  (code viewer)
  └─ Image options (if on):          ✅ Validation (errors/warnings)
       Scale preset                  📊 Stats      (word/section counts)
       Padding / border              🪲 Debug Log  (pipeline trace)
       Pad/border colour
       Format convert
  DOCX media folder (if DOCX)
  Convert button
```

Only `st.session_state.results` persists across reruns (the completed conversion output). All other state is ephemeral.

### Theme

Defined entirely in `.streamlit/config.toml`:
```toml
backgroundColor = "#0c0c0c"
secondaryBackgroundColor = "#111111"
textColor = "#e2e2e2"
primaryColor = "#c8ff00"
font = "monospace"
```
No CSS injection — Streamlit strips `<style>` tags from `st.markdown()`.

---

## Debug Logging

`extract_pdf()` accepts an optional `debug_log: list` parameter. When provided, structured trace messages are appended covering:
- `extract_images` flag value at call time
- Every figure caption detected (page index, Y position, text)
- Every image region computed (gap_above, gap_below, chosen side, clip rect, rendered size)
- Any fitz import or render errors

The UI writes the full log to `dita_converter_debug.log` in the project root after each conversion, and exposes it in the **🪲 Debug Log** tab with a download button.

---

## Dependencies

```
pdfplumber==0.10.4      # PDF text + table extraction
python-docx==1.1.2      # DOCX extraction
PyYAML==6.0.2           # mapping_rules.yaml
lxml==5.3.1             # DITA XML serialisation + validation
streamlit==1.35.0       # UI
protobuf==3.20.3        # Streamlit dependency pin
pymupdf>=1.23.0         # PDF page rendering for image extraction (fitz)
pillow>=10.0.0          # Image processing
```

**Important**: The project runs under **Python 3.11** (Streamlit's interpreter at `C:\Users\Admin\AppData\Local\Programs\Python\Python311`). PyMuPDF must be installed into this interpreter specifically:

```cmd
C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe -m pip install "pymupdf>=1.23.0"
```

The system also has Python 3.14 at `C:\Python314\python.exe` (default `python` command in shell). Installing packages there will NOT be seen by Streamlit.

---

## Known Issues / Recent Fixes

### Fixed this session

| Issue | Root cause | Fix |
|---|---|---|
| Images not extracted | PyMuPDF installed in Python 3.14, Streamlit runs Python 3.11 | Installed PyMuPDF into Python 3.11 |
| Images extracted wrong (random page regions) | Fixed 350pt crop always looked above caption; Gilbarco docs put image below caption | Replaced with adaptive gap-detection algorithm (measures above + below, picks larger) |
| Images not extracted at all (previous session) | Multi-step wizard: `extract_images` toggle state lost between step transitions | Rewrote UI as single-page layout, all values read as local variables at convert time |
| Stray CSS rendered as text | `st.markdown("<style>...</style>")` — Streamlit strips `<style>` tags | Removed all CSS injection; theme delegated to `config.toml` |

### Active limitations

- **DOCX image extraction**: Requires user to manually extract the `.docx` and provide the `word/media` folder path. No automatic extraction.
- **Multi-page figures**: A figure whose image spans a page boundary (caption on one page, image continues to next) will only capture the portion on the caption's page.
- **Scanned PDFs**: Not supported — pdfplumber finds no extractable text and raises `ExtractorError`.
- **Figure caption format**: Only `Figure N:` and `Figure N-N:` patterns are detected. Non-standard captions (e.g. `Fig. 1`) are not matched.

---

## Backlog

| ID | Feature | Priority |
|---|---|---|
| B-003 | Batch conversion (multi-file, folder input, master ditamap) | Medium |
| B-005 | Mapping profile selector in UI | Low |
| B-006 | FrameMaker 17 import workflow guide | Low |

---

## Sample PDFs (in project root)

| File | Doc type | Notes |
|---|---|---|
| `MDE-5624B.pdf` | Bookmap (Figure N-N: numbering) | 84 pages, image below caption |
| `MDE-5643C.pdf` | Bookmap | For testing |
| `MDE-5648B.pdf` | Map (Figure N: numbering) | 56 pages, image below caption, used for image extraction debugging |
| `MDE-5767A.pdf` | Map | For testing |
