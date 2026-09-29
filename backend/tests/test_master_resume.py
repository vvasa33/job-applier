import os
import stat
from pathlib import Path

from fastapi.testclient import TestClient

from jobhunter.api.app import create_app
from jobhunter.config import Settings
from jobhunter.resume.parser import parse_resume

VALID = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\resumeSubheading{Northwind}{2025}{Intern}{Remote}
\begin{itemize}
\item Built a data pipeline
\end{itemize}
\section{Education}
\item B.S. Computer Science
\section{Projects}
\item Local job hunter
\section*{Skills}
\item Python, SQL
\end{document}
"""

INVALID = r"""
\documentclass{article}
\begin{document}
\notacommand
\end{document}
"""


def _settings(tmp_path: Path, tex: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8765,
        data_dir=tmp_path / "data",
        log_level="WARNING",
        master_resume=tex,
    )


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


def test_parser_finds_resume_sections() -> None:
    parsed = parse_resume(VALID)
    kinds = [section.kind for section in parsed.sections]
    assert kinds == ["experience", "education", "projects", "skills"]
    assert "Northwind, Intern, 2025, Remote" in parsed.sections[0].entries
    assert "Built a data pipeline" in parsed.sections[0].entries


def test_master_resume_is_never_modified(tmp_path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _pdflatex(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    tex = tmp_path / "master.tex"
    tex.write_text(VALID, encoding="utf-8")
    before = tex.read_bytes()
    stamp = tex.stat().st_mtime_ns

    client = TestClient(create_app(_settings(tmp_path, tex)))
    validated = client.post("/api/resume/validate")
    compiled = client.post("/api/resume/compile")

    assert validated.status_code == 200
    assert compiled.status_code == 200
    assert tex.read_bytes() == before
    assert tex.stat().st_mtime_ns == stamp
    assert Path(compiled.json()["pdf_path"]).is_file()
    assert Path(compiled.json()["pdf_path"]).resolve() != tex.resolve()
    assert validated.json()["sections"][0]["kind"] == "experience"


def test_invalid_latex_is_reported(tmp_path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _pdflatex(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    tex = tmp_path / "broken.tex"
    tex.write_text(INVALID, encoding="utf-8")
    before = tex.read_bytes()

    client = TestClient(create_app(_settings(tmp_path, tex)))
    response = client.post("/api/resume/compile")

    assert response.status_code == 400
    assert "Undefined control sequence" in response.json()["detail"]
    assert tex.read_bytes() == before


def test_missing_master_resume_is_reported(tmp_path) -> None:
    missing = tmp_path / "missing.tex"
    client = TestClient(create_app(_settings(tmp_path, missing)))
    response = client.post("/api/resume/validate")
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]
