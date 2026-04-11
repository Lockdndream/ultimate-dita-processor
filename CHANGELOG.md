# Changelog

All notable changes to the DITA Converter Tool are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Commits follow [Conventional Commits](https://www.conventionalcommits.org/).

---

## [1.1.0] — 2026-04-11 — PDF Quality Enhancement

### Added
- Hybrid PDF-quality logic in `pdf_quality.py`
- Editable brand/logo rules in `pdf_quality_rules.yaml`
- Placeholder location for approved logo assets in `config/logo_refs/README.md`

### Features
- Configurable brand rules for Invenco, Gilbarco, Gasboy, and Angi
- Required "Powered by Vontier" detection
- Logo color expectation checks
- Optional OCR fallback when local Tesseract is available
- Logo reference matching hooks that activate once logo images are added
- Raster fallback for change-bar detection and OCR fallback for watermark/tagline detection

---

## [1.0.0] — 2026-03-14 — Proof of Concept Release

### S-07 — Integration & Handoff
- Full end-to-end integration test: both sample PDFs, all 4 agents
- Verified: 0 errors, ≤1 warning per document, ~2.3s per file
- Final CHANGELOG, README, and project structure verified clean

### S-06 — Streamlit UI
- File uploader (PDF + DOCX), live pipeline stage indicators
- Three output tabs: DITA XML preview, Validation Report, Content Stats
- Download button for .dita file (primary style, correct MIME)
- Scanned PDF error shown cleanly with user tip
- Sidebar: mapping profile, pipeline legend, supported formats

### S-05 — Validator Module
- XML well-formedness check via lxml (strips declaration/DOCTYPE)
- Structural checks: root tag, topic id, title, empty sections,
  steps missing cmd, typeless notes, tables missing thead/tbody
- Content inventory stats: type, id, title, all element counts, word count
- Human-readable Unicode-bordered plain-text report
- Pretty-printer via etree.indent

### S-04 — Generator Module
- DITA 1.3 XML serialisation with correct DOCTYPE per topic type
- CALS table: tgroup + colspec per column + thead + tbody
- Notes: <note type="warning|caution|important|danger|note">
- Steps: <steps><step><cmd> with <info><ul> for sub-bullets
- Figures: <fig><title> with image placeholder
- Lists: <ul><li><p> and <ol><li><p> buffered and flushed
- Menucascade: UI path strings split into <uicontrol> segments
- Section/sectiondiv boundary management via cursor pattern
- topic_id derived from title (lowercase, non-alnum → underscore)

### S-03 — Mapper Module
- Three-pass pipeline: merge split headings → reclassify callout
  tables → detect numbered steps in task context
- Callout box reclassification: 1-col WARNING/CAUTION/IMPORTANT
  tables converted to note blocks (pdfplumber PDF artefact fix)
- First H1 → title; subsequent H1 → section_title
- UI path detection (MWS > Set Up > ...) → menucascade
- Zero unmapped and zero fallback blocks on both sample files

### S-02 — Extractor Module
- PDF: font-size-calibrated heading detection (H1=18pt, H2=14pt,
  H3=12pt, notes=15pt, steps=10pt) from MDE-5570A, MDE-3839Q
- PDF: line-level extraction with paragraph merger for wrapped text
- PDF: table extraction with is_header flag on first row
- PDF: scanned PDF guard with clear user-facing error message
- DOCX: style-name-driven extraction matching PDF block schema
- Drops running headers/footers, copyright, TOC lines

### S-01 — Project Scaffold
- Directory layout: agents/, config/, ui/, tests/, docs/, sample_inputs/
- requirements.txt with all pinned dependencies
- config/mapping_rules.yaml — locked decisions D-001 through D-009
- Agent stubs: extractor, mapper, generator, validator
- Streamlit UI scaffold, test structure, README, .gitignore

---

## Decision Log

| ID    | Decision                                  | Session |
|-------|-------------------------------------------|---------|
| D-001 | Target DITA 1.3 (2.0 migration documented)| S-01    |
| D-002 | Streamlit for UI                          | S-01    |
| D-003 | Rule-based pipeline (no LLM at runtime)   | S-01    |
| D-004 | YAML for mapping config                   | S-01    |
| D-005 | pdfplumber over PyPDF2                    | S-01    |
| D-006 | lxml for XML generation                   | S-01    |
| D-007 | One topic per file (v1)                   | S-01    |
| D-008 | Sub-bullets → <info><ul> (not <substeps>) | S-03    |
| D-009 | Tables → CALS <thead> + multi-col         | S-03    |
