"""
agents/generator.py
DITA Converter Tool — Generator Agent

Serialises the annotated Content Tree into valid DITA 2.0 XML using lxml.

NEW in S-08:
  - DITA 2.0 namespace and DOCTYPE
  - Multi-topic splitting: each H1 boundary produces a separate topic file.
  - Returns list of (filename, xml_string) tuples.

NEW in S-09:
  - @id removed from topic root elements (server assigns on import)
  - Per-topic type detection: task / reference / concept / topic per chunk
  - generate_ditamap() produces a .ditamap listing all topic files

Session: S-04 | Updated S-08/S-09 | Reviewer-signed-off
"""

from __future__ import annotations

import re
from lxml import etree  # type: ignore
from typing import Any


# ---------------------------------------------------------------------------
# DITA 2.0 constants
# ---------------------------------------------------------------------------

DITA2_NS = "https://docs.oasis-open.org/dita/ns/2.0"
DITA2_NS_MAP = {None: DITA2_NS}

_VALID_TOPIC_TYPES = {"concept", "task", "reference", "topic", "ditabase"}

_DOCTYPE_MAP = {
    "concept":   '<!DOCTYPE concept PUBLIC "-//OASIS//DTD DITA 2.0 Concept//EN" "concept.dtd">',
    "task":      '<!DOCTYPE task PUBLIC "-//OASIS//DTD DITA 2.0 Task//EN" "task.dtd">',
    "reference": '<!DOCTYPE reference PUBLIC "-//OASIS//DTD DITA 2.0 Reference//EN" "reference.dtd">',
    "topic":     '<!DOCTYPE topic PUBLIC "-//OASIS//DTD DITA 2.0 Topic//EN" "topic.dtd">',
    "ditabase":  '<!DOCTYPE dita PUBLIC "-//OASIS//DTD DITA 2.0 Composite//EN" "ditabase.dtd">',
}

