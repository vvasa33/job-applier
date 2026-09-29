"""Tailor one application's resume from validated, evidence-cited bullets. The master is only read."""

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.config import Settings
from jobhunter.db.models import Application, Job, Resume, ResumeVersion
from jobhunter.db.records import add_resume_version
from jobhunter.domain.enums import ResumeVersionKind
from jobhunter.llm.client import LLMClient
from jobhunter.llm.errors import LLMError
from jobhunter.llm.schemas import TailoringPlan
from jobhunter.matching.profiles import JobProfile, resume_profile_from_parsed
from jobhunter.matching.result import MatchResult
from jobhunter.matching.service import match_job
from jobhunter.resume.application import (
    ApplicationResumeError,
    _artifact_paths,
    _compile_pdf,
    _valid_pdf,
)
from jobhunter.resume.evidence import skills_vocabulary, trace_problems
from jobhunter.resume.master import _read_master, validate_master
from jobhunter.resume.spans import Block, ResumeDocument, locate

REPRESENTATION = "resume.json"
EVIDENCE = "evidence.json"
CHANGE_REPORT = "change_report.md"

_ESCAPES = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


class TailoringPlanner(Protocol):
    def plan(
        self,
        job: JobProfile,
        entries: list[tuple[str, str]],
        requirements: list[str],
        evidence: list[str],
    ) -> TailoringPlan: ...


class LLMTailoringPlanner:
    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def plan(self, job, entries, requirements, evidence) -> TailoringPlan:
        return self._client.tailor_resume(job, entries, requirements=requirements, evidence=evidence).output


@dataclass(frozen=True)
class OutputBullet:
    block_id: str
    text: str
    source_ids: tuple[str, ...]
    change: str


@dataclass(frozen=True)
class Rejection:
    text: str
    source_ids: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass
class BlockOutcome:
    block: Block
    bullets: list[OutputBullet]
    touched: bool = False
    fell_back: bool = False
    removed: list[str] = field(default_factory=list)
    reordered: bool = False


@dataclass(frozen=True)
class TailoringOutcome:
    tex: str
    blocks: tuple[BlockOutcome, ...]
    rejections: tuple[Rejection, ...]


@dataclass(frozen=True)
class TailoringReport:
    application_id: int
    directory: str
    tex_path: str
    pdf_path: str
    representation_path: str
    evidence_path: str
    change_report_path: str
    sha256: str
    version_id: int
    applied_changes: int
    rejected: int
    plan_error: str | None


def apply_plan(document: ResumeDocument, plan: TailoringPlan | None) -> TailoringOutcome:
    vocabulary = skills_vocabulary(document)
    proposed: dict[str, list[OutputBullet]] = {}
    failed_blocks: set[str] = set()
    rejections: list[Rejection] = []
    used: set[str] = set()

    for bullet in plan.bullets if plan is not None else []:
        text = " ".join(bullet.text.split())
        ids = tuple(dict.fromkeys(item.strip() for item in bullet.source_ids if item.strip()))
        known = [item for item in ids if item in document.bullets]
        blocks = {document.bullets[item].block_id for item in known}
        reasons = _structure_problems(ids, known, blocks, used)
        if not reasons:
            sources = [document.bullets[item] for item in ids]
            block = document.block(sources[0].block_id)
            evidence = " ".join([block.heading, *(source.plain for source in sources)])
            reasons = trace_problems(text, evidence, vocabulary)
        if reasons:
            rejections.append(Rejection(text=text, source_ids=ids, reasons=tuple(reasons)))
            failed_blocks.update(blocks)
            continue
        used.update(ids)
        if len(ids) > 1:
            change = "combined"
        elif text == sources[0].plain:
            change = "unchanged"
        else:
            change = "rewritten"
        proposed.setdefault(sources[0].block_id, []).append(
            OutputBullet(block_id=sources[0].block_id, text=text, source_ids=ids, change=change)
        )

    outcomes = tuple(_block_outcome(document, block, proposed.get(block.id), block.id in failed_blocks) for block in document.blocks)
    return TailoringOutcome(tex=_render(document, outcomes), blocks=outcomes, rejections=tuple(rejections))


