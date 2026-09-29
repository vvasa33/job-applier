import os
import stat
from pathlib import Path

import pytest

from jobhunter.config import Settings
from jobhunter.db.migrate import init_database
from jobhunter.db.models import ResumeVersion
from jobhunter.db.records import JobSighting, create_application, record_job_sighting
from jobhunter.db.session import build_engine, session_factory
from jobhunter.domain.enums import ResumeVersionKind
from jobhunter.resume.application import ApplicationResumeError, prepare_application_resume

VALID = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\item Built a data pipeline
\end{document}
"""

INVALID = r"""
\documentclass{article}
\begin{document}
\notacommand
\end{document}
"""


def _pdflatex(bin_dir: Path) -> None:
    script = bin_dir / "pdflatex"
    script.write_text(
        """#!/bin/sh
outdir="."
tex=""
while [ $# -gt 0 ]; do
  case "$1" in
    -output-directory=*) outdir="${1#*=}" ;;
    -output-directory) shift; outdir="$1" ;;
    -*) ;;
    *) tex="$1" ;;
  esac
  shift
done
if grep -q 'notacommand' "$tex"; then
  echo '! Undefined control sequence.'
  echo 'l.3 \\notacommand'
  exit 1
fi
base=$(basename "$tex" .tex)
mkdir -p "$outdir"
printf '%%PDF-1.1\\n' > "$outdir/$base.pdf"
exit 0
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def _world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _pdflatex(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    tex = tmp_path / "master.tex"
    tex.write_text(source, encoding="utf-8")
    settings = Settings(
        host="127.0.0.1",
        port=8765,
        data_dir=tmp_path / "data",
        log_level="WARNING",
        master_resume=tex,
    )
    init_database(settings)
    session = session_factory(build_engine(settings))()
    job = record_job_sighting(
        session,
        JobSighting(
            title="Data Intern",
            company="Northwind",
            url="https://jobs.example/northwind/data",
            ats_type="lever",
            external_id="nw-1",
        ),
    )
    application = create_application(session, job)
    session.commit()
    return settings, session, tex, application


def test_application_resume_is_an_exact_copy_and_master_is_unchanged(tmp_path, monkeypatch) -> None:
    settings, session, tex, application = _world(tmp_path, monkeypatch, VALID)
    before = tex.read_bytes()
    stamp = tex.stat().st_mtime_ns
    try:
        report = prepare_application_resume(session, settings, application.id)
        tex_path = Path(report.tex_path)
        pdf_path = Path(report.pdf_path)
        assert tex_path == settings.data_dir / "applications" / str(application.id) / "resume.tex"
        assert pdf_path == tex_path.with_suffix(".pdf")
        assert tex_path.read_bytes() == before
        assert pdf_path.read_bytes().startswith(b"%PDF-")
        assert {path.name for path in tex_path.parent.iterdir()} == {"resume.tex", "resume.pdf"}
        assert tex.read_bytes() == before
        assert tex.stat().st_mtime_ns == stamp
        assert tex_path.resolve() != tex.resolve()

        stored = session.get(ResumeVersion, report.version_id)
        assert stored is not None
        assert stored.kind == ResumeVersionKind.tailored
        assert stored.application_id == application.id
        assert stored.sha256 == report.sha256
        assert stored.tex_path == str(tex_path)
        assert stored.pdf_path == str(pdf_path)
    finally:
        session.close()


def test_existing_application_resume_is_not_rewritten(tmp_path, monkeypatch) -> None:
    settings, session, _tex, application = _world(tmp_path, monkeypatch, VALID)
    try:
        first = prepare_application_resume(session, settings, application.id)
        tex_path = Path(first.tex_path)
        stamp = tex_path.stat().st_mtime_ns
        pdf_stamp = Path(first.pdf_path).stat().st_mtime_ns

        second = prepare_application_resume(session, settings, application.id)

        assert second.version_id == first.version_id
        assert second.sha256 == first.sha256
        assert tex_path.stat().st_mtime_ns == stamp
        assert Path(first.pdf_path).stat().st_mtime_ns == pdf_stamp
        assert session.query(ResumeVersion).filter(ResumeVersion.application_id == application.id).count() == 1
    finally:
        session.close()


def test_different_existing_copy_is_left_untouched(tmp_path, monkeypatch) -> None:
    settings, session, _tex, application = _world(tmp_path, monkeypatch, VALID)
    try:
        report = prepare_application_resume(session, settings, application.id)
        tex_path = Path(report.tex_path)
        tex_path.write_text("already tailored\n", encoding="utf-8")
        changed = tex_path.read_bytes()

        with pytest.raises(ApplicationResumeError, match="will not be replaced"):
            prepare_application_resume(session, settings, application.id)

        assert tex_path.read_bytes() == changed
    finally:
        session.close()


def test_failed_compile_leaves_no_application_files(tmp_path, monkeypatch) -> None:
    settings, session, tex, application = _world(tmp_path, monkeypatch, INVALID)
    before = tex.read_bytes()
    directory = settings.data_dir / "applications" / str(application.id)
    try:
        with pytest.raises(ApplicationResumeError, match="Undefined control sequence"):
            prepare_application_resume(session, settings, application.id)
        assert not directory.exists()
        assert tex.read_bytes() == before
    finally:
        session.close()
