import json
import os
import stat
from pathlib import Path

import pytest
from sqlalchemy import select

from jobhunter.config import Settings
from jobhunter.db.migrate import init_database
from jobhunter.db.models import JobRequirement, ResumeVersion
from jobhunter.db.records import JobSighting, create_application, record_job_sighting
from jobhunter.db.session import build_engine, session_factory
from jobhunter.domain.enums import CsRelevance, LocationClass, RequirementKind, ResumeVersionKind
from jobhunter.llm.client import LLMClient
from jobhunter.llm.errors import LLMError
from jobhunter.llm.provider import FakeProvider
from jobhunter.llm.schemas import TailoredBullet, TailoringPlan
from jobhunter.resume.application import ApplicationResumeError
from jobhunter.resume.evidence import skills_vocabulary, trace_problems
from jobhunter.resume.spans import locate
from jobhunter.resume.tailoring import LLMTailoringPlanner, apply_plan, tailor_application_resume

MASTER = r"""\documentclass{article}
\newcommand{\resumeItem}[1]{\item\small{#1}}
\newcommand{\resumeSubheading}[4]{\item \textbf{#1} #2 \\ #3 #4}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}}
\begin{document}
Vasa Example \\ vasa@example.com
\section{Experience}
\begin{itemize}
  \resumeSubheading{Northwind}{June 2025 -- Aug 2025}{Software Engineering Intern}{Remote}
  \resumeItemListStart
    \resumeItem{Built a Python data pipeline that cut report time by 30\%}
    \resumeItem{Wrote SQL queries for weekly dashboards}
    \resumeItem{Fixed flaky tests in the CI suite}
  \resumeItemListEnd
  \resumeSubheading{Contoso}{Jan 2024 -- May 2024}{Research Assistant}{College Park, MD}
  \resumeItemListStart
    \resumeItem{Trained PyTorch models on campus GPUs}
  \resumeItemListEnd
\end{itemize}
\section{Education}
\begin{itemize}
  \item B.S. Computer Science, University of Maryland, 2027 % expected
\end{itemize}
\section{Skills}
\begin{itemize}
  \item Python, SQL, PyTorch, Linux
\end{itemize}
\end{document}
"""


class FakePlanner:
    def __init__(self, plan: TailoringPlan | None = None, error: Exception | None = None) -> None:
        self.plan_value = plan
        self.error = error
        self.calls: list[dict] = []

    def plan(self, job, entries, requirements, evidence) -> TailoringPlan:
        self.calls.append({"job": job, "entries": entries, "requirements": requirements, "evidence": evidence})
        if self.error is not None:
            raise self.error
        return self.plan_value


def _plan(*bullets: tuple[str, list[str]]) -> TailoringPlan:
    return TailoringPlan(
        bullets=[TailoredBullet(text=text, source_ids=ids) for text, ids in bullets],
        explanation="Emphasize data work.",
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


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _pdflatex(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    def build(source: str = MASTER):
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
                title="Data Engineering Intern",
                company="Initech",
                url="https://jobs.example/initech/data",
                ats_type="greenhouse",
                external_id="it-1",
                description_text="Build Python and SQL pipelines. Rust is a plus.",
                locations=("Arlington, VA",),
                location_class=LocationClass.dmv,
                is_internship=True,
                cs_relevance=CsRelevance.relevant,
            ),
        )
        session.add(JobRequirement(job_id=job.id, kind=RequirementKind.other, value="Python and SQL"))
        application = create_application(session, job)
        session.commit()
        return settings, session, tex, application

    return build