def _structure_problems(ids, known, blocks, used) -> list[str]:
    reasons: list[str] = []
    if not ids:
        reasons.append("has no source evidence")
    unknown = [item for item in ids if item not in known]
    if unknown:
        reasons.append(f"cites evidence that is not in the master resume: {', '.join(unknown)}")
    if len(blocks) > 1:
        reasons.append("combines evidence from different resume entries")
    reused = [item for item in known if item in used]
    if reused:
        reasons.append(f"reuses evidence already used by another bullet: {', '.join(reused)}")
    return reasons


def _block_outcome(document, block: Block, proposed: list[OutputBullet] | None, failed: bool) -> BlockOutcome:
    original = [
        OutputBullet(block_id=block.id, text=document.bullets[item].plain, source_ids=(item,), change="unchanged")
        for item in block.bullet_ids
    ]
    if failed or not proposed:
        return BlockOutcome(block=block, bullets=original, fell_back=failed)
    referenced = {item for bullet in proposed for item in bullet.source_ids}
    removed = [item for item in block.bullet_ids if item not in referenced]
    leads = [bullet.source_ids[0] for bullet in proposed]
    reordered = leads != [item for item in block.bullet_ids if item in set(leads)]
    changed = removed or reordered or any(bullet.change != "unchanged" for bullet in proposed)
    if not changed:
        return BlockOutcome(block=block, bullets=original)
    return BlockOutcome(block=block, bullets=proposed, touched=True, removed=removed, reordered=reordered)


def _render(document: ResumeDocument, outcomes: tuple[BlockOutcome, ...]) -> str:
    text = document.source
    for outcome in reversed(outcomes):
        if not outcome.touched:
            continue
        block = outcome.block
        pieces = [
            f"% evidence: {', '.join(bullet.source_ids)}\n{block.indent}{_bullet_tex(document, bullet)}"
            for bullet in outcome.bullets
        ]
        text = text[: block.start] + block.separator.join(pieces) + text[block.end :]
    return text


def _bullet_tex(document: ResumeDocument, bullet: OutputBullet) -> str:
    source = document.bullets[bullet.source_ids[0]]
    if bullet.change == "unchanged":
        return source.raw
    escaped = "".join(_ESCAPES.get(char, char) for char in bullet.text)
    if source.style == "resumeItem":
        return f"\\resumeItem{{{escaped}}}"
    if source.style == "item_braced":
        return f"\\item{{{escaped}}}"
    return f"\\item {escaped}"


def tailor_application_resume(
    session: Session,
    settings: Settings,
    application_id: int,
    planner: TailoringPlanner,
    *,
    match: MatchResult | None = None,
) -> TailoringReport:
    application = session.get(Application, application_id)
    if application is None:
        raise ApplicationResumeError(f"application {application_id} does not exist")
    existing = session.scalar(
        select(ResumeVersion.id).where(
            ResumeVersion.application_id == application_id,
            ResumeVersion.kind == ResumeVersionKind.tailored,
        )
    )
    if existing is not None:
        raise ApplicationResumeError(
            f"application {application_id} already has a resume version; artifacts are never replaced"
        )

    master = validate_master(session, settings)
    master_path = Path(master.path)
    source, master_digest = _read_master(master_path, expected_sha256=master.sha256)
    tex_path, pdf_path = _artifact_paths(settings, application_id, master_path)
    directory = tex_path.parent
    if directory.exists():
        raise ApplicationResumeError(f"application resume directory already exists and will not be replaced: {directory}")

    job = application.job
    profile = job_profile(job)
    if match is None:
        match = match_job(profile, resume_profile_from_parsed(master.parsed))
    document = locate(source)
    entries = [
        (bullet.id, f"[{bullet.section} / {bullet.heading}] {bullet.plain}")
        for bullet in document.bullets.values()
    ]
    requirements = [f"{item.kind.value}: {item.value}" for item in job.requirements]

    plan: TailoringPlan | None = None
    plan_error: str | None = None
    try:
        plan = planner.plan(profile, entries, requirements, match_evidence(match))
    except LLMError as exc:
        plan_error = str(exc)

    outcome = apply_plan(document, plan)
    tex_bytes = outcome.tex.encode("utf-8")
    digest = hashlib.sha256(tex_bytes).hexdigest()
    bullets = _numbered(outcome)
    files = {
        REPRESENTATION: _json(representation(application_id, job, master_digest, document, bullets)),
        EVIDENCE: _json(evidence_mapping(document, bullets)),
        CHANGE_REPORT: change_report(
            application_id,
            job,
            master_path,
            master_digest,
            digest,
            document,
            outcome,
            plan_error,
            plan.explanation if plan is not None else "",
        ),
    }

    _publish(directory, tex_bytes, files, master_path)
    try:
        version = _store_version(session, application, master.resume_id, digest, tex_path, pdf_path, directory)
        session.commit()
    except Exception:
        session.rollback()
        shutil.rmtree(directory, ignore_errors=True)
        raise
    _read_master(master_path, expected_sha256=master_digest)

    return TailoringReport(
        application_id=application_id,
        directory=str(directory),
        tex_path=str(tex_path),
        pdf_path=str(pdf_path),
        representation_path=str(directory / REPRESENTATION),
        evidence_path=str(directory / EVIDENCE),
        change_report_path=str(directory / CHANGE_REPORT),
        sha256=digest,
        version_id=version.id,
        applied_changes=sum(_change_count(block) for block in outcome.blocks),
        rejected=len(outcome.rejections),
        plan_error=plan_error,
    )


