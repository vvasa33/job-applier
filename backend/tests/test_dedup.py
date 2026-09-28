from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from jobhunter.db.models import Job, JobSource
from jobhunter.db.records import JobSighting, create_application, record_job_sighting
from jobhunter.dedup.service import DedupService
from jobhunter.domain.dedup import (
    DedupSignal,
    JobProfile,
    SourceRef,
    choose_preferred_url,
    match_jobs,
)

_DESC = (
    "Build backend services for the internship program. "
    "You will write Python, review pull requests, and work with a mentor on production systems. "
    "The team ships weekly and documents decisions in writing."
)
_DESC_VARIANT = _DESC.replace("weekly", "each week")
_DATA_DESC = (
    "Analyze product data for the internship program. "
    "You will write SQL, review dashboards, and work with a mentor on production metrics. "
    "The team ships weekly and documents decisions in writing."
)


def _source(external_id: str = "gh-1", **overrides) -> SourceRef:
    fields = {
        "ats_type": "greenhouse",
        "board_key": "acme",
        "external_id": external_id,
        "url": f"https://boards.greenhouse.io/acme/jobs/{external_id}",
    }
    fields.update(overrides)
    return SourceRef(**fields)


def _job(title: str = "Software Engineering Intern", **overrides) -> JobProfile:
    fields = {
        "company": "Acme Inc.",
        "title": title,
        "locations": ("McLean, VA",),
        "description_text": _DESC,
        "sources": (_source(),),
    }
    fields.update(overrides)
    return JobProfile(**fields)


def test_same_source_and_external_id_wins_over_a_different_title() -> None:
    match = match_jobs(
        _job(sources=(_source("R-10"),)),
        _job(title="Totally Different Intern", locations=("Austin, Texas",), sources=(_source("r-10"),)),
    )
    assert match is not None
    assert match.signal is DedupSignal.source_external_id


def test_external_id_on_another_board_is_not_the_same_source() -> None:
    assert (
        match_jobs(
            _job(
                company="Acme",
                description_text=None,
                sources=(_source("R-10", url="https://acme.example/jobs/1"),),
            ),
            _job(
                company="Other Labs",
                title="Data Intern",
                locations=("Austin, Texas",),
                description_text=None,
                sources=(_source("R-10", board_key="other", url="https://other.example/jobs/1"),),
            ),
        )
        is None
    )


@pytest.mark.parametrize(
    ("left_url", "right_url"),
    [
        ("https://WWW.example.com/en-us/jobs/1?utm_source=newsletter", "https://example.com/jobs/1"),
        ("https://boards.greenhouse.io/acme/jobs/5?gh_src=abc", "https://boards.greenhouse.io/acme/jobs/5"),
        (
            "https://jobs.lever.co/acme/abc/apply?lever-source=linked",
            "https://jobs.lever.co/acme/abc/apply",
        ),
    ],
)
def test_canonical_url_ignores_tracking_locale_and_host_case(left_url: str, right_url: str) -> None:
    match = match_jobs(
        _job(apply_url=left_url, sources=(), locations=()),
        _job(apply_url=right_url, sources=(), locations=(), title="Something Else"),
    )
    assert match is not None
    assert match.signal is DedupSignal.canonical_url


def test_different_greenhouse_job_ids_stay_apart() -> None:
    assert (
        match_jobs(
            _job(apply_url="https://stripe.com/jobs/search?gh_jid=1", sources=(), locations=(), description_text=None),
            _job(apply_url="https://stripe.com/jobs/search?gh_jid=2", sources=(), locations=(), description_text=None),
        )
        is None
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            _job(company="Acme Inc.", title="Software Engineering Intern", locations=("McLean, VA",)),
            _job(company="Acme LLC", title="SWE Internship - Summer 2027", locations=("McLean, Virginia",)),
        ),
        (
            _job(title="Hardware Co-op", locations=("Remote - United States",)),
            _job(title="Hardware Coop", locations=("Remote",)),
        ),
        (
            _job(title="Software Engineering Intern", locations=("Hybrid - Arlington, VA",)),
            _job(title="Software Engineer Intern", locations=("Arlington, Virginia",)),
        ),
    ],
)
def test_company_title_and_location_collapse_wording_differences(left: JobProfile, right: JobProfile) -> None:
    left = JobProfile(
        company=left.company,
        title=left.title,
        locations=left.locations,
        description_text=None,
        sources=(_source("left", url="https://example.com/left"),),
    )
    right = JobProfile(
        company=right.company,
        title=right.title,
        locations=right.locations,
        description_text=None,
        sources=(_source("right", url="https://example.com/right"),),
    )
    match = match_jobs(left, right)
    assert match is not None
    assert match.signal is DedupSignal.title_location


