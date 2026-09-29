"""Read and compile the one configured master resume without writing to it."""

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.config import Settings
from jobhunter.db.models import ResumeVersion
from jobhunter.db.records import add_master_resume, add_resume_version
from jobhunter.domain.enums import ResumeVersionKind
from jobhunter.resume.parser import ParsedResume, parse_resume


class MasterResumeError(Exception):
    """The master resume cannot be validated or compiled."""


@dataclass(frozen=True)
class MasterResumeReport:
    path: str
    sha256: str
    parsed: ParsedResume
    resume_id: int


@dataclass(frozen=True)
class CompileReport:
    path: str
    sha256: str
    pdf_path: str


def validate_master(session: Session, settings: Settings) -> MasterResumeReport:
    path = _master_path(settings)
    source, digest = _read_master(path)
    parsed = parse_resume(source)
    resume = add_master_resume(
        session,
        path=str(path),
        sha256=digest,
        structure=parsed.as_dict(),
    )
    already = session.scalar(
        select(ResumeVersion.id).where(
            ResumeVersion.resume_id == resume.id,
            ResumeVersion.kind == ResumeVersionKind.master_snapshot,
            ResumeVersion.sha256 == digest,
        )
    )
    if already is None:
        add_resume_version(
            session,
            resume=resume,
            kind=ResumeVersionKind.master_snapshot,
            sha256=digest,
            tex_path=str(path),
        )
    session.commit()
    _read_master(path, expected_sha256=digest)
    return MasterResumeReport(path=str(path), sha256=digest, parsed=parsed, resume_id=resume.id)


def compile_master(session: Session, settings: Settings) -> CompileReport:
    report = validate_master(session, settings)
    path = Path(report.path)
    output_dir = settings.data_dir / "resume"
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / "master.pdf"
    if pdf_path.resolve() == path.resolve():
        raise MasterResumeError("refusing to write the compiled PDF over the master resume")

    with tempfile.TemporaryDirectory(prefix="jobhunter-resume-") as temp_name:
        temp = Path(temp_name)
        copied = temp / path.name
        copied.write_bytes(path.read_bytes())
        _run_pdflatex(source=copied, output_dir=temp, master_dir=path.parent)
        produced = temp / f"{path.stem}.pdf"
        if not produced.is_file():
            raise MasterResumeError("pdflatex finished without writing a PDF")
        pdf_path.write_bytes(produced.read_bytes())

    _read_master(path, expected_sha256=report.sha256)
    return CompileReport(path=str(path), sha256=report.sha256, pdf_path=str(pdf_path))


def _master_path(settings: Settings) -> Path:
    if settings.master_resume is None:
        raise MasterResumeError("master resume path is not configured. Set JOBHUNTER_MASTER_RESUME.")
    return settings.master_resume.expanduser()


def _read_master(path: Path, expected_sha256: str | None = None) -> tuple[str, str]:
    if not path.is_file():
        raise MasterResumeError(f"master resume file does not exist: {path}")
    if path.suffix.lower() != ".tex":
        raise MasterResumeError(f"master resume must be a LaTeX .tex file: {path}")
    descriptor = os.open(path, os.O_RDONLY)
    try:
        data = os.read(descriptor, 8_000_000)
        extra = os.read(descriptor, 1)
    finally:
        os.close(descriptor)
    if extra:
        raise MasterResumeError(f"master resume is larger than 8 MB: {path}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MasterResumeError(f"master resume is not UTF-8 text: {path}") from exc
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise MasterResumeError("master resume changed during validation")
    return text, digest


def _run_pdflatex(*, source: Path, output_dir: Path, master_dir: Path) -> None:
    if output_dir.resolve() == master_dir.resolve():
        raise MasterResumeError("refusing to run pdflatex in the master resume directory")
    binary = shutil.which("pdflatex")
    if binary is None:
        raise MasterResumeError("pdflatex is not installed")
    env = os.environ.copy()
    env["TEXINPUTS"] = f"{master_dir}{os.pathsep}"
    try:
        completed = subprocess.run(
            [
                binary,
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-output-directory={output_dir}",
                source.name,
            ],
            cwd=source.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MasterResumeError("pdflatex timed out after 60 seconds") from exc
    if completed.returncode != 0:
        raise MasterResumeError(_latex_failure(completed.stdout + "\n" + completed.stderr))


def _latex_failure(log: str) -> str:
    chosen: list[str] = []
    lines = log.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("!"):
            chosen.append(line.strip())
            if index + 1 < len(lines) and lines[index + 1].strip():
                chosen.append(lines[index + 1].strip())
    if not chosen:
        return "pdflatex failed without a LaTeX error line"
    return "\n".join(chosen[:8])