def job_profile(job: Job) -> JobProfile:
    return JobProfile(
        title=job.title,
        company=job.company_name,
        locations=list(job.locations or []),
        location_class=job.location_class,
        is_internship=job.is_internship,
        cs_relevance=job.cs_relevance,
        description_text=job.description_text,
    )


def match_evidence(match: MatchResult) -> list[str]:
    lines: list[str] = []
    if match.matched_skills:
        lines.append(f"Skills on both the job and the resume: {', '.join(match.matched_skills)}")
    if match.missing_skills:
        lines.append(
            f"Job skills missing from the resume (do not add them): {', '.join(match.missing_skills)}"
        )
    lines.extend(f"Relevant resume entry: {entry}" for entry in match.relevant_experience)
    lines.append(f"Location: {match.location_fit.summary}")
    lines.append(f"Internship: {match.internship_fit.summary}")
    return lines


def _publish(directory: Path, tex_bytes: bytes, files: dict[str, str], master_path: Path) -> None:
    """Build every artifact in a private directory, then rename it into place in one step."""
    directory.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(tempfile.mkdtemp(prefix=f".{directory.name}-", suffix=".partial", dir=directory.parent))
    try:
        (partial / "resume.tex").write_bytes(tex_bytes)
        _compile_pdf(tex_bytes, partial / "resume.pdf", master_path)
        if (partial / "resume.tex").read_bytes() != tex_bytes or not _valid_pdf(partial / "resume.pdf"):
            raise ApplicationResumeError("compiled tailored resume failed validation")
        for name, content in files.items():
            (partial / name).write_text(content, encoding="utf-8")
        if directory.exists():
            raise ApplicationResumeError(f"application resume directory appeared during tailoring: {directory}")
        os.rename(partial, directory)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def _store_version(session, application, resume_id, digest, tex_path, pdf_path, directory) -> ResumeVersion:
    resume = session.get(Resume, resume_id)
    if resume is None:
        raise ApplicationResumeError("master resume record disappeared during tailoring")
    return add_resume_version(
        session,
        resume=resume,
        kind=ResumeVersionKind.tailored,
        application=application,
        sha256=digest,
        tex_path=str(tex_path),
        pdf_path=str(pdf_path),
        diff_path=str(directory / CHANGE_REPORT),
    )


def _numbered(outcome: TailoringOutcome) -> list[tuple[str, BlockOutcome, OutputBullet]]:
    rows = []
    for block in outcome.blocks:
        for bullet in block.bullets:
            rows.append((f"t{len(rows) + 1}", block, bullet))
    return rows


