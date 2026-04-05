"""
agents/validator.py
DITA Converter Tool — Validator Agent

Validates each DITA 2.0 XML string produced by the Generator:
  - Well-formedness check via lxml
  - Structural checks (title, sections, steps)
  - Content inventory (word count, element counts)
  - Human-readable report

Session: S-05 | Updated S-08 (DITA 2.0 namespace) | Reviewer-signed-off
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from lxml import etree  # type: ignore


DITA2_NS = "https://docs.oasis-open.org/dita/ns/2.0"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    is_valid: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    report: str = ""
    pretty_xml: str = ""


# ---------------------------------------------------------------------------
# Validator class
# ---------------------------------------------------------------------------

class Validator:

    def validate(
        self,
        xml_string: str,
        annotated_blocks: list[dict] | None = None,
        filename: str = "output.dita",
    ) -> ValidationResult:
        result = ValidationResult()

        # ---- 1. Well-formedness ----
        clean_xml = self._strip_declaration(xml_string)
        try:
            root = etree.fromstring(clean_xml.encode("utf-8"))
            result.is_valid = True
        except etree.XMLSyntaxError as exc:
            result.is_valid = False
            result.errors.append(f"XML syntax error: {exc}")
            result.report = self._build_report(result, filename)
            return result

        # ---- 2. Root element check ----
        local = etree.QName(root.tag).localname if root.tag.startswith("{") else root.tag
        if local not in ("concept", "task", "reference", "topic", "bookmap", "map"):
            result.errors.append(
                f"Root element <{local}> is not a recognised DITA 2.0 topic or map type."
            )

        ns = DITA2_NS

        # ---- 3. Title ----
        titles = root.findall(f"{{{ns}}}title")
        if not titles:
            result.errors.append("Missing <title> element at topic root.")
        elif not (titles[0].text or "").strip():
            result.warnings.append("Topic <title> is empty.")

        # ---- 4. Topic id ----
        if not root.get("id"):
            result.warnings.append("Topic root element has no @id attribute.")

        # ---- 5. Structural checks ----
        # Empty sections
        for sec in root.iter(f"{{{ns}}}section"):
            sec_title = sec.findtext(f"{{{ns}}}title") or ""
            children = [c for c in sec if etree.QName(c.tag).localname != "title"]
            if not children:
                result.warnings.append(
                    f'Section "{sec_title.strip()}" has no body content.'
                )

        # Steps missing <cmd>
        for step in root.iter(f"{{{ns}}}step"):
            if step.find(f"{{{ns}}}cmd") is None:
                result.warnings.append("A <step> element is missing <cmd>.")

        # Notes missing @type — skip hazardstatement (has different structure)
        for note in root.iter(f"{{{ns}}}note"):
            if not note.get("type"):
                result.warnings.append("A <note> element has no @type attribute.")

        # Tables missing <thead>
        for tgroup in root.iter(f"{{{ns}}}tgroup"):
            if tgroup.find(f"{{{ns}}}thead") is None:
                result.warnings.append("A <tgroup> has no <thead> row.")

        # ---- 6. Content stats ----
        stats: dict = {}
        stats["topic_type"] = local
        stats["topic_id"] = root.get("id", "")
        stats["title"] = (titles[0].text or "").strip() if titles else ""
        stats["sections"] = len(root.findall(f".//{{{ns}}}section"))
        stats["notes"] = len(root.findall(f".//{{{ns}}}note"))
        stats["steps"] = len(root.findall(f".//{{{ns}}}step"))
        stats["tables"] = len(root.findall(f".//{{{ns}}}table"))
        stats["figures"] = len(root.findall(f".//{{{ns}}}fig"))
        all_text = " ".join(t for t in root.itertext() if t)
        stats["word_count"] = len(all_text.split())

        # Pipeline stats from annotated blocks
        if annotated_blocks:
            stats["blocks_total"] = len(annotated_blocks)
            stats["blocks_dropped"] = annotated_blocks[0].get("metadata", {}).get(
                "dropped_count", 0)
            stats["blocks_fallback"] = annotated_blocks[0].get("metadata", {}).get(
                "fallback_count", 0)

        result.stats = stats

        # ---- 7. Pretty-print ----
        try:
            etree.indent(root, space="  ")
            pretty_bytes = etree.tostring(root, encoding="unicode", pretty_print=True)
            result.pretty_xml = pretty_bytes
        except Exception:
            result.pretty_xml = xml_string

        # ---- 8. Report ----
        result.report = self._build_report(result, filename)

        return result

    # -----------------------------------------------------------------------
    # Strip XML declaration + DOCTYPE before lxml parse
    # -----------------------------------------------------------------------

    @staticmethod
    def _strip_declaration(xml: str) -> str:
        lines = xml.splitlines()
        clean = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("<?xml") or stripped.startswith("<!DOCTYPE"):
                continue
            clean.append(line)
        return "\n".join(clean)

    # -----------------------------------------------------------------------
    # Report builder
    # -----------------------------------------------------------------------

    def _build_report(self, result: ValidationResult, filename: str) -> str:
        lines: list[str] = []
        W = 60
        sep = "─" * W

        lines.append(f"╔{'═' * W}╗")
        lines.append(f"║  DITA 2.0 Validation Report{' ' * (W - 27)}║")
        lines.append(f"║  File: {filename:<{W - 8}}║")
        lines.append(f"╚{'═' * W}╝")
        lines.append("")

        status = "✅  VALID" if result.is_valid else "❌  INVALID"
        lines.append(f"  Status : {status}")
        lines.append(f"  Errors : {len(result.errors)}")
        lines.append(f"  Warnings: {len(result.warnings)}")
        lines.append("")
        lines.append(sep)

        if result.stats:
            s = result.stats
            lines.append("  CONTENT INVENTORY")
            lines.append(sep)
            lines.append(f"  Topic type  : {s.get('topic_type', '—')}")
            lines.append(f"  Topic id    : {s.get('topic_id', '—')}")
            lines.append(f"  Title       : {s.get('title', '—')}")
            lines.append(f"  Sections    : {s.get('sections', 0)}")
            lines.append(f"  Notes       : {s.get('notes', 0)}")
            lines.append(f"  Steps       : {s.get('steps', 0)}")
            lines.append(f"  Tables      : {s.get('tables', 0)}")
            lines.append(f"  Figures     : {s.get('figures', 0)}")
            lines.append(f"  Word count  : {s.get('word_count', 0)}")
            if "blocks_total" in s:
                lines.append(f"  Blocks in   : {s['blocks_total']}")
                lines.append(f"  Dropped     : {s['blocks_dropped']}")
                lines.append(f"  Fallbacks   : {s['blocks_fallback']}")
            lines.append("")
            lines.append(sep)

        if result.errors:
            lines.append("  ERRORS")
            lines.append(sep)
            for e in result.errors:
                lines.append(f"  ✗ {e}")
            lines.append("")
            lines.append(sep)

        if result.warnings:
            lines.append("  WARNINGS")
            lines.append(sep)
            for w in result.warnings:
                lines.append(f"  ⚠ {w}")
            lines.append("")
            lines.append(sep)

        if not result.errors and not result.warnings:
            lines.append("  No issues found. Output is clean DITA 2.0.")
            lines.append(sep)

        return "\n".join(lines)
