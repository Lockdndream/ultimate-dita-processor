# CLAUDE.md — AI Assistant Context for DITA Converter

This file gives Claude Code the context needed to work effectively on this project without re-explaining history each session.

---

## What This Project Is

A Python/Streamlit tool that converts text-based PDF and DOCX files to DITA 2.0 XML. It runs as a web app (Streamlit Cloud) and as a Windows executable (PyInstaller).

- **Live app**: https://dita-converter-testnaipat.streamlit.app/
- **Repo**: https://github.com/Lockdndream/dita-converter
- **Target users**: Technical documentation professionals at Gilbarco
- **Source docs**: Gilbarco technical manuals (FrameMaker output, PDF)

---

## Architecture

Four-agent pipeline — each agent is a single Python file:

```
agents/extractor.py  →  agents/mapper.py  →  agents/generator.py  →  agents/validator.py
```

| Agent | Input | Output |
|---|---|---|
| Extractor | PDF/DOCX bytes | List of block dicts (type, text, metadata) |
| Mapper | Block list + mapping_rules.yaml | Annotated block list (dita_element per block) |
| Generator | Annotated blocks | List of (filename, xml_string) tuples + ditamap/bookmap |
| Validator | XML string | ValidationResult (is_valid, errors, warnings, stats) |

`ui/app.py` is the Streamlit front-end. It calls the agents directly (in-process, no API).

---

## Key Technical Decisions

| ID | Decision |
|---|---|
| D-001 | DITA 2.0 output (namespace: `https://docs.oasis-open.org/dita/ns/2.0`) |
| D-003 | Rule-based pipeline — no LLM at runtime |
| D-004 | YAML mapping config (`config/mapping_rules.yaml`) |
| D-007 | Per-topic splitting at H1 boundaries |
| D-010 | Per-chunk topic type detection |
| D-011 | `@id` omitted from topic roots (server assigns on import) |
| D-012 | Non-intro topics → ditabase (`<dita>`) root with typed child topics |
| D-013 | Introduction topic → `<concept>` with `<section>` for H2/H3 |
| D-014 | Appendix topics (title first word = "Appendix") → always `<reference>` |

---

## DITA Output Rules — Critical

These are locked decisions. Do not change without explicit instruction.

**Topic structure:**
- Introduction topic → `<concept>` root, H2/H3 → `<section><title>`
- Appendix topic → `<reference>` root
- Everything else → `<dita>` (ditabase) root, typed child topics (`<concept>/<task>/<reference>/<topic>`)
- H2/H3 in ditabase topics → sibling typed topics inside `<dita>`, NOT nested inside the first child

**Forbidden output:**
- `<div>` — never generate, never add
- `<div>` with `<title>` — specifically prohibited
- Stray `<p>` from fallback — unknown blocks are silently dropped, not emitted

**Hazard statements:**
- `IMPORTANT INFORMATION` → `<hazardstatement type="notice">`
- `WARNING` → `<hazardstatement type="warning">`
- `CAUTION` → `<hazardstatement type="caution">`
- `DANGER` → `<hazardstatement type="danger">`
- All hazard statements: `<messagepanel><typeofhazard>text</typeofhazard></messagepanel>` only — no `<consequence>` or `<howtoavoid>`
- Generic `Note:` → `<note type="note">`

**Tables:**
- ROW_SHOW format: 2pt header rules (thick rects), 0.5pt row separators, no vertical lines
- `<thead>` applies only to the first thick-bordered row
- Subsequent thick-bordered rows → `<tbody>` with bold text (`<b>`)
- Straddle cells → `namest`/`nameend` on `<entry>`
- `frame="topbot"` default (not `frame="all"`)
- No `@navtitle` on `<topicref>` or `<chapter>`
- No `@type` on `<chapter>`

**Inline markup:**
- Bold → `<b>` in paragraphs and tbody cells; stripped in thead (toolchain renders it bold)
- Superscript ® ™ ℠ → `<tm tmtype="reg|tm|service">preceding_word</tm>`
- `trademark` attribute on `<tm>` left blank

