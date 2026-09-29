"""Locate editable bullets in master LaTeX by offset. Headings, dates, and the preamble are never editable."""

import re
from dataclasses import dataclass

from jobhunter.resume.parser import _KINDS

_SECTION = re.compile(r"\\section\*?\{([^{}]*)\}")
_SUBHEADING = re.compile(
    r"\\resumeSubheading\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}"
)
_BULLET = re.compile(r"\\resumeItem\s*\{|\\item(?![A-Za-z])")
_BEGIN_DOCUMENT = re.compile(r"\\begin\{document\}")
_END_DOCUMENT = re.compile(r"\\end\{document\}")
_WRAPPER = re.compile(
    r"\\(?:textbf|textit|emph|underline|texttt|textsc|small|footnotesize|large|mbox)\s*\{([^{}]*)\}"
)
_HREF = re.compile(r"\\href\s*\{[^{}]*\}\s*\{([^{}]*)\}")


@dataclass(frozen=True)
class Bullet:
    id: str
    block_id: str
    section: str
    section_kind: str
    heading: str
    start: int
    end: int
    raw: str
    plain: str
    style: str


@dataclass(frozen=True)
class Block:
    """Consecutive bullets under one heading. Only whitespace separates them."""

    id: str
    section: str
    section_kind: str
    heading: str
    bullet_ids: tuple[str, ...]
    start: int
    end: int
    indent: str
    separator: str


@dataclass(frozen=True)
class ResumeDocument:
    source: str
    bullets: dict[str, Bullet]
    blocks: tuple[Block, ...]
    sections: tuple[tuple[str, str], ...]

    def block(self, block_id: str) -> Block:
        return next(block for block in self.blocks if block.id == block_id)


def locate(source: str) -> ResumeDocument:
    masked = mask_comments(source)
    begin = _BEGIN_DOCUMENT.search(masked)
    body_start = begin.end() if begin else 0
    end = _END_DOCUMENT.search(masked, body_start)
    body_end = end.start() if end else len(masked)

    matches = list(_SECTION.finditer(masked, body_start, body_end))
    bullets: dict[str, Bullet] = {}
    blocks: list[Block] = []
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        stop = matches[index + 1].start() if index + 1 < len(matches) else body_end
        name = " ".join(match.group(1).split())
        kind = _KINDS.get(name.casefold(), "other")
        sections.append((name, kind))
        _collect(source, masked, start, stop, name, kind, bullets, blocks)
    return ResumeDocument(source=source, bullets=bullets, blocks=tuple(blocks), sections=tuple(sections))


def _collect(source, masked, start, stop, section, kind, bullets, blocks) -> None:
    headings = [
        (heading.start(), heading.end(), _heading_text(heading))
        for heading in _SUBHEADING.finditer(masked, start, stop)
    ]
    current: list[Bullet] = []
    current_heading: int | None = None
    cursor = start

    def close() -> None:
        if not current:
            return
        block_id = f"blk{len(blocks) + 1}"
        first = current[0]
        separator = masked[first.end : current[1].start] if len(current) > 1 else "\n" + _indent(source, first.start)
        grouped = [_with_block(bullet, block_id) for bullet in current]
        for bullet in grouped:
            bullets[bullet.id] = bullet
        blocks.append(
            Block(
                id=block_id,
                section=section,
                section_kind=kind,
                heading=first.heading,
                bullet_ids=tuple(bullet.id for bullet in grouped),
                start=first.start,
                end=current[-1].end,
                indent=_indent(source, first.start),
                separator=separator,
            )
        )
        current.clear()

    for match in _BULLET.finditer(masked, start, stop):
        if match.start() < cursor or any(h_start <= match.start() < h_end for h_start, h_end, _ in headings):
            continue
        style, end = _bullet_end(masked, match, stop)
        if end is None:
            continue
        raw = source[match.start() : end]
        plain = latex_to_plain(_inner(raw, style))
        cursor = end
        if not plain:
            continue
        heading_index = max((i for i, (_, h_end, _) in enumerate(headings) if h_end <= match.start()), default=None)
        heading = headings[heading_index][2] if heading_index is not None else section
        if current and (heading_index != current_heading or masked[current[-1].end : match.start()].strip()):
            close()
        current_heading = heading_index
        current.append(
            Bullet(
                id=f"b{len(bullets) + len(current) + 1}",
                block_id="",
                section=section,
                section_kind=kind,
                heading=heading,
                start=match.start(),
                end=end,
                raw=raw,
                plain=plain,
                style=style,
            )
        )
    close()


def _with_block(bullet: Bullet, block_id: str) -> Bullet:
    return Bullet(**{**bullet.__dict__, "block_id": block_id})


def _bullet_end(masked: str, match: re.Match, stop: int) -> tuple[str, int | None]:
    if match.group(0).startswith("\\resumeItem"):
        end = _balanced(masked, match.end() - 1)
        return "resumeItem", end if end is not None and end <= stop else None
    position = match.end()
    while position < stop and masked[position] in " \t":
        position += 1
    if position < stop and masked[position] == "{":
        end = _balanced(masked, position)
        return "item_braced", end if end is not None and end <= stop else None
    line_end = masked.find("\n", match.end())
    end = stop if line_end == -1 else min(line_end, stop)
    while end > match.end() and masked[end - 1] in " \t":
        end -= 1
    return "item", end


def _balanced(text: str, open_index: int) -> int | None:
    depth = 0
    index = open_index
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _inner(raw: str, style: str) -> str:
    if style == "item":
        return raw[len("\\item") :]
    return raw[raw.index("{") + 1 : -1]


def _indent(source: str, position: int) -> str:
    line_start = source.rfind("\n", 0, position) + 1
    prefix = source[line_start:position]
    return prefix if not prefix.strip() else ""


def _heading_text(match: re.Match) -> str:
    parts = (latex_to_plain(match.group(index)) for index in (1, 3, 2, 4))
    return ", ".join(part for part in parts if part)


def mask_comments(source: str) -> str:
    """Blank out comments while keeping every offset aligned with the source."""
    chars = list(source)
    index = 0
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == "%":
            line_end = source.find("\n", index)
            line_end = len(source) if line_end == -1 else line_end
            for position in range(index, line_end):
                chars[position] = " "
            index = line_end
            continue
        index += 1
    return "".join(chars)


def latex_to_plain(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = _HREF.sub(r"\1", text)
        text = _WRAPPER.sub(r"\1", text)
    text = text.replace("$|$", "|")
    text = re.sub(r"(?<!\\)\$", "", text)
    text = text.replace("\\\\", " ")
    text = re.sub(r"\\([%&$#_{}])", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\*?", " ", text)
    text = text.replace("{", " ").replace("}", " ").replace("~", " ").replace("--", "-")
    return " ".join(text.split())