def test_description_hash_matches_html_and_a_missing_location() -> None:
    html_desc = f"<p>{_DESC}</p>"
    match = match_jobs(
        _job(description_text=html_desc, locations=(), sources=(_source("a", url="https://example.com/a"),)),
        _job(
            description_text="  " + _DESC.upper() + "  ",
            locations=("Austin, Texas",),
            sources=(_source("b", url="https://example.com/b"),),
        ),
    )
    assert match is not None
    assert match.signal is DedupSignal.content_hash


def test_typo_in_the_title_uses_local_similarity_not_an_exact_key() -> None:
    match = match_jobs(
        _job(title="Sofware Engineering Intern", description_text=None, sources=(_source("a", url="https://example.com/a"),)),
        _job(title="Software Engineering Intern", description_text=None, sources=(_source("b", url="https://example.com/b"),)),
    )
    assert match is not None
    assert match.signal is DedupSignal.semantic


def test_near_titles_with_similar_descriptions_match_semantically() -> None:
    match = match_jobs(
        _job(title="Platform Intern", description_text=_DESC, sources=(_source("a", url="https://example.com/a"),)),
        _job(
            title="Platform Engineering Intern",
            description_text=_DESC_VARIANT,
            sources=(_source("b", url="https://example.com/b"),),
        ),
    )
    assert match is not None
    assert match.signal is DedupSignal.semantic


def test_state_only_location_matches_a_city_on_the_description_hash() -> None:
    match = match_jobs(
        _job(locations=("Virginia",), sources=(_source("a", url="https://example.com/a"),)),
        _job(locations=("McLean, VA",), sources=(_source("b", url="https://example.com/b"),)),
    )
    assert match is not None
    assert match.signal is DedupSignal.content_hash


@pytest.mark.parametrize(
    "other",
    [
        _job(company="Other Labs", sources=(_source("x", board_key="other", url="https://other.example/1"),)),
        _job(locations=("Austin, Texas",), sources=(_source("x", url="https://example.com/austin"),)),
        _job(locations=("Remote - Canada",), sources=(_source("x", url="https://example.com/ca"),), title="Software Engineering Intern"),
        _job(
            title="Data Engineering Intern",
            description_text=_DESC,
            sources=(_source("x", url="https://example.com/data"),),
        ),
        _job(
            title="Software Engineer",
            description_text=_DESC,
            sources=(_source("x", url="https://example.com/fulltime"),),
        ),
        _job(
            title="Internal Tools Engineer",
            description_text=None,
            locations=("McLean, VA",),
            sources=(_source("x", url="https://example.com/internal"),),
        ),
        _job(
            description_text="Apply now",
            locations=(),
            sources=(_source("x", url="https://example.com/short"),),
            title="Research Intern",
        ),
        _job(
            apply_url="https://other.example/jobs/1",
            sources=(),
            locations=(),
            description_text=None,
            title="Unrelated",
            company="Unrelated",
        ),
    ],
)
def test_lookalike_postings_are_not_merged(other: JobProfile) -> None:
    assert match_jobs(_job(sources=(_source("keep", url="https://example.com/keep"),)), other) is None


def test_same_boilerplate_description_does_not_merge_different_roles() -> None:
    assert (
        match_jobs(
            _job(title="Software Engineering Intern", description_text=_DESC, sources=(_source("a", url="https://example.com/a"),)),
            _job(title="Data Engineering Intern", description_text=_DESC, sources=(_source("b", url="https://example.com/b"),)),
        )
        is None
    )


