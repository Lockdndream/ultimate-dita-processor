"""
agents/mapper.py
DITA Converter Tool — Mapper Agent

Reads the Content Tree produced by the Extractor and annotates every block
with its DITA 2.0 element name, using rules loaded from mapping_rules.yaml.

Three-pass pipeline:
  1. Merge split headings (PDF artefact: H1 split across two lines)
  2. Reclassify callout-box tables as note blocks
  3. Annotate every block with its DITA element

Session: S-03 | Updated S-08 (DITA 2.0) | Reviewer-signed-off
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "mapping_rules.yaml"

_REQUIRED_KEYS = {
    "heading_map", "note_map", "figure_map",
    "list_map", "table_map", "fallback_element",
}


class Mapper:
    def __init__(self, config_path: str | Path = _DEFAULT_CONFIG):
        self.config_path = Path(config_path)
        with open(self.config_path, encoding="utf-8") as fh:
            self.rules: dict[str, Any] = yaml.safe_load(fh)

        missing = _REQUIRED_KEYS - set(self.rules)
        if missing:
            raise ValueError(f"mapping_rules.yaml missing keys: {missing}")

        # Build note-pattern lookup
        self._note_patterns = []
        for entry in self.rules.get("note_map", []):
            self._note_patterns.append((
                re.compile(entry["pattern"], re.IGNORECASE),
                entry.get("type", "note"),
            ))

        self._task_signals = [
            s.lower() for s in self.rules.get("topic_type_signals", {}).get("task", [])
        ]
        self._ref_signals = [
            s.lower() for s in self.rules.get("topic_type_signals", {}).get("reference", [])
        ]
        self._ui_pattern = re.compile(
            self.rules.get("ui_path_pattern", r".+\s*>\s*.+")
        )

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def map(self, blocks: list[dict]) -> list[dict]:
        """Annotate all blocks with dita_element. Returns the same list (mutated)."""
        blocks = self._merge_split_headings(blocks)
        blocks = self._reclassify_callout_tables(blocks)
        topic_type = self._detect_topic_type(blocks)

        first_h1_seen = False
        fallback_count = 0

        for i, block in enumerate(blocks):
            btype = block["type"]
            text = block.get("text", "")
            meta = block.get("metadata", {})

            # ---- Headings ----
            if btype == "heading":
                level = block.get("level", 1)
                if level == 1:
                    if not first_h1_seen:
                        block["dita_element"] = "title"
                        first_h1_seen = True
                    else:
                        block["dita_element"] = "section_title"
                elif level in (2, 3):
                    block["dita_element"] = "sectiondiv_title"
                else:
                    block["dita_element"] = "p"
                continue

            # ---- Paragraphs ----
            if btype == "paragraph":
                # UI menucascade
                if self._ui_pattern.match(text) and ">" in text:
                    block["dita_element"] = "menucascade"
                    continue

                block["dita_element"] = "p"
                continue

            # ---- List items ----
            if btype == "list_item":
                list_kind = meta.get("list_kind", "bullet")
                if list_kind == "bullet":
                    block["dita_element"] = "ul_li"
                elif list_kind == "numbered":
                    # Do not resolve step vs ol_li here — the mapper does not have
                    # chunk context. The generator detects topic type per-chunk and
                    # resolves this correctly at render time.
                    block["dita_element"] = "numbered_li"
                else:
                    block["dita_element"] = "ul_li"
                continue

            # ---- Note headers (already classified) ----
            if btype == "note_header":
                note_type = meta.get("note_type", "note")
                # ANSI Z535 / ISO 3864 hazard types use <hazardstatement>
                _HAZARD_TYPES = {"notice", "caution", "warning", "danger"}
                if note_type in _HAZARD_TYPES:
                    block["dita_element"] = f"hazard:{note_type}"
                else:
                    # Distinguish real inline notes from section subheadings.
                    # In MDE/FrameMaker docs, 15pt bold text is used for section
                    # sub-headings ("Purpose", "Kit Numbers and Descriptions",
                    # "Table of Contents", etc.) as well as for genuine "Note:"
                    # callout headers.  If the text doesn't start with a standard
                    # note keyword it is a sub-heading → sectiondiv_title.
                    _NOTE_KEYWORDS = {"note", "notes", "notice", "tip", "important"}
                    first_word = (
                        text.strip().split()[0].lower().rstrip(":")
                        if text.strip() else ""
                    )
                    if first_word in _NOTE_KEYWORDS or not text.strip():
                        block["dita_element"] = "note:note"
                    else:
                        block["dita_element"] = "sectiondiv_title"
                continue

            # ---- Inline notes ----
            if btype == "note_inline":
                # Strip prefix to get content
                clean = re.sub(r"^Notes?:\s*", "", text, flags=re.IGNORECASE)
                block["text"] = clean
                block["dita_element"] = "note:note"
                continue

            # ---- Tables ----
            if btype == "table":
                # Always use <table> — <dl> is not used for any table output.
                block["dita_element"] = "table"
                continue

            # ---- Figures ----
            if btype == "figure":
                block["dita_element"] = "fig"
                # Extract caption from "Figure N: Caption" pattern
                match = re.match(r"^Figure\s+\d[\d\-\.]*\s*:\s*(.+)", text, re.IGNORECASE)
                if match:
                    block["metadata"]["caption"] = match.group(1).strip()
                else:
                    block["metadata"]["caption"] = text
                continue

            # ---- Code blocks ----
            if btype == "code_block":
                block["dita_element"] = "codeblock"
                continue

            # ---- Dropped ----
            if btype == "dropped":
                block["dita_element"] = "dropped"
                continue

            # ---- Fallback ----
            block["dita_element"] = self.rules["fallback_element"]
            fallback_count += 1

        # Store stats on first block
        if blocks:
            blocks[0].setdefault("metadata", {})
            blocks[0]["metadata"]["topic_type"] = topic_type
            blocks[0]["metadata"]["fallback_count"] = fallback_count

        return blocks

    # -----------------------------------------------------------------------
    # Pass 1: Merge split H1s
    # -----------------------------------------------------------------------

    def _merge_split_headings(self, blocks: list[dict]) -> list[dict]:
        """Merge consecutive H1 heading blocks (PDF line-break artefact)."""
        if not blocks:
            return blocks
        merged: list[dict] = []
        i = 0
        while i < len(blocks):
            block = blocks[i]
            if (block["type"] == "heading" and block.get("level") == 1
                    and i + 1 < len(blocks)
                    and blocks[i + 1]["type"] == "heading"
                    and blocks[i + 1].get("level") == 1):
                combined = block["text"] + " " + blocks[i + 1]["text"]
                block["text"] = combined.strip()
                merged.append(block)
                i += 2
            else:
                merged.append(block)
                i += 1
        return merged

    # -----------------------------------------------------------------------
    # Pass 2: Reclassify callout-box tables as notes
    # -----------------------------------------------------------------------

    def _reclassify_callout_tables(self, blocks: list[dict]) -> list[dict]:
        """
        pdfplumber treats bordered callout boxes as single-column tables.
        Detect these and reclassify as note_header blocks.
        """
        _CALLOUT_KW = {
            "WARNING": "warning",
            "CAUTION": "caution",
            "DANGER": "danger",
            "IMPORTANT INFORMATION": "notice",
            "IMPORTANT": "notice",
        }
        result: list[dict] = []
        for block in blocks:
            if block["type"] == "table":
                rows = block.get("rows", [])
                if rows and len(rows[0]) == 1:
                    header_text = str(rows[0][0]).strip().upper()
                    note_type = _CALLOUT_KW.get(header_text)
                    if note_type:
                        # Combine remaining rows as note body text
                        body_parts = []
                        for row in rows[1:]:
                            for cell in row:
                                if str(cell).strip():
                                    body_parts.append(str(cell).strip())
                        body = " ".join(body_parts)
                        block["type"] = "note_header"
                        block["text"] = body
                        block["rows"] = []
                        block.setdefault("metadata", {})["note_type"] = note_type
                        result.append(block)
                        continue
            result.append(block)
        return result

    # -----------------------------------------------------------------------
    # Topic type detection
    # -----------------------------------------------------------------------

    def _detect_topic_type(self, blocks: list[dict]) -> str:
        """
        Detect topic type from document structure.

        Rules:
          task      → 3 or more numbered list_item blocks
                      (structural signal — reliable regardless of phrasing)
          reference → majority of body blocks are tables
          concept   → default
        """
        numbered_count = sum(
            1 for b in blocks
            if b.get("type") == "list_item"
            and b.get("metadata", {}).get("list_kind") == "numbered"
        )
        if numbered_count >= 3:
            return "task"

        # Reference: majority of non-heading body blocks are tables
        body_blocks = [
            b for b in blocks
            if b.get("type") not in ("heading",)
            and b.get("type") is not None
        ]
        table_count = sum(1 for b in body_blocks if b.get("type") == "table")
        if body_blocks and table_count / len(body_blocks) >= 0.5:
            return "reference"

        for sig in self._ref_signals:
            if any(sig in b.get("text", "").lower() for b in blocks):
                return "reference"

        return self.rules.get("topic_type", "concept")
