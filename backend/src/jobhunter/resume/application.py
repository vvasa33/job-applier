"""Write one immutable resume copy for an application. The master file is never written."""

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.config import Settings
from jobhunter.db.models import Application, Resume, ResumeVersion
from jobhunter.db.records import add_resume_version
from jobhunter.domain.enums import ResumeVersionKind
from jobhunter.resume.master import MasterResumeError, _read_master, _run_pdflatex, validate_master


class ApplicationResumeError(MasterResumeError):
    """An application resume could not be copied, compiled, or validated."""


@dataclass(frozen=True)
class ApplicationResumeReport:
    application_id: int
    tex_path: str
    pdf_path: str
    sha256: str
    version_id: int


def prepare_application_resume(
    session: Session,
    settings: Settings,
    application_id: int,
) -> ApplicationResumeReport:
    application = session.get(Application, application_id)
    if application is None:
        raise ApplicationResumeError(f"application {application_id} does not exist")

    master = validate_master(session, settings)
    master_path = Path(master.path)
    source, digest = _read_master(master_path, expected_sha256=master.sha256)
    tex_bytes = source.encode("utf-8")

    tex_path, pdf_path = _artifact_paths(settings, application_id, master_path)
    created = _publish(tex_path, pdf_path, tex_bytes, master_path)
    try:
        _validate_files(tex_path, pdf_path, digest, master_path)
        version = _store_version(session, application, master.resume_id, digest, tex_path, pdf_path)
        session.commit()
    except Exception:
        session.rollback()
        if created:
            _remove_created(tex_path, pdf_path)
        raise
    _read_master(master_path, expected_sha256=digest)
    return ApplicationResumeReport(
        application_id=application_id,
        tex_path=str(tex_path),
        pdf_path=str(pdf_path),
        sha256=digest,
        version_id=version.id,
    )


def _artifact_paths(settings: Settings, application_id: int, master_path: Path) -> tuple[Path, Path]:
    if application_id < 1:
        raise ApplicationResumeError("application id must be a positive integer")
    root = (settings.data_dir / "applications").resolve()
    directory = (root / str(application_id)).resolve()
    if directory.parent != root:
        raise ApplicationResumeError("application resume path escapes the applications directory")
    tex_path = directory / "resume.tex"
    pdf_path = directory / "resume.pdf"
    for path in (directory, tex_path, pdf_path):
        if path.resolve() == master_path.resolve():
            raise ApplicationResumeError("refusing to write an application resume over the master resume")
    return tex_path, pdf_path


def _publish(tex_path: Path, pdf_path: Path, tex_bytes: bytes, master_path: Path) -> bool:
    """Copy and compile. Return whether this call created the final files."""
    if tex_path.is_file():
        existing = tex_path.read_bytes()
        if existing != tex_bytes:
            raise ApplicationResumeError(
                f"application resume already exists and will not be replaced: {tex_path}"
            )
        if _valid_pdf(pdf_path):
            return False
        _compile_pdf(tex_bytes, pdf_path, master_path)
        return False

    directory = tex_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    partials = (directory / ".resume.tex.partial", directory / ".resume.pdf.partial")
    try:
        partials[0].write_bytes(tex_bytes)
        _compile_pdf(tex_bytes, partials[1], master_path)
        if partials[0].read_bytes() != tex_bytes or not _valid_pdf(partials[1]):
            raise ApplicationResumeError("compiled application resume failed validation")
        os.replace(partials[0], tex_path)
        try:
            os.replace(partials[1], pdf_path)
        except OSError:
            tex_path.unlink(missing_ok=True)
            raise
    except Exception:
        for partial in partials:
            partial.unlink(missing_ok=True)
        _remove_dir_if_empty(directory)
        raise
    return True


def _compile_pdf(tex_bytes: bytes, pdf_path: Path, master_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="jobhunter-application-resume-") as temp_name:
        temp = Path(temp_name)
        source = temp / "resume.tex"
        source.write_bytes(tex_bytes)
        try:
            _run_pdflatex(source=source, output_dir=temp, master_dir=master_path.parent)
        except MasterResumeError as exc:
            raise ApplicationResumeError(str(exc)) from exc
        produced = temp / "resume.pdf"
        if not produced.is_file():
            raise ApplicationResumeError("pdflatex finished without writing a PDF")
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(produced.read_bytes())


def _store_version(
    session: Session,
    application: Application,
    resume_id: int,
    digest: str,
    tex_path: Path,
    pdf_path: Path,
) -> ResumeVersion:
    resume = session.get(Resume, resume_id)
    if resume is None:
        raise ApplicationResumeError("master resume record disappeared during preparation")
    existing = session.scalar(
        select(ResumeVersion).where(
            ResumeVersion.application_id == application.id,
            ResumeVersion.kind == ResumeVersionKind.tailored,
            ResumeVersion.sha256 == digest,
        )
    )
    if existing is not None:
        return existing
    return add_resume_version(
        session,
        resume=resume,
        kind=ResumeVersionKind.tailored,
        application=application,
        sha256=digest,
        tex_path=str(tex_path),
        pdf_path=str(pdf_path),
    )


def _validate_files(tex_path: Path, pdf_path: Path, digest: str, master_path: Path) -> None:
    if tex_path.name != "resume.tex" or pdf_path.name != "resume.pdf":
        raise ApplicationResumeError("application resume files must be named resume.tex and resume.pdf")
    if tex_path.parent != pdf_path.parent:
        raise ApplicationResumeError("application resume files must live in the same directory")
    if tex_path.resolve() == master_path.resolve() or pdf_path.resolve() == master_path.resolve():
        raise ApplicationResumeError("application resume path points at the master resume")
    if not tex_path.is_file() or not pdf_path.is_file():
        raise ApplicationResumeError("application resume files are missing")
    data = tex_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ApplicationResumeError("application resume.tex does not match the recorded master hash")
    if not _valid_pdf(pdf_path):
        raise ApplicationResumeError("application resume.pdf is not a PDF")
    extras = {path.name for path in tex_path.parent.iterdir()} - {"resume.tex", "resume.pdf"}
    if extras:
        raise ApplicationResumeError(f"application resume directory has unexpected files: {', '.join(sorted(extras))}")


def _valid_pdf(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 5:
        return False
    with path.open("rb") as handle:
        return handle.read(5) == b"%PDF-"


def _remove_created(tex_path: Path, pdf_path: Path) -> None:
    tex_path.unlink(missing_ok=True)
    pdf_path.unlink(missing_ok=True)
    _remove_dir_if_empty(tex_path.parent)


def _remove_dir_if_empty(directory: Path) -> None:
    if directory.is_dir() and not any(directory.iterdir()):
        shutil.rmtree(directory)