def test_tailored_resume_applies_traceable_edits_and_leaves_master_untouched(world) -> None:
    settings, session, tex, application = world()
    before = tex.read_bytes()
    stamp = tex.stat().st_mtime_ns
    planner = FakePlanner(
        _plan(
            ("Wrote SQL queries for weekly dashboards", ["b2"]),
            ("Developed a Python data pipeline that reduced report time by 30%", ["b1"]),
            ("Trained PyTorch models on campus GPUs", ["b4"]),
            ("Python, SQL, Linux, PyTorch", ["b6"]),
        )
    )
    try:
        report = tailor_application_resume(session, settings, application.id, planner)
        directory = Path(report.directory)
        generated = Path(report.tex_path).read_text(encoding="utf-8")

        assert tex.read_bytes() == before
        assert tex.stat().st_mtime_ns == stamp
        assert directory == settings.data_dir.resolve() / "applications" / str(application.id)
        assert sorted(path.name for path in directory.iterdir()) == [
            "change_report.md",
            "evidence.json",
            "resume.json",
            "resume.pdf",
            "resume.tex",
        ]
        assert Path(report.pdf_path).read_bytes().startswith(b"%PDF-")

        assert r"\resumeItem{Developed a Python data pipeline that reduced report time by 30\%}" in generated
        assert "% evidence: b1\n" in generated
        assert generated.index("Wrote SQL queries") < generated.index("Developed a Python")
        assert "Fixed flaky tests" not in generated
        assert r"\item Python, SQL, Linux, PyTorch" in generated
        for fixed in (
            r"\resumeSubheading{Northwind}{June 2025 -- Aug 2025}{Software Engineering Intern}{Remote}",
            r"\resumeSubheading{Contoso}{Jan 2024 -- May 2024}{Research Assistant}{College Park, MD}",
            r"\item B.S. Computer Science, University of Maryland, 2027 % expected",
            "Vasa Example \\\\ vasa@example.com",
        ):
            assert fixed in generated
        assert generated.split(r"\begin{document}")[0] == MASTER.split(r"\begin{document}")[0]

        evidence = json.loads(Path(report.evidence_path).read_text(encoding="utf-8"))["bullets"]
        assert all(entry["sources"] for entry in evidence)
        rewritten = next(entry for entry in evidence if entry["text"].startswith("Developed"))
        assert rewritten["change"] == "rewritten"
        assert rewritten["sources"] == [{"id": "b1", "text": "Built a Python data pipeline that cut report time by 30%"}]

        representation = json.loads(Path(report.representation_path).read_text(encoding="utf-8"))
        assert [section["kind"] for section in representation["sections"]] == ["experience", "education", "skills"]
        assert representation["sections"][0]["entries"][0]["heading"].startswith("Northwind")

        change_report = Path(report.change_report_path).read_text(encoding="utf-8")
        assert "Rewrote b1." in change_report
        assert "Removed b3: Fixed flaky tests in the CI suite" in change_report
        assert "Reordered the bullets for this entry." in change_report
        assert "not modified" in change_report
        assert report.rejected == 0

        version = session.get(ResumeVersion, report.version_id)
        assert version.kind is ResumeVersionKind.tailored
        assert version.application_id == application.id
        assert version.diff_path == report.change_report_path

        call = planner.calls[0]
        assert ("b1", "[Experience / Northwind, Software Engineering Intern, June 2025 - Aug 2025, Remote] "
                "Built a Python data pipeline that cut report time by 30%") in call["entries"]
        assert "other: Python and SQL" in call["requirements"]
        assert any("do not add them" in line and "rust" in line for line in call["evidence"])
    finally:
        session.close()


def test_combining_bullets_from_the_same_entry_is_allowed(world) -> None:
    settings, session, _, application = world()
    planner = FakePlanner(
        _plan(("Wrote SQL queries for weekly dashboards and fixed flaky CI tests", ["b2", "b3"]))
    )
    try:
        report = tailor_application_resume(session, settings, application.id, planner)
        generated = Path(report.tex_path).read_text(encoding="utf-8")
        assert "% evidence: b2, b3\n" in generated
        assert "Built a Python data pipeline" not in generated
        change_report = Path(report.change_report_path).read_text(encoding="utf-8")
        assert "Combined b2, b3." in change_report
        assert "Removed b1" in change_report
    finally:
        session.close()


HALLUCINATIONS = [
    pytest.param("Built a Python data pipeline that cut report time by 45%", ["b1"], "numbers or metrics", id="metric"),
    pytest.param("Built a Python data pipeline at Google that cut report time by 30%", ["b1"], "names", id="employer"),
    pytest.param("Built a Python data pipeline in 2019 that cut report time by 30%", ["b1"], "numbers", id="date"),
    pytest.param("Built a Python data pipeline in March that cut report time by 30%", ["b1"], "dates", id="month"),
    pytest.param("Built a Rust data pipeline that cut report time by 30%", ["b1"], "skills", id="skill"),
    pytest.param("Led a team of five engineers building a Python data pipeline", ["b1"], "claims", id="leadership"),
    pytest.param(
        "Designed a distributed payments platform serving enterprise customers",
        ["b3"],
        "content",
        id="untraceable-experience",
    ),
    pytest.param("M.S. Computer Science, University of Maryland, 2027", ["b5"], "names", id="degree"),
    pytest.param("B.S. Computer Science, Stanford University, 2027", ["b5"], "names", id="school"),
    pytest.param("B.S. Computer Science, University of Maryland, 2027, GPA 4.0", ["b5"], "numbers", id="gpa"),
    pytest.param("Python, SQL, PyTorch, Linux, Kubernetes", ["b6"], "skills", id="skills-line"),
    pytest.param("Built a Python data pipeline", [], "no source evidence", id="no-evidence"),
    pytest.param("Built a Python data pipeline", ["b99"], "not in the master resume", id="unknown-evidence"),
    pytest.param("Built Python pipelines and trained PyTorch models", ["b1", "b4"], "different resume entries", id="cross-employer"),
    pytest.param(r"Built a \textbf{Python} data pipeline", ["b1"], "LaTeX", id="latex"),
]


@pytest.mark.parametrize(("text", "ids", "reason"), HALLUCINATIONS)
def test_hallucinated_bullets_are_rejected_and_master_text_is_kept(world, text, ids, reason) -> None:
    settings, session, tex, application = world()
    before = tex.read_bytes()
    try:
        report = tailor_application_resume(session, settings, application.id, FakePlanner(_plan((text, ids))))
        assert report.rejected == 1
        assert report.applied_changes == 0
        assert Path(report.tex_path).read_bytes() == before
        assert tex.read_bytes() == before
        change_report = Path(report.change_report_path).read_text(encoding="utf-8")
        assert "## Rejected suggestions" in change_report
        assert f'"{" ".join(text.split())}"' in change_report
        assert reason in change_report
        evidence = json.loads(Path(report.evidence_path).read_text(encoding="utf-8"))["bullets"]
        assert all(entry["change"] == "unchanged" for entry in evidence)
        assert text not in {entry["text"] for entry in evidence}
    finally:
        session.close()