def test_different_descriptions_do_not_make_near_titles_a_match() -> None:
    assert (
        match_jobs(
            _job(title="Platform Intern", description_text=_DESC, sources=(_source("a", url="https://example.com/a"),)),
            _job(title="Platform Engineering Intern", description_text=_DATA_DESC, sources=(_source("b", url="https://example.com/b"),)),
        )
        is None
    )


def test_same_title_in_two_cities_is_not_a_duplicate() -> None:
    assert (
        match_jobs(
            _job(locations=("McLean, VA",), description_text=None, sources=(_source("a", url="https://example.com/a"),)),
            _job(locations=("Austin, Texas",), description_text=None, sources=(_source("b", url="https://example.com/b"),)),
        )
        is None
    )


def test_remote_united_states_does_not_match_remote_canada() -> None:
    assert (
        match_jobs(
            _job(locations=("Remote - United States",), description_text=None, sources=(_source("a", url="https://example.com/a"),)),
            _job(locations=("Remote - Canada",), description_text=None, sources=(_source("b", url="https://example.com/b"),)),
        )
        is None
    )


def test_placeholder_location_text_is_ignored() -> None:
    assert (
        match_jobs(
            _job(locations=("3 Locations",), description_text=None, sources=(_source("a", url="https://example.com/a"),)),
            _job(locations=("Austin, Texas",), description_text=None, sources=(_source("b", url="https://example.com/b"),)),
        )
        is None
    )


def test_preferred_url_is_the_earliest_sighting_then_https_then_source_identity() -> None:
    early = datetime(2026, 1, 1, tzinfo=timezone.utc)
    late = datetime(2026, 6, 1, tzinfo=timezone.utc)
    chosen = choose_preferred_url(
        (
            SourceRef("lever", "acme", "b", "http://jobs.lever.co/acme/b/apply", early),
            SourceRef("greenhouse", "acme", "a", "https://boards.greenhouse.io/acme/jobs/a", late),
            SourceRef("workday", "acme", "c", "https://acme.wd5.myworkdayjobs.com/job/c", early),
        )
    )
    assert chosen == "https://acme.wd5.myworkdayjobs.com/job/c"

    tied = choose_preferred_url(
        (
            SourceRef("workday", "acme", "c", "https://acme.example/c", early),
            SourceRef("greenhouse", "acme", "a", "https://acme.example/a", early),
        )
    )
    assert tied == "https://acme.example/a"


def _store(session, *, url: str, external_id: str, ats_type: str = "greenhouse", **fields) -> Job:
    job = record_job_sighting(
        session,
        JobSighting(
            title=fields.pop("title", "Software Engineering Intern"),
            company=fields.pop("company", "Acme Inc."),
            url=url,
            ats_type=ats_type,
            external_id=external_id,
            board_key=fields.pop("board_key", "acme"),
        ),
    )
    for name, value in fields.items():
        setattr(job, name, value)
    session.flush()
    return job


def test_merge_keeps_every_source_and_picks_one_url(db_session) -> None:
    early = datetime(2026, 1, 2, tzinfo=timezone.utc)
    late = datetime(2026, 2, 2, tzinfo=timezone.utc)
    first = _store(
        db_session,
        url="https://boards.greenhouse.io/acme/jobs/1",
        external_id="gh-1",
        locations=["McLean, VA"],
        description_text=None,
    )
    second = _store(
        db_session,
        url="https://jobs.lever.co/acme/abc/apply",
        external_id="abc",
        ats_type="lever",
        locations=["McLean, Virginia"],
        description_text=_DESC,
    )
    first.sources[0].first_seen_at = late
    second.sources[0].first_seen_at = early
    db_session.commit()

    report = DedupService().deduplicate(db_session)
    db_session.commit()

    assert report.blocked == ()
    assert len(report.merges) == 1
    assert report.merges[0].signal is DedupSignal.title_location
    assert report.merges[0].apply_url == "https://jobs.lever.co/acme/abc/apply"
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1
    keeper = db_session.get(Job, report.merges[0].keeper_id)
    assert keeper is not None
    assert {source.url for source in keeper.sources} == {
        "https://boards.greenhouse.io/acme/jobs/1",
        "https://jobs.lever.co/acme/abc/apply",
    }
    assert {source.ats_type for source in keeper.sources} == {"greenhouse", "lever"}
    assert keeper.apply_url == "https://jobs.lever.co/acme/abc/apply"
    assert keeper.description_text == _DESC
    assert "McLean, VA" in keeper.locations and "McLean, Virginia" in keeper.locations


