"""Plain-text views used in prompts. These helpers do not call a provider."""

from jobhunter.matching.profiles import JobProfile, ResumeProfile


def job_text(job: JobProfile) -> str:
    locations = ", ".join(job.locations) if job.locations else "unspecified"
    return (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Locations: {locations}\n"
        f"Description:\n{job.description_text or ''}"
    )


def resume_text(resume: ResumeProfile) -> str:
    return resume.text()
