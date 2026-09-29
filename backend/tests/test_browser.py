import json
import os
from pathlib import Path

import pytest

from jobhunter.browser.errors import NavigationTimeout, SubmitRefused, UnexpectedPage
from jobhunter.browser.manager import BrowserManager
from tests.browser_fixture import FormServer

pytest.importorskip("playwright")


def _needs_display() -> None:
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("visible Chromium needs a display")


def chromium_commands(profile_dir: Path) -> list[str]:
    marker = str(profile_dir)
    found: list[str] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
        except OSError:
            continue
        if marker in raw and "chrom" in raw.lower():
            found.append(raw)
    return found


@pytest.fixture(scope="module")
def site():
    server = FormServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def browser(tmp_path_factory, site):
    _needs_display()
    manager = BrowserManager(profile_dir=tmp_path_factory.mktemp("browser-profile"))
    manager.open()
    yield manager
    manager.close()


def test_chromium_is_visible_and_the_profile_is_private(browser) -> None:
    commands = chromium_commands(browser.profile_dir)
    assert commands
    assert all("--headless" not in command for command in commands)
    assert browser.profile_dir.stat().st_mode & 0o777 == 0o700
    with pytest.raises(SubmitRefused):
        browser.submit("#submit", "not an authorization")


def test_form_controls_are_filled_without_submitting(browser, site, tmp_path) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text("Ada Lovelace\n", encoding="utf-8")
    browser.navigate(site.demo, expect="demo.html")

    browser.fill("#full-name", "Ada Lovelace")
    browser.fill("#email", "ada@example.com")
    browser.fill("#notes", "Interested in the summer internship.")
    browser.select("#term", "summer")
    browser.click("#work-auth")
    browser.click("#workplace-onsite")
    browser.upload("#resume", resume)
    browser.click("#draft")

    page = browser.read_structure()
    assert page.title == "Job Hunter form demo"
    assert page.headings == ("Application form demo",)
    assert page.control("#full-name").value == "Ada Lovelace"
    assert page.control("#full-name").label == "Full name"
    assert page.control("#email").value == "ada@example.com"
    assert page.control("#notes").value == "Interested in the summer internship."
    assert page.control("#term").value == "summer"
    assert page.control("#term").options == ("", "summer", "fall")
    assert page.control("#work-auth").checked is True
    assert page.control("#workplace-onsite").checked is True
    assert page.control("#workplace-remote").checked is False
    assert page.control("#resume").value == "resume.txt"
    assert "draft saved" in page.notices
    assert "submitted" not in page.notices


def test_screenshot_and_action_log_record_the_session(browser, site, tmp_path) -> None:
    browser.navigate(site.demo, expect="demo.html")
    image = browser.screenshot(tmp_path / "form.png")

    assert image.read_bytes().startswith(b"\x89PNG")
    logged = [json.loads(line) for line in browser.record_path.read_text(encoding="utf-8").splitlines()]
    assert [row["action"] for row in logged if row["ok"]][:1] == ["open"]
    assert any(row["action"] == "navigate" and row["ok"] and "demo.html" in row["target"] for row in logged)
    assert any(row["action"] == "screenshot" and row["ok"] for row in logged)


def test_a_submit_control_is_refused(browser, site) -> None:
    browser.navigate(site.demo, expect="demo.html")
    with pytest.raises(SubmitRefused, match="refusing to activate"):
        browser.click("#submit")
    page = browser.read_structure()
    assert "submitted" not in page.notices
    assert browser.actions[-2].ok is False
    assert browser.actions[-2].action == "click"


def test_an_unexpected_page_is_rejected(browser, site) -> None:
    browser.navigate(site.demo, expect="demo.html")
    with pytest.raises(UnexpectedPage, match="elsewhere.html"):
        browser.navigate(site.elsewhere, expect="demo.html")
    with pytest.raises(UnexpectedPage, match="after activating #leave"):
        browser.navigate(site.demo, expect="demo.html")
        browser.click("#leave")


def test_navigation_timeout_is_reported(browser, site) -> None:
    with pytest.raises(NavigationTimeout, match="timed out after 700 ms"):
        browser.navigate(site.hang, timeout_ms=700)
    assert browser.actions[-1].ok is False
    assert browser.actions[-1].action == "navigate"


def test_the_profile_survives_closing_the_window(browser, tmp_path, site) -> None:
    _needs_display()
    browser.close()
    profile = tmp_path / "kept-profile"
    first = BrowserManager(profile_dir=profile)
    first.open()
    try:
        first.navigate(site.demo, expect="demo.html")
        first.click("#remember")
        assert "kept" in first.read_structure().notices
    finally:
        first.close()

    second = BrowserManager(profile_dir=profile)
    second.open()
    try:
        second.navigate(site.demo, expect="demo.html")
        assert "kept" in second.read_structure().notices
    finally:
        second.close()