def test_one_rejected_bullet_keeps_its_entry_but_other_entries_still_change(world) -> None:
    settings, session, _, application = world()
    planner = FakePlanner(
        _plan(
            ("Built a Python data pipeline that cut report time by 90%", ["b1"]),
            ("Wrote SQL queries", ["b2"]),
            ("Trained PyTorch models on GPUs", ["b4"]),
        )
    )
    try:
        report = tailor_application_resume(session, settings, application.id, planner)
        generated = Path(report.tex_path).read_text(encoding="utf-8")
        assert r"\resumeItem{Built a Python data pipeline that cut report time by 30\%}" in generated
        assert r"\resumeItem{Wrote SQL queries for weekly dashboards}" in generated
        assert r"\resumeItem{Trained PyTorch models on GPUs}" in generated
        assert "90" not in generated
        change_report = Path(report.change_report_path).read_text(encoding="utf-8")
        assert "Kept the original bullets because a suggestion for this entry was rejected." in change_report
        assert report.rejected == 1
    finally:
        session.close()


def test_planner_failure_produces_an_exact_copy_with_a_report(world) -> None:
    settings, session, tex, application = world()
    try:
        report = tailor_application_resume(
            session, settings, application.id, FakePlanner(error=LLMError("resume_tailoring failed after 3 attempts"))
        )
        assert Path(report.tex_path).read_bytes() == tex.read_bytes()
        assert report.plan_error == "resume_tailoring failed after 3 attempts"
        assert "No plan was applied" in Path(report.change_report_path).read_text(encoding="utf-8")
    finally:
        session.close()


def test_existing_artifacts_are_never_replaced(world) -> None:
    settings, session, _, application = world()
    try:
        first = tailor_application_resume(
            session, settings, application.id, FakePlanner(_plan(("Wrote SQL queries", ["b2"])))
        )
        original = Path(first.tex_path).read_bytes()
        with pytest.raises(ApplicationResumeError, match="never replaced"):
            tailor_application_resume(session, settings, application.id, FakePlanner(_plan()))
        assert Path(first.tex_path).read_bytes() == original
    finally:
        session.close()


def test_compile_failure_leaves_no_artifacts_or_version(world) -> None:
    broken = MASTER.replace("Vasa Example", r"\notacommand Vasa Example")
    settings, session, tex, application = world(broken)
    try:
        with pytest.raises(ApplicationResumeError, match="Undefined control sequence"):
            tailor_application_resume(session, settings, application.id, FakePlanner(_plan(("Wrote SQL queries", ["b2"]))))
        applications = settings.data_dir / "applications"
        assert not applications.exists() or list(applications.iterdir()) == []
        assert session.scalar(select(ResumeVersion).where(ResumeVersion.application_id == application.id)) is None
        assert tex.read_text(encoding="utf-8") == broken
    finally:
        session.close()


def test_llm_planner_sends_rules_and_its_output_is_still_validated(world) -> None:
    settings, session, tex, application = world()
    provider = FakeProvider(
        [json.dumps({"bullets": [{"text": "Built a Python data pipeline at Meta", "source_ids": ["b1"]}]})]
    )
    client = LLMClient(provider, model="test-model", timeout_s=30, max_attempts=1, sleep=lambda _: None)
    try:
        report = tailor_application_resume(session, settings, application.id, LLMTailoringPlanner(client))
        assert "Never invent employers." in provider.requests[0].system
        assert report.rejected == 1
        assert Path(report.tex_path).read_bytes() == tex.read_bytes()
    finally:
        session.close()


def test_locate_finds_editable_bullets_only_in_the_document_body() -> None:
    document = locate(MASTER)
    assert list(document.bullets) == ["b1", "b2", "b3", "b4", "b5", "b6"]
    assert [block.bullet_ids for block in document.blocks] == [("b1", "b2", "b3"), ("b4",), ("b5",), ("b6",)]
    assert document.bullets["b5"].plain == "B.S. Computer Science, University of Maryland, 2027"
    assert document.bullets["b1"].heading.startswith("Northwind")
    for bullet in document.bullets.values():
        assert MASTER[bullet.start : bullet.end] == bullet.raw


def test_trace_problems_accepts_rewording_of_the_same_facts() -> None:
    vocabulary = skills_vocabulary(locate(MASTER))
    evidence = "Northwind Built a Python data pipeline that cut report time by 30%"
    assert trace_problems("Developed a Python data pipeline that reduced report time by 30%", evidence, vocabulary) == []
    assert trace_problems("Automated Python pipelines cutting report time by 30%", evidence, vocabulary) == []


def test_apply_plan_without_a_plan_is_the_master_source() -> None:
    document = locate(MASTER)
    assert apply_plan(document, None).tex == MASTER
