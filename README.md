![Python](https://img.shields.io/badge/python-3.11-blue)
![DITA](https://img.shields.io/badge/DITA-2.0-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-active-brightgreen)

# DITA Converter [![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://dita-converter-testnaipat.streamlit.app/)

Converts text-based **PDF** and **DOCX** files into valid **DITA 2.0 XML** via a rule-based pipeline with a Streamlit web UI. Calibrated against Gilbarco technical manuals.

> **DITA Version**: 2.0  
> **Style Reference**: Gilbarco Technical Manuals  
> **Source Analysis**: Passport V24.04 — 42 docs, 64 elements, 1,559 instances

---

## Quick Start

**Requirements:** Python 3.11, pip

\`\`\`bash
git clone https://github.com/Lockdndream/dita-converter.git
cd dita-converter
pip install -r requirements.txt
streamlit run ui/app.py
\`\`\`

Open \`http://localhost:8501\` — upload a PDF or DOCX and download your DITA output.

---

## Pipeline

\`\`\`
Upload PDF or DOCX
        │
        ▼
  ┌─────────────┐   pdfplumber / python-docx
  │  Extractor  │ → Content Tree (block dicts with Y-position ordering)
  └─────────────┘   ROW_SHOW table detection · TM sentinel encoding · bold detection
        │
        ▼
  ┌─────────────┐   config/mapping_rules.yaml
  │   Mapper    │ → Annotated Content Tree (dita_element per block)
  └─────────────┘   Hazard statement classification · UI path detection
        │
        ▼
  ┌─────────────┐   lxml
  │  Generator  │ → DITA 2.0 XML + .ditamap or .bookmap
  └─────────────┘   Per-topic type detection · Ditabase composite output
        │
        ▼
  ┌─────────────┐   lxml well-formedness
  │  Validator  │ → ValidationResult + report per topic
  └─────────────┘
        │
        ▼
  Map view → select topics → download .dita · scoped ZIP · .ditamap · .bookmap
\`\`\`

---

## Features

| Feature | Detail |
|---|---|
| **Multi-topic output** | Each H1 → separate \`.dita\` file |
| **Ditabase composite** | Non-intro topics use \`<dita>\` (ditabase) root with typed child topics |
| **Topic type detection** | \`task\` · \`concept\` · \`reference\` · \`topic\` — per chunk, per H2/H3 |
| **Introduction topics** | H2/H3 → \`<section>\` with \`<title>\` |
| **Appendix topics** | Title starts with "Appendix" → always \`<reference>\` |
| **ROW_SHOW table detection** | FrameMaker-style borderless tables: 2pt header rules, no vertical lines |
| **Multi-row thead** | Straddle/spanning header rows with \`namest\`/\`nameend\` attributes |
| **Hazard statements** | \`IMPORTANT INFORMATION\` / \`WARNING\` / \`CAUTION\` / \`DANGER\` → \`<hazardstatement>\` per ANSI Z535 |
| **Bold retention** | Bold text in paragraphs and table cells → \`<b>\` (stripped in \`<thead>\`) |
| **Trademark markup** | Superscript ® ™ ℠ → \`<tm tmtype="reg|tm|service">word</tm>\` |
| **Map output** | \`.ditamap\` (kit documents) or \`.bookmap\` (book documents) |
| **Page range** | Extract specific pages: \`1-5, 8, 12-15\` |
| **Blank page detection** | "Intentionally left blank" pages skipped automatically |
| **Selective export** | Check topics → download one \`.dita\` or scoped ZIP |
| **Image support (DOCX)** | Provide extracted \`media/\` folder path |
| **Particle UI** | Animated magnetic field particle background |

---

## Topic Structure Rules

| Topic | Root element | H2/H3 handling |
|---|---|---|
| Introduction | \`<concept>\` | \`<section><title>\` |
| Appendix | \`<reference>\` | \`<section><title>\` |
| Everything else | \`<dita>\` (ditabase) | Sibling typed topics inside \`<dita>\` |

Ditabase child type detection (priority order):

1. Steps detected → \`<task>\`
2. Majority tables/dl → \`<reference>\`
3. Prose paragraphs → \`<concept>\`
4. Default → \`<topic>\`

---

## Project Structure

\`\`\`
dita-converter/
├── agents/
│   ├── extractor.py       # PDF/DOCX → Content Tree
│   ├── mapper.py          # Content Tree + YAML → Annotated Tree
│   ├── generator.py       # Annotated Tree → DITA 2.0 XML + maps
│   └── validator.py       # XML validation + report
├── config/
│   └── mapping_rules.yaml # Style mapping rules (editable)
├── ui/
│   └── app.py             # Streamlit web UI
├── build/
│   ├── launcher.py        # Windows exe entry point
│   ├── build.py           # PyInstaller build script
│   ├── dita_converter.spec
│   └── IT_Certificate_Guide.md
├── runtime.txt
├── requirements.txt
├── COMMANDS.md            # Developer command reference
├── CLAUDE.md              # AI assistant context
└── README.md
\`\`\`

---

## DOCX Image Extraction

1. Copy your \`.docx\` → rename to \`.zip\` → extract
2. Navigate to extracted folder → \`word/\` → \`media/\`
3. Paste the full path to \`media/\` in the UI image folder field

---

## Mapping Rules

Edit \`config/mapping_rules.yaml\` to adapt to different document styles — no code changes required. Based on Passport V24.04 analysis (42 documents, 64 element types).

Key configurable sections: \`topic_type_signals\`, \`note_map\`, \`table_map\`, \`task_section_map\`, \`drop_patterns\`.

---

## Windows Executable

Build a standalone \`.exe\` (no Python install required):

\`\`\`cmd
py -3.11 build\build.py
\`\`\`

Output: \`dist\DITAConverter.exe\` — double-click to launch. See \`build\IT_Certificate_Guide.md\` for enterprise signing via GPO.

---

## Dependencies

| Library | Version | Purpose |
|---|---|---|
| pdfplumber | 0.10.x | PDF text, table, and geometry extraction |
| python-docx | 1.1.x | DOCX parsing |
| PyYAML | 6.x | Mapping rules config |
| lxml | 5.x | XML generation and validation |
| streamlit | 1.35.x | Web UI |
| protobuf | 3.20.3 | Pinned for Python 3.11 exe compatibility |

**Total runtime cost: \$0.00** — no API keys, no cloud services.

---

## Roadmap

| Version | Focus |
|---|---|
| v1.1 | ✅ DITA 2.0 · Ditabase · ROW_SHOW tables · Hazard statements · TM markup · Bold · Bookmap · Particle UI |
| v2.0 | Batch conversion · Auto DOCX image extraction · Full DTD validation |
| v2.1 | DITA map editor · Drag-to-reorder topics |
| v3.0 | LLM-assisted mapping for ambiguous content |

---

## License

MIT — see \`LICENSE\` for details.
