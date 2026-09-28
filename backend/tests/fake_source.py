from collections.abc import Mapping
from typing import Any

from jobhunter.ingestion.ports import SourceIdentity
from jobhunter.ingestion.raw import RawJob


class FakeJobSource:
    """Test double. Returns messy records and never talks to a network."""

    def __init__(self) -> None:
        self.detail_calls: list[str] = []

    def identify(self) -> SourceIdentity:
        return SourceIdentity(
            key="fake",
            ats_type="fake",
            board_key="fixture",
            label="Fake fixture",
            company="Northwind Labs",
        )

    def discover(self) -> list[dict[str, Any]]:
        return [
            {
                "external_id": "nw-1",
                "title": "  Software Engineer Intern  ",
                "url": "https://jobs.northwind.example/interns/1?utm_source=fake",
                "company": "Northwind Labs, Inc.",
                "locations": ["McLean, VA", "Austin, TX"],
                "description": "<p>Build backend services.</p>",
                "requisition_id": "R-100",
                "term": "summer 2027",
                "raw": {"id": "nw-1", "title": "  Software Engineer Intern  "},
            },
            {
                "external_id": "nw-1b",
                "title": "SWE Internship",
                "url": "https://boards.example/northwind/R-100",
                "company": "Northwind Labs LLC",
                "locations": ["Remote - United States"],
                "requisition_id": "r-100",
                "raw": {"id": "nw-1b"},
            },
            {
                "external_id": "nw-2",
                "title": "Intern",
                "url": "https://jobs.northwind.example/interns/2",
                "raw": {"id": "nw-2"},
            },
            {
                "external_id": "nw-3",
                "title": "Software Engineering Internship",
                "company": "Northwind Labs",
                "url": "https://jobs.northwind.example/interns/3",
                "locations": ["Washington, DC", "London, UK", "Remote, United States"],
                "description": "Summer 2027 internship on the data platform.",
                "raw": {"id": "nw-3"},
            },
            {
                "external_id": "nw-3-dup",
                "title": "software   engineering internship",
                "url": "https://WWW.jobs.northwind.example/en-us/interns/3/?utm_medium=email",
                "company": "Northwind Labs LLC",
                "raw": {"id": "nw-3-dup"},
            },
            {
                "external_id": "nw-bad",
                "title": "Broken listing",
                "company": "Northwind Labs",
            },
        ]

    def fetch_detail(self, job: RawJob) -> Mapping[str, Any] | None:
        self.detail_calls.append(job.external_id)
        if job.external_id != "nw-2":
            return None
        data = job.model_dump()
        data["description"] = "General internship program."
        data["raw"] = {**job.raw, "detail": True}
        return data
