from jobhunter.domain.identity import canonical_url, make_dedup_key, normalize_name


def test_canonical_url_drops_tracking_params_and_locale() -> None:
    url = "https://WWW.Example.com/en-us/jobs/123/?utm_source=gh&gh_src=abc&lever-source=x"
    assert canonical_url(url) == "https://example.com/jobs/123"


def test_dedup_key_prefers_requisition_then_url_then_title() -> None:
    requisition = make_dedup_key(
        company="Acme Inc.",
        requisition_id="R-10",
        apply_url="https://acme.example/jobs/1",
        title="Intern",
        term="Summer 2027",
    )
    by_url = make_dedup_key(
        company="Acme",
        requisition_id=None,
        apply_url="https://acme.example/jobs/1?utm_medium=email",
        title="Intern",
        term=None,
    )
    by_title = make_dedup_key(
        company="Acme LLC",
        requisition_id="  ",
        apply_url=None,
        title="Software Intern",
        term="Summer 2027",
    )

    assert requisition == "req:acme:r-10"
    assert by_url == "url:https://acme.example/jobs/1"
    assert by_title == "title:acme|software intern|summer 2027"
    assert normalize_name("Acme, Inc.") == "acme"