_BODY_ELEM = {
    "concept":   "conbody",
    "task":      "taskbody",
    "reference": "refbody",
    "topic":     "body",
    "ditabase":  None,   # ditabase has no body — children are typed topics
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename(title: str, index: int) -> str:
    """Derive a filesystem-safe filename from a topic title."""
    if not title:
        return f"topic_{index:02d}.dita"
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return (slug or f"topic_{index:02d}") + ".dita"


def _detect_topic_type(
    chunk: list[dict],
    log: list[str] | None = None,
) -> str:
    """
    Detect the DITA topic type for a chunk of blocks.

    Rules (in priority order):
      1. Title first word = "Appendix"  → reference (always)
      2. Title = "Introduction"         → concept (section-based)
      3. Any step element present       → task (child of ditabase)
      4. Majority tables/dl             → reference (child of ditabase)
      5. Has prose paragraphs           → concept (child of ditabase)
      6. Default                        → topic (child of ditabase)

    Note: the caller (_render_topic) wraps non-intro, non-appendix topics
    in a <ditabase> root — this function returns the CHILD type.
    """
    elements = [b.get("dita_element") for b in chunk]

    # Rule 1: Appendix → reference
    for b in chunk:
        if b.get("dita_element") in ("title", "section_title"):
            title_words = b.get("text", "").strip().split()
            if title_words and title_words[0].lower() == "appendix":
                result = "reference"
                if log is not None:
                    numbered_count = sum(1 for e in elements if e == "numbered_li")
                    step_count     = sum(1 for e in elements if e == "step")
                    table_count    = sum(1 for e in elements if e == "table")
                    para_count     = sum(1 for e in elements if e == "p")
                    title_text     = next(
                        (b.get("text", "")[:60] for b in chunk
                         if b.get("dita_element") in ("title", "section_title")),
                        "(no title)"
                    )
                    log.append(
                        f"[TOPIC_TYPE] '{title_text}' → {result}"
                        f"  numbered_li={numbered_count} step={step_count}"
                        f"  table={table_count} p={para_count}"
                        f"  total_blocks={len(chunk)}"
                    )
                return result
            break

    body_elements = [e for e in elements if e not in (
        "title", "section_title", "sectiondiv_title", "dropped", None
    )]

    if not body_elements:
        result = "topic"
    else:
        # Rule 3: steps or ≥2 unresolved numbered items → task
        numbered_li_count = sum(1 for e in elements if e == "numbered_li")
        if "step" in elements or numbered_li_count >= 2:
            result = "task"
        else:
            # Rule 4: majority tables → reference
            table_count = sum(1 for e in body_elements if e == "table")
            para_count  = sum(1 for e in body_elements if e == "p")
            total = len(body_elements)

            if total > 0 and table_count / total >= 0.5:
                result = "reference"
            # Rule 5: prose → concept
            elif para_count > 0:
                result = "concept"
            else:
                result = "topic"

    if log is not None:
        numbered_count = sum(1 for e in elements if e == "numbered_li")
        step_count     = sum(1 for e in elements if e == "step")
        table_count    = sum(1 for e in elements if e == "table")
        para_count     = sum(1 for e in elements if e == "p")
        title_text     = next(
            (b.get("text", "")[:60] for b in chunk
             if b.get("dita_element") in ("title", "section_title")),
            "(no title)"
        )
        log.append(
            f"[TOPIC_TYPE] '{title_text}' → {result}"
            f"  numbered_li={numbered_count} step={step_count}"
            f"  table={table_count} p={para_count}"
            f"  total_blocks={len(chunk)}"
        )
    return result


def _apply_inline(element: etree._Element, text: str, ns: str,
                  bold: bool = False) -> None:
    """
    Set element content handling __TM__{type}__ and __BOLD__ sentinels.

    Sentinel format: "some text wordname__TM__{type}__ more text"
    The word immediately before __TM__{type}__ becomes the <tm> content:
      → "some text <tm tmtype='{type}'>wordname</tm> more text"

    __BOLD__ prefix wraps everything in <b>.
    """
    import re as _re
    if not text:
        return

    # Strip __BOLD__ prefix
    if text.startswith("__BOLD__"):
        bold = True
        text = text[8:]

    # Wrap in <b> if bold
    if bold:
        container = etree.SubElement(element, _tag(ns, "b"))
    else:
        container = element

    # Split on TM sentinels — the word before the sentinel is the tm content.
    # Pattern: (text_before_word)(tm_word)__TM__(type)__(text_after)
    # We use re.split on __TM__{type}__ to get alternating [text, type, text, type...]
    parts = _re.split(r"__TM__([a-z]+)__", text)
    # parts[0]   = text before first TM
    # parts[1]   = first tm_type
    # parts[2]   = text between first and second TM
    # etc.

    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Plain text — but if next part is a TM type, extract last word into <tm>
            is_last = (i == len(parts) - 1)
            if not is_last:
                # Next part is a TM type — extract last word of this segment
                stripped = part.rstrip()
                last_space = stripped.rfind(" ")
                if last_space >= 0:
                    pre_text  = stripped[:last_space + 1]  # text before tm word
                    tm_word   = stripped[last_space + 1:]  # the tm word itself
                else:
                    pre_text  = ""
                    tm_word   = stripped

                # Emit pre_text as plain
                if pre_text:
                    _append_to(container, pre_text)

                # The <tm> element will be emitted in the next (odd) iteration
                # Store tm_word so the next iteration can use it
                parts[i] = ""           # clear — already handled
                parts[i + 1] = (tm_word, parts[i + 1])  # bundle (word, type)
            else:
                # Last segment — just plain text
                if part:
                    _append_to(container, part)
        else:
            # TM marker — may be (tm_word, type) tuple from above, or plain type string
            if isinstance(part, tuple):
                tm_word, tm_type = part
            else:
                tm_word = ""
                tm_type = part

            tm_el = etree.SubElement(container, _tag(ns, "tm"))
            tm_el.set("tmtype", tm_type)
            tm_el.text = tm_word if tm_word else None
            tm_el.tail = ""


def _append_to(element: etree._Element, text: str) -> None:
    """Append text to element — as .text if no children, else last child's .tail."""
    if not text:
        return
    children = list(element)
    if not children:
        element.text = (element.text or "") + text
    else:
        last = children[-1]
        last.tail = (last.tail or "") + text


def _safe_text(element: etree._Element, text: str) -> None:
    """Simple text setter — no sentinel processing. For structural elements."""
    if text:
        element.text = text


def _apply_text(element: etree._Element, text: str, ns: str) -> None:
    """Apply text with __BOLD__ and __TM__ sentinel handling."""
    _apply_inline(element, text, ns)


def _tag(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


# ---------------------------------------------------------------------------
# Generator class
# ---------------------------------------------------------------------------

class Generator:
    def __init__(self, topic_type: str = "concept"):
        # topic_type kept for backward compat but ignored — detection is per-chunk
        self.topic_type = topic_type

    # -----------------------------------------------------------------------
    # Public: generate one or more topics
    # -----------------------------------------------------------------------

    def generate(
        self,
        blocks: list[dict],
        debug_log: list[str] | None = None,
    ) -> list[tuple[str, str]]:
        """
        Split blocks at every section_title boundary and generate one DITA
        2.0 XML string per topic. Topic type is detected per chunk.

        Returns:
            List of (filename, xml_string) tuples.
            Single-topic documents return a list of exactly one tuple.
        """
        topic_chunks = self._split_into_topics(blocks)

        results: list[tuple[str, str]] = []
        for i, chunk in enumerate(topic_chunks):
            # Per-chunk type detection (Fix 1 / S-09)
            topic_type = _detect_topic_type(chunk, log=debug_log)
            xml_str = self._render_topic(chunk, topic_type, debug_log=debug_log)
            # Derive filename from title block
            title_text = ""
            for b in chunk:
                if b.get("dita_element") in ("title", "section_title"):
                    title_text = b.get("text", "")
                    break
            filename = _safe_filename(title_text, i + 1)
            results.append((filename, xml_str))

        return results

    # -----------------------------------------------------------------------
    # Public: generate a DITA 2.0 .ditamap referencing all topic files
    # -----------------------------------------------------------------------

    def generate_ditamap(
        self,
        topic_files: list[tuple[str, str]],
        map_title: str = "Document Map",
    ) -> str:
        """
        Generate a DITA 2.0 .ditamap that references all supplied topic files.
        """
        ns = DITA2_NS
        root = etree.Element(f"{{{ns}}}map", nsmap=DITA2_NS_MAP)
        root.set("{http://www.w3.org/XML/1998/namespace}lang", "en-US")

        title_el = etree.SubElement(root, f"{{{ns}}}title")
        title_el.text = map_title

        for fname, xml_str in topic_files:
            topic_title = fname.replace(".dita", "").replace("_", " ").title()
            topic_type  = "topic"
            try:
                clean = "\n".join(
                    l for l in xml_str.splitlines()
                    if not l.strip().startswith("<?") and not l.strip().startswith("<!DOCTYPE")
                )
                parsed = etree.fromstring(clean.encode("utf-8"))
                local = etree.QName(parsed.tag).localname
                if local in _VALID_TOPIC_TYPES:
                    topic_type = local
                title_nodes = parsed.findall(f"{{{ns}}}title")
                if title_nodes and title_nodes[0].text:
                    topic_title = title_nodes[0].text.strip()
            except Exception:
                pass

            topicref = etree.SubElement(root, f"{{{ns}}}topicref")
            topicref.set("href", fname)
            topicref.set("type", topic_type)

        etree.indent(root, space="  ")
        xml_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
        xml_str = xml_bytes.decode("utf-8")
        doctype = '<!DOCTYPE map PUBLIC "-//OASIS//DTD DITA 2.0 Map//EN" "map.dtd">'
        decl_end = xml_str.index("?>") + 2
        return xml_str[:decl_end] + "\n" + doctype + xml_str[decl_end:]

    # -----------------------------------------------------------------------
    # Public: generate a DITA 2.0 .ditamap of type bookmap
    # -----------------------------------------------------------------------

    def generate_bookmap(
        self,
        topic_files: list[tuple[str, str]],
        map_title: str = "Book Title",
        subtitle: str = "",
        author: str = "",
    ) -> str:
        """
        Generate a DITA 2.0 bookmap.

        Structure:
          <bookmap>
            <booktitle>
              <mainbooktitle>...</mainbooktitle>
              <subtitle>...</subtitle>          (if provided)
            </booktitle>
            <bookmeta>
              <author>...</author>              (if provided)
            </bookmeta>
            <chapter href="topic1.dita">        (one per topic file)
              <topicref href="subtopic.dita"/>  (nested topics not yet supported)
            </chapter>
          </bookmap>

        Each topic file becomes a <chapter>. The first topic (usually the
        introduction / overview) becomes the first chapter. Topic type is
        preserved as the @type attribute on each chapter element.

        Args:
            topic_files: list of (filename, xml_string) from generate()
            map_title:   Main book title
            subtitle:    Optional subtitle
            author:      Optional author name
        """
        ns = DITA2_NS

        root = etree.Element(f"{{{ns}}}bookmap", nsmap=DITA2_NS_MAP)
        root.set("{http://www.w3.org/XML/1998/namespace}lang", "en-US")

        # ── <booktitle> ────────────────────────────────────────────────────
        booktitle_el = etree.SubElement(root, f"{{{ns}}}booktitle")
        main_title = etree.SubElement(booktitle_el, f"{{{ns}}}mainbooktitle")
        main_title.text = map_title
        if subtitle:
            sub_el = etree.SubElement(booktitle_el, f"{{{ns}}}subtitle")
            sub_el.text = subtitle

        # ── <bookmeta> ─────────────────────────────────────────────────────
        bookmeta_el = etree.SubElement(root, f"{{{ns}}}bookmeta")
        if author:
            author_el = etree.SubElement(bookmeta_el, f"{{{ns}}}author")
            author_el.text = author

        # ── <chapter> per topic ────────────────────────────────────────────
        for fname, xml_str in topic_files:
            # Resolve topic title and type from XML
            topic_title = fname.replace(".dita", "").replace("_", " ").title()
            topic_type  = "topic"
            try:
                clean = "\n".join(
                    l for l in xml_str.splitlines()
                    if not l.strip().startswith("<?") and not l.strip().startswith("<!DOCTYPE")
                )
                parsed = etree.fromstring(clean.encode("utf-8"))
                local = etree.QName(parsed.tag).localname
                if local in _VALID_TOPIC_TYPES:
                    topic_type = local
                title_nodes = parsed.findall(f"{{{ns}}}title")
                if title_nodes and title_nodes[0].text:
                    topic_title = title_nodes[0].text.strip()
            except Exception:
                pass

            chapter = etree.SubElement(root, f"{{{ns}}}chapter")
            chapter.set("href", fname)

        etree.indent(root, space="  ")
        xml_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
        xml_str = xml_bytes.decode("utf-8")
        doctype = '<!DOCTYPE bookmap PUBLIC "-//OASIS//DTD DITA 2.0 BookMap//EN" "bookmap.dtd">'
        decl_end = xml_str.index("?>") + 2
        return xml_str[:decl_end] + "\n" + doctype + xml_str[decl_end:]

    # -----------------------------------------------------------------------
    # Split blocks at section_title boundaries
    # -----------------------------------------------------------------------

    def _split_into_topics(self, blocks: list[dict]) -> list[list[dict]]:
        """
        Split the block list at every `section_title` element.
        The first chunk contains everything up to the first section_title.
        Each subsequent chunk starts with the section_title block (re-typed
        as `title` for its own topic).
        """
        if not blocks:
            return [[]]

        chunks: list[list[dict]] = []
        current: list[dict] = []

        for block in blocks:
            if block.get("dita_element") == "section_title" and current:
                # Close current chunk, start new one
                chunks.append(current)
                # Promote section_title → title for the new topic
                new_block = dict(block)
                new_block["dita_element"] = "title"
                current = [new_block]
            else:
                current.append(block)

        if current:
            chunks.append(current)

        # Filter out empty/title-only chunks
        return [c for c in chunks if any(
            b.get("dita_element") not in (None, "dropped") for b in c
        )]

    # -----------------------------------------------------------------------
    # Render a single topic to XML string
    # -----------------------------------------------------------------------

    def _render_topic(
        self,
        blocks: list[dict],
        topic_type: str,
        debug_log: list[str] | None = None,
    ) -> str:
        # Find title
        title_text = "Untitled Topic"
        for b in blocks:
            if b.get("dita_element") == "title":
                title_text = b.get("text", "Untitled Topic")
                break

        ns = DITA2_NS

        # Root element — @id intentionally omitted (assigned by authoring server on import)
        root = etree.Element(
            _tag(ns, topic_type),
            nsmap=DITA2_NS_MAP,
        )
        root.set("{http://www.w3.org/XML/1998/namespace}lang", "en-US")

        # <title>
        title_el = etree.SubElement(root, _tag(ns, "title"))
        _safe_text(title_el, title_text)

        # <shortdesc> from first paragraph after title
        first_para = None
        past_title = False
        for b in blocks:
            de = b.get("dita_element")
            if de == "title":
                past_title = True
                continue
            if past_title and de == "p" and not first_para:
                first_para = b.get("text", "")
                break

        if first_para:
            sd = etree.SubElement(root, _tag(ns, "shortdesc"))
            _safe_text(sd, first_para)

        # ── Determine root structure ─────────────────────────────────────────
        # Introduction → concept with <section> for H2/H3
        # Appendix     → reference (detected via topic_type already)
        # Everything else → ditabase root containing one typed child topic,
        #   with H2/H3 becoming additional sibling typed child topics.
        is_intro    = "introduction" in title_text.lower()
        is_appendix = topic_type == "reference" and                       title_text.strip().split()[0:1] == ["Appendix"] if title_text else False
        use_ditabase = not is_intro and not is_appendix

        if use_ditabase:
            # Rewrap: root = <dita>, first child = typed topic
            root.tag = _tag(ns, "dita")
            # Remove title/shortdesc from dita root — they go on child topic
            for child in list(root):
                root.remove(child)

            # First child topic
            child_topic = etree.SubElement(root, _tag(ns, topic_type))
            child_topic.set("{http://www.w3.org/XML/1998/namespace}lang", "en-US")
            child_title = etree.SubElement(child_topic, _tag(ns, "title"))
            _safe_text(child_title, title_text)
            if first_para:
                child_sd = etree.SubElement(child_topic, _tag(ns, "shortdesc"))
                _safe_text(child_sd, first_para)
            body_tag = _BODY_ELEM.get(topic_type, "body")
            body = etree.SubElement(child_topic, _tag(ns, body_tag))
        else:
            # Concept (intro) or reference (appendix) — single typed root
            body_tag = _BODY_ELEM.get(topic_type, "body")
            body = etree.SubElement(root, _tag(ns, body_tag))

        # Render blocks — H2/H3 handling depends on context
        self._render_blocks(blocks, body, ns, title_text, first_para,
                            is_introduction=is_intro, topic_type=topic_type,
                            dita_root=root if use_ditabase else None,
                            debug_log=debug_log)

        # Serialise
        xml_bytes = etree.tostring(
            root,
            xml_declaration=True,
            encoding="UTF-8",
            pretty_print=True,
        )
        xml_str = xml_bytes.decode("utf-8")

        # Prepend DOCTYPE
        actual_root = etree.QName(root.tag).localname
        if actual_root == "dita":
            doctype = _DOCTYPE_MAP["ditabase"]
        else:
            doctype = _DOCTYPE_MAP.get(topic_type, _DOCTYPE_MAP["topic"])
        decl_end = xml_str.index("?>") + 2
        xml_str = xml_str[:decl_end] + "\n" + doctype + xml_str[decl_end:]

        return xml_str

    # -----------------------------------------------------------------------
    # Block rendering
    # -----------------------------------------------------------------------

    def _render_blocks(
        self,
        blocks: list[dict],
        body: etree._Element,
        ns: str,
        title_text: str,
        first_para_text: str | None,
        is_introduction: bool = False,
        topic_type: str = "concept",
        dita_root: etree._Element | None = None,
        debug_log: list[str] | None = None,
    ) -> None:

        current_section: etree._Element | None = None
        current_sectiondiv: etree._Element | None = None
        step_buffer: list[dict] = []
        ul_buffer: list[dict] = []
        ol_buffer: list[dict] = []
        skip_first_para = first_para_text  # used as shortdesc already

        def flush_steps():
            nonlocal step_buffer
            if not step_buffer:
                return
            # <steps> is only valid as direct child of <taskbody>
            # Never emit inside <section> or <sectiondiv>
            steps_parent = body
            steps_el = etree.SubElement(steps_parent, _tag(ns, "steps"))
            for sb in step_buffer:
                step_el = etree.SubElement(steps_el, _tag(ns, "step"))
                cmd_el  = etree.SubElement(step_el, _tag(ns, "cmd"))
                _safe_text(cmd_el, sb.get("text", ""))
            step_buffer = []

        def flush_ul():
            nonlocal ul_buffer
            if not ul_buffer:
                return
            parent = current_sectiondiv or current_section or body
            ul_el = etree.SubElement(parent, _tag(ns, "ul"))
            for ub in ul_buffer:
                li_el = etree.SubElement(ul_el, _tag(ns, "li"))
                p_el = etree.SubElement(li_el, _tag(ns, "p"))
                _safe_text(p_el, ub.get("text", ""))
            ul_buffer = []

        def flush_ol():
            nonlocal ol_buffer
            if not ol_buffer:
                return
            parent = current_sectiondiv or current_section or body
            ol_el = etree.SubElement(parent, _tag(ns, "ol"))
            for ob in ol_buffer:
                li_el = etree.SubElement(ol_el, _tag(ns, "li"))
                p_el = etree.SubElement(li_el, _tag(ns, "p"))
                _safe_text(p_el, ob.get("text", ""))
            ol_buffer = []

        def flush_all():
            flush_steps()
            flush_ul()
            flush_ol()

        past_title = False
        first_para_done = False

        for block in blocks:
            de = block.get("dita_element")
            text = block.get("text", "")
            meta = block.get("metadata", {})

            if de == "title":
                past_title = True
                continue  # already rendered as root <title>

            if not past_title:
                continue

            # Skip first paragraph (already used as shortdesc)
            if de == "p" and not first_para_done and skip_first_para:
                # Use startswith to handle minor trailing whitespace differences
                if text.strip() == skip_first_para.strip():
                    first_para_done = True
                    continue

            # ---- section_title: open new <section> ----
            if de == "section_title":
                flush_all()
                current_sectiondiv = None
                if topic_type == "task":
                    # taskbody forbids <section> — render heading as bold <p>
                    # and keep body as the current parent
                    current_section = None
                    if text.strip():
                        p_el = etree.SubElement(body, _tag(ns, "p"))
                        b_el = etree.SubElement(p_el, _tag(ns, "b"))
                        _safe_text(b_el, text)
                else:
                    current_section = etree.SubElement(body, _tag(ns, "section"))
                    sec_title = etree.SubElement(current_section, _tag(ns, "title"))
                    _safe_text(sec_title, text)
                continue

            # ---- sectiondiv_title: H2/H3/H4 heading ----
            # Introduction → <section><title> inside the single concept body
            # Ditabase     → new sibling typed topic appended to <dita> root
            if de == "sectiondiv_title":
                flush_all()
                if is_introduction:
                    # Introduction: <section> with <title>
                    parent = current_section or body
                    current_sectiondiv = etree.SubElement(parent, _tag(ns, "section"))
                    sec_title = etree.SubElement(current_sectiondiv, _tag(ns, "title"))
                    _safe_text(sec_title, text)
                else:
                    # Ditabase: sibling typed topic on the <dita> root
                    # Collect lookahead blocks for this sub-topic to detect type correctly
                    _lookahead = [block]
                    _j = blocks.index(block) + 1 if block in blocks else len(blocks)
                    while _j < len(blocks):
                        _nb = blocks[_j]
                        if _nb.get("dita_element") == "sectiondiv_title":
                            break
                        _lookahead.append(_nb)
                        _j += 1
                    sub_type = _detect_topic_type(_lookahead)
                    if debug_log is not None:
                        debug_log.append(
                            f"[TOPIC_TYPE] sectiondiv '{text[:60]}' → {sub_type}"
                            f"  lookahead={len(_lookahead)} blocks"
                        )
                    sub_body_tag = _BODY_ELEM.get(sub_type, "body")
                    anchor = dita_root if dita_root is not None else body.getparent()
                    sibling = etree.SubElement(anchor, _tag(ns, sub_type))
                    sib_title = etree.SubElement(sibling, _tag(ns, "title"))
                    _safe_text(sib_title, text)
                    sib_body = etree.SubElement(sibling, _tag(ns, sub_body_tag))
                    # Redirect content into sibling body
                    body = sib_body
                    current_section = None
                    current_sectiondiv = None
                    topic_type = sub_type   # update so numbered_li resolves correctly
                continue

            parent = current_sectiondiv or current_section or body

            # ---- Paragraph ----
            if de == "p":
                flush_all()
                p_el = etree.SubElement(parent, _tag(ns, "p"))
                _apply_inline(p_el, text, ns, bold=meta.get("bold", False))
                continue

            # ---- Menucascade ----
            if de == "menucascade":
                flush_all()
                # menucascade is inline — must be wrapped in <p>
                p_el = etree.SubElement(parent, _tag(ns, "p"))
                mc = etree.SubElement(p_el, _tag(ns, "menucascade"))
                for segment in re.split(r"\s*>\s*", text):
                    seg = segment.strip()
                    if seg:
                        uc = etree.SubElement(mc, _tag(ns, "uicontrol"))
                        _safe_text(uc, seg)
                continue

            # ---- List items (buffered) ----
            if de == "step":
                flush_ul()
                flush_ol()
                step_buffer.append(block)
                continue

            if de == "ul_li":
                flush_steps()
                flush_ol()
                ul_buffer.append(block)
                continue

            if de == "ol_li":
                flush_steps()
                flush_ul()
                ol_buffer.append(block)
                continue

            if de == "numbered_li":
                # Resolve at render time using the per-chunk topic_type
                # that the generator has already correctly detected.
                if topic_type == "task":
                    flush_ul()
                    flush_ol()
                    step_buffer.append(block)
                else:
                    flush_steps()
                    flush_ul()
                    ol_buffer.append(block)
                continue

            # ---- Hazard statement / Note ----
            if de and (de.startswith("note:") or de.startswith("hazard:")):
                flush_all()
                note_type = de.split(":", 1)[1]
                HAZARD_TYPES = {"warning", "caution", "danger", "notice"}
                if note_type in HAZARD_TYPES:
                    hs = etree.SubElement(parent, _tag(ns, "hazardstatement"))
                    hs.set("type", note_type)
                    mp = etree.SubElement(hs, _tag(ns, "messagepanel"))
                    toh = etree.SubElement(mp, _tag(ns, "typeofhazard"))
                    _safe_text(toh, text)
                else:
                    note_el = etree.SubElement(parent, _tag(ns, "note"))
                    note_el.set("type", note_type)
                    note_p = etree.SubElement(note_el, _tag(ns, "p"))
                    _apply_inline(note_p, text, ns)
                continue

            # ---- Figure ----
            if de == "fig":
                flush_all()
                caption = meta.get("caption", text)
                image_href = meta.get("image_href", "")
                fig_el = etree.SubElement(parent, _tag(ns, "fig"))
                fig_title = etree.SubElement(fig_el, _tag(ns, "title"))
                _safe_text(fig_title, caption)
                img_el = etree.SubElement(fig_el, _tag(ns, "image"))
                if image_href:
                    img_el.set("href", image_href)
                else:
                    img_el.set("href", "")
                    img_el.set("placement", "inline")
                    alt = etree.SubElement(img_el, _tag(ns, "alt"))
                    _safe_text(alt, f"[IMAGE — {caption}]")
                continue

            # ---- Codeblock ----
            if de == "codeblock":
                flush_all()
                cb_el = etree.SubElement(parent, _tag(ns, "codeblock"))
                _safe_text(cb_el, text)
                continue

            # ---- Table (CALS) ----
            if de == "table":
                flush_all()
                rows = block.get("rows", [])
                if not rows:
                    continue

                # Use n_header_rows from extractor metadata if available,
                # otherwise fall back to straddle-based detection
                n_header_rows = block.get("metadata", {}).get("n_header_rows", None)
                if n_header_rows is None:
                    n_header_rows = 1
                    for ri in range(1, len(rows)):
                        if any("__STRADDLE__" in str(c) for c in rows[ri]):
                            n_header_rows = ri + 1
                        else:
                            break

                ncols = max(len(r) for r in rows)
                tbl = etree.SubElement(parent, _tag(ns, "table"))
                tbl.set("frame", "all")
                tgroup = etree.SubElement(tbl, _tag(ns, "tgroup"))
                tgroup.set("cols", str(ncols))
                for ci in range(1, ncols + 1):
                    cs = etree.SubElement(tgroup, _tag(ns, "colspec"))
                    cs.set("colname", f"col{ci}")
                    cs.set("colnum", str(ci))

                def _make_row(parent_el, row_data, row_ncols, is_header_row=False):
                    """Emit a <row> with entry elements, handling straddle and bold markers."""
                    row_el = etree.SubElement(parent_el, _tag(ns, "row"))
                    ci = 0
                    while ci < row_ncols:
                        cell_val = str(row_data[ci]) if ci < len(row_data) else ""
                        next_val = str(row_data[ci + 1]) if ci + 1 < len(row_data) else ""
                        if next_val.startswith("__STRADDLE__"):
                            span = int(next_val.split("__")[2]) if "__" in next_val else row_ncols
                            entry = etree.SubElement(row_el, _tag(ns, "entry"))
                            entry.set("namest", f"col{ci + 1}")
                            entry.set("nameend", f"col{ci + span}")
                            # Strip __BOLD__ from straddle cell value for clean_val
                            import re as _re
                            clean_val = _re.sub(r'__TM__[a-z]+__', '', cell_val)
                            clean_val = clean_val[8:] if clean_val.startswith("__BOLD__") else clean_val
                            if is_header_row:
                                _safe_text(entry, clean_val.strip())
                            else:
                                # Non-thead straddle: render bold explicitly
                                _apply_inline(entry, f"__BOLD__{clean_val.strip()}" if clean_val.strip() else "", ns)
                            ci += span
                        else:
                            entry = etree.SubElement(row_el, _tag(ns, "entry"))
                            if is_header_row:
                                # thead: strip sentinels — toolchain renders header bold
                                import re as _re
                                clean_val = _re.sub(r'__TM__[a-z]+__', '', cell_val)
                                clean_val = clean_val[8:] if clean_val.startswith("__BOLD__") else clean_val
                                _safe_text(entry, clean_val.strip())
                            else:
                                _apply_inline(entry, cell_val, ns)
                            ci += 1
                    # Pad missing cells
                    cells_emitted = len(row_el)
                    for _ in range(row_ncols - cells_emitted):
                        etree.SubElement(row_el, _tag(ns, "entry"))

                # Header rows
                thead_el = etree.SubElement(tgroup, _tag(ns, "thead"))
                for ri in range(n_header_rows):
                    _make_row(thead_el, rows[ri], ncols, is_header_row=True)

                # Body rows
                if len(rows) > n_header_rows:
                    tbody_el = etree.SubElement(tgroup, _tag(ns, "tbody"))
                    for row_data in rows[n_header_rows:]:
                        _make_row(tbody_el, row_data, ncols, is_header_row=False)
                continue

# ---- Dropped / None ----
            if de in ("dropped", None):
                continue

            # ---- Generic fallback — silently drop unmapped blocks ----
            # Writers don't use fallback syntax; unknown blocks are dropped
            # rather than emitting stray <p> elements that corrupt structure.
            pass  # drop

        flush_all()