def test_split_cities_merge_only_through_a_combined_posting(db_session) -> None:
    mclean = _store(db_session, url="https://example.com/mclean", external_id="mclean", locations=["McLean, VA"], description_text=None)
    austin = _store(db_session, url="https://example.com/austin", external_id="austin", locations=["Austin, Texas"], description_text=None)
    db_session.commit()
    assert DedupService().deduplicate(db_session).merges == ()

    _store(
        db_session,
        url="https://example.com/both",
        external_id="both",
        locations=["McLean, VA", "Austin, Texas"],
        description_text=None,
    )
    db_session.commit()
    report = DedupService().deduplicate(db_session)
    assert len(report.merges) == 1
    keeper = db_session.get(Job, report.merges[0].keeper_id)
    assert keeper is not None
    assert {source.external_id for source in keeper.sources} == {"mclean", "austin", "both"}
    assert mclean.id == keeper.id or austin.id == keeper.id


def test_semantic_matches_do_not_chain(db_session) -> None:
    _store(
        db_session,
        url="https://example.com/platform",
        external_id="platform",
        title="Platform Intern",
        description_text=_DESC,
        locations=["McLean, VA"],
    )
    _store(
        db_session,
        url="https://example.com/platform-eng",
        external_id="platform-eng",
        title="Platform Engineering Intern",
        description_text=_DESC_VARIANT,
        locations=["McLean, VA"],
    )
    _store(
        db_session,
        url="https://example.com/engineering",
        external_id="engineering",
        title="Engineering Intern",
        description_text=_DESC_VARIANT.replace("mentor", "advisor"),
        locations=["McLean, VA"],
    )
    db_session.commit()

    report = DedupService().deduplicate(db_session)
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(Job)) == 2
    assert len(report.merges) == 1
    assert report.merges[0].signal is DedupSignal.semantic
    remaining = {job.title for job in db_session.scalars(select(Job))}
    assert "Engineering Intern" in remaining


def test_job_with_an_application_is_the_keeper(db_session) -> None:
    bare = _store(db_session, url="https://example.com/bare", external_id="bare", locations=["Remote - United States"], description_text=None)
    applied = _store(db_session, url="https://example.com/applied", external_id="applied", locations=["Remote"], description_text=None)
    create_application(db_session, applied)
    db_session.commit()

    report = DedupService().deduplicate(db_session)
    db_session.commit()

    assert report.merges[0].keeper_id == applied.id
    assert bare.id in report.merges[0].merged_ids
    keeper = db_session.get(Job, applied.id)
    assert keeper is not None
    assert keeper.application is not None
    assert db_session.scalar(select(func.count()).select_from(JobSource)) == 2


def test_two_applications_block_the_merge_and_record_the_possible_duplicate(db_session) -> None:
    first = _store(db_session, url="https://example.com/one", external_id="one", locations=["McLean, VA"], description_text=None)
    second = _store(db_session, url="https://example.com/two", external_id="two", locations=["McLean, Virginia"], description_text=None)
    create_application(db_session, first)
    create_application(db_session, second)
    db_session.commit()

    report = DedupService().deduplicate(db_session)
    db_session.commit()

    assert report.merges == ()
    assert report.blocked[0].job_ids == (first.id, second.id)
    assert db_session.scalar(select(func.count()).select_from(Job)) == 2
    assert db_session.get(Job, second.id).possible_duplicate_of == first.id
    assert {source.job_id for source in db_session.scalars(select(JobSource))} == {first.id, second.id}
