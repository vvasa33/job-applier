from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from jobhunter.db.models import Resume, ResumeVersion
from jobhunter.db.records import (
    JobSighting,
    add_master_resume,
    add_resume_version,
    create_application,
    record_job_sighting,
)
from jobhunter.domain.enums import ResumeVersionKind
from jobhunter.domain.errors import ResumeVersionError

MASTER_PATH = "/home/vvasa/Projects/funstuff/job-applier/Vasa_Resume.pdf"
TAILORED_TEX = "/tmp/jobhunter/applications/1/resume.tex"
TAILORED_PDF = "/tmp/jobhunter/applications/1/resume.pdf"


def test_tailored_resume_version_belongs_to_one_application(db_session) -> None:
    master = add_master_resume(db_session, path=MASTER_PATH, sha256="a" * 64)
    job = record_job_sighting(
        db_session,
        JobSighting(
            title="Data Intern",
            company="Contoso",
            url="https://jobs.contoso.example/9",
            ats_type="ashby",
            external_id="ash-9",
        ),
    )
    application = create_application(db_session, job)
    version = add_resume_version(
        db_session,
        resume=master,
        kind=ResumeVersionKind.tailored,
        application=application,
        sha256="b" * 64,
        tex_path=TAILORED_TEX,
        pdf_path=TAILORED_PDF,
    )
    db_session.commit()

    stored = db_session.get(ResumeVersion, version.id)
    assert stored is not None
    assert stored.application_id == application.id
    assert stored.resume_id == master.id
    assert stored.kind == ResumeVersionKind.tailored
    assert stored.tex_path == TAILORED_TEX
    assert stored.pdf_path == TAILORED_PDF
    assert master.path == MASTER_PATH
    assert master.is_current is True
    assert stored.tex_path != master.path


def test_master_snapshot_is_not_an_application_resume(db_session) -> None:
    master = add_master_resume(db_session, path=MASTER_PATH, sha256="c" * 64)
    snapshot = add_resume_version(
        db_session,
        resume=master,
        kind=ResumeVersionKind.master_snapshot,
        sha256="c" * 64,
        tex_path=MASTER_PATH,
    )
    db_session.commit()

    assert snapshot.application_id is None
    assert snapshot.kind == ResumeVersionKind.master_snapshot
    assert isinstance(db_session.get(Resume, master.id), Resume)


def test_tailored_version_without_an_application_is_rejected(db_session) -> None:
    master = add_master_resume(db_session, path=MASTER_PATH, sha256="d" * 64)
    try:
        add_resume_version(
            db_session,
            resume=master,
            kind=ResumeVersionKind.tailored,
            sha256="e" * 64,
            tex_path=TAILORED_TEX,
        )
        raised = False
    except ResumeVersionError:
        raised = True
    assert raised

    db_session.add(
        ResumeVersion(
            resume_id=master.id,
            application_id=None,
            kind=ResumeVersionKind.tailored,
            tex_path=TAILORED_TEX,
            sha256="f" * 64,
        )
    )
    try:
        db_session.flush()
        constrained = False
    except IntegrityError:
        db_session.rollback()
        constrained = True
    assert constrained


def test_only_one_resume_is_current(db_session) -> None:
    add_master_resume(db_session, path=MASTER_PATH, sha256="1" * 64)
    replacement = add_master_resume(db_session, path=MASTER_PATH, sha256="2" * 64)
    db_session.commit()

    current = db_session.scalars(select(Resume).where(Resume.is_current.is_(True))).all()
    assert current == [replacement]
