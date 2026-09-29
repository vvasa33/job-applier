"""Read-only parsing of a master resume's LaTeX source."""

import re
from dataclasses import dataclass

_SECTION = re.compile(r"\\section\*?\{([^{}]*)\}")
_ENTRY = re.compile(
    r"\\resumeSubheading\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}"
    r"|\\resumeItem\s*\{([^{}]*)\}"
    r"|\\item\s+([^\n\\]+)"
)

_KINDS = {
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "employment": "experience",
    "education": "education",
    "projects": "projects",
    "personal projects": "projects",
    "skills": "skills",
    "technical skills": "skills",
}


@dataclass(frozen=True)
class ResumeSection:
    name: str
    kind: str
    entries: tuple[str, ...]


@dataclass(frozen=True)
class ParsedResume:
    sections: tuple[ResumeSection, ...]

    def as_dict(self) -> dict:
        return {
            "sections": [
                {"name": section.name, "kind": section.kind, "entries": list(section.entries)}
                for section in self.sections
            ]
        }


def parse_resume(source: str) -> ParsedResume:
    text = _without_comments(source)
    matches = list(_SECTION.finditer(text))
    sections: list[ResumeSection] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        name = " ".join(match.group(1).split())
        kind = _KINDS.get(name.casefold(), "other")
        sections.append(ResumeSection(name=name, kind=kind, entries=_entries(text[start:end])))
    return ParsedResume(sections=tuple(sections))


def _entries(body: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in _ENTRY.finditer(body):
        if match.group(1) is not None:
            parts = [match.group(1), match.group(3), match.group(2), match.group(4)]
            found.append(", ".join(part.strip() for part in parts if part and part.strip()))
        else:
            text = match.group(5) if match.group(5) is not None else match.group(6)
            cleaned = " ".join(text.split())
            if cleaned:
                found.append(cleaned)
    return tuple(found)


def _without_comments(source: str) -> str:
    kept: list[str] = []
    for line in source.splitlines():
        output: list[str] = []
        index = 0
        while index < len(line):
            if line[index] == "%" and (index == 0 or line[index - 1] != "\\"):
                break
            output.append(line[index])
            index += 1
        kept.append("".join(output))
    return "\n".join(kept)