def representation(application_id, job: Job, master_digest, document: ResumeDocument, bullets) -> dict:
    sections = []
    for name, kind in document.sections:
        blocks = []
        for block in document.blocks:
            if block.section != name:
                continue
            blocks.append(
                {
                    "heading": block.heading,
                    "bullets": [
                        {"id": output_id, "text": bullet.text, "source_ids": list(bullet.source_ids)}
                        for output_id, owner, bullet in bullets
                        if owner.block.id == block.id
                    ],
                }
            )
        sections.append({"name": name, "kind": kind, "entries": blocks})
    return {
        "application_id": application_id,
        "job": {"title": job.title, "company": job.company_name},
        "master_sha256": master_digest,
        "sections": sections,
    }


def evidence_mapping(document: ResumeDocument, bullets) -> dict:
    return {
        "bullets": [
            {
                "id": output_id,
                "section": owner.block.section,
                "entry": owner.block.heading,
                "text": bullet.text,
                "change": bullet.change,
                "sources": [
                    {"id": item, "text": document.bullets[item].plain} for item in bullet.source_ids
                ],
            }
            for output_id, owner, bullet in bullets
        ]
    }


def change_report(
    application_id,
    job: Job,
    master_path: Path,
    master_digest: str,
    digest: str,
    document: ResumeDocument,
    outcome: TailoringOutcome,
    plan_error: str | None,
    explanation: str,
) -> str:
    changed = [block for block in outcome.blocks if block.touched or block.fell_back]
    lines = [
        f"# Resume changes for application {application_id}",
        "",
        f"Job: {job.title} at {job.company_name}",
        f"Master resume: {master_path} (sha256 {master_digest}, not modified)",
        f"Generated resume.tex sha256: {digest}",
        "",
        "## Summary",
        "",
        f"{sum(_change_count(block) for block in outcome.blocks)} changes applied, "
        f"{len(outcome.rejections)} suggestions rejected.",
        "Every bullet in resume.tex is preceded by a comment naming the master entries it came from.",
        "",
    ]
    if plan_error is not None:
        lines += [
            "## Tailoring plan",
            "",
            f"No plan was applied: {plan_error}",
            "The generated resume is an exact copy of the master resume.",
            "",
        ]
    elif explanation:
        lines += ["## Planner explanation", "", explanation, ""]

    for block in changed:
        title = block.block.section if block.block.heading == block.block.section else f"{block.block.section}: {block.block.heading}"
        lines += [f"## {title}", ""]
        if block.fell_back:
            lines += ["- Kept the original bullets because a suggestion for this entry was rejected.", ""]
            continue
        for bullet in block.bullets:
            ids = ", ".join(bullet.source_ids)
            if bullet.change == "unchanged":
                lines.append(f"- Kept {ids} unchanged.")
                continue
            verb = "Combined" if bullet.change == "combined" else "Rewrote"
            lines.append(f"- {verb} {ids}.")
            for item in bullet.source_ids:
                lines.append(f"  - Before ({item}): {document.bullets[item].plain}")
            lines.append(f"  - After: {bullet.text}")
        for item in block.removed:
            lines.append(f"- Removed {item}: {document.bullets[item].plain}")
        if block.reordered:
            lines.append("- Reordered the bullets for this entry.")
        lines.append("")

    if outcome.rejections:
        lines += ["## Rejected suggestions", ""]
        for rejection in outcome.rejections:
            cited = ", ".join(rejection.source_ids) or "none"
            lines.append(f'- "{rejection.text}" (evidence: {cited})')
            lines.extend(f"  - Rejected because it {reason}." for reason in rejection.reasons)
        lines.append("")

    untouched = [
        f"{block.block.section}: {block.block.heading}" if block.block.heading != block.block.section else block.block.section
        for block in outcome.blocks
        if not block.touched and not block.fell_back
    ]
    if untouched:
        lines += ["## Unchanged entries", ""]
        lines.extend(f"- {name}" for name in untouched)
        lines.append("")
    return "\n".join(lines)


def _change_count(block: BlockOutcome) -> int:
    if not block.touched:
        return 0
    edits = sum(1 for bullet in block.bullets if bullet.change != "unchanged")
    return edits + len(block.removed) + (1 if block.reordered else 0)


def _json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