**Maps:**
- `<topicref>` attributes: `href` and `type` only — no `@navtitle`
- `<chapter>` attributes: `href` only — no `@type`, no `@navtitle`

---

## Table Detection (ROW_SHOW)

FrameMaker tables are detected via PDF geometry, not pdfplumber's standard table finder. Key constants in `extractor.py`:

- `_ROW_SHOW_THICK = 1.5` — minimum rect height (pts) for a header boundary rule
- `_ROW_SHOW_COL_GAP = 40` — minimum X gap for column break detection
- `_assign_col()` uses 2pt left tolerance to handle float precision between word x0 and rect x0

Column inference uses the header band with the most distinct X positions (not all words).

Straddle detection: if leftmost word in a header band starts at > 60% of the first column's width, it's a spanning cell.

---

## Inline Sentinel System

The extractor encodes inline markup as text sentinels that the generator decodes:

| Sentinel | Meaning |
|---|---|
| `__BOLD__text` | Entire string is bold |
| `word__TM__reg__` | `word` is `<tm tmtype="reg">` |
| `word__TM__tm__` | `word` is `<tm tmtype="tm">` |
| `word__TM__service__` | `word` is `<tm tmtype="service">` |
| `__STRADDLE__{n}` | Cell spans n columns (namest/nameend) |

Generator function `_apply_inline()` parses these and emits correct XML.

---

## Branch Workflow

- `main` — stable, tested, deployed to Streamlit Cloud
- `develop` — all active development

```cmd
git checkout develop   # always start here
# ... work ...
git checkout main
git merge develop
git push
git checkout develop
```

---

## File Locations

| File | Purpose |
|---|---|
| `agents/extractor.py` | PDF/DOCX → Content Tree. ROW_SHOW detection, TM sentinels, bold, blank page, page range |
| `agents/mapper.py` | YAML-driven annotation. Hazard classification, UI path detection |
| `agents/generator.py` | DITA 2.0 XML. Ditabase output, topic type detection, inline markup, CALS tables |
| `agents/validator.py` | Well-formedness check, stats collection |
| `config/mapping_rules.yaml` | All mapping rules. Based on V24.04 analysis (42 docs, 64 elements) |
| `ui/app.py` | Streamlit UI. Particle animation via `st.components.v1.html()` + `window.parent` |
| `build/launcher.py` | PyInstaller exe entry point. Patches importlib.metadata and signal for frozen bundle |
| `build/dita_converter.spec` | PyInstaller spec with dist-info metadata bundles |

---

## Particle Animation

The UI background uses a magnetic field lines animation:

- 2200 iron filings (short line segments) oriented by a local field vector
- Field = rotating uniform background + standing sine waves + mouse pole (`1/d` falloff)
- Injected via `st.components.v1.html(..., height=0)` using `window.parent.document` to escape Streamlit's iframe sandbox
- Canvas appended to `body`, CSS sets `z-index: 0` with `pointer-events: none`
- Left column has frosted glass: `rgba(255,255,255,0.55)` + `backdrop-filter: blur`

---

## Mapping Rules Summary

`config/mapping_rules.yaml` drives the Mapper. Key sections:

- `topic_type_signals` — text patterns that force task/reference type
- `task_section_map` — heading patterns for prereq/context/postreq
- `note_map` — callout keyword → hazardstatement/note type mapping
- `table_map` — frame defaults, colwidth preservation, abbreviation detection
- `drop_patterns` — pagination/boilerplate lines to discard
- `element_frequency` — reference table from V24.04 analysis for QA prioritisation

---

## What's Coming (Backlog)

| ID | Feature | Priority |
|---|---|---|
| B-003 | Batch conversion (multi-file, folder input, master ditamap) | Medium |
| B-005 | Mapping profile selector in UI | Low |
| B-006 | FrameMaker 17 import workflow guide | Low |
