"""Merge stored jobs that the dedup rules say are the same posting.

Source rows are reassigned, never deleted. Every apply URL stays on its source
row. The canonical job's apply URL is then chosen with `choose_preferred_url`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.db.models import Application, Job, JobSource
from jobhunter.domain.enums import RequirementKind
from jobhunter.domain.dedup import (
    DedupSignal,
    JobProfile,
    SourceRef,
    choose_preferred_url,
    content_hash,
    match_jobs,
    normalize_description,
    stronger_signal,
)
@dataclass(frozen=True)
class MergeResult:
    keeper_id: int
    merged_ids: tuple[int, ...]
    signal: DedupSignal
    apply_url: str
    source_ids: tuple[int, ...]


@dataclass(frozen=True)
class BlockedMerge:
    """Two jobs in the group each already have an application, so neither row can be removed."""

    job_ids: tuple[int, ...]
    signal: DedupSignal


@dataclass(frozen=True)
class DedupReport:
    merges: tuple[MergeResult, ...]
    blocked: tuple[BlockedMerge, ...]


class DedupService:
    def deduplicate(self, session: Session) -> DedupReport:
        jobs = list(session.scalars(select(Job).order_by(Job.id)))
        if len(jobs) < 2:
            return DedupReport(merges=(), blocked=())
        profiles = {job.id: _profile(job) for job in jobs}
        groups, signals = _clusters(profiles)
        merges: list[MergeResult] = []
        blocked: list[BlockedMerge] = []
        for member_ids, signal in zip(groups, signals, strict=True):
            if len(member_ids) < 2:
                continue
            result, held = _merge_group(session, member_ids, signal)
            if result is not None:
                merges.append(result)
            if held is not None:
                blocked.append(held)
        return DedupReport(merges=tuple(merges), blocked=tuple(blocked))


class _UnionFind:
    def __init__(self, ids: list[int]) -> None:
        self.parent = {job_id: job_id for job_id in ids}
        self.signal: dict[int, DedupSignal] = {}

    def find(self, job_id: int) -> int:
        parent = self.parent
        while parent[job_id] != job_id:
            parent[job_id] = parent[parent[job_id]]
            job_id = parent[job_id]
        return job_id

    def union(self, left: int, right: int, signal: DedupSignal) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            self.signal[left_root] = stronger_signal(self.signal.get(left_root), signal)
            return
        if left_root > right_root:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.signal[left_root] = stronger_signal(
            stronger_signal(self.signal.get(left_root), signal),
            self.signal.get(right_root) or signal,
        )
        self.signal.pop(right_root, None)


def _clusters(profiles: dict[int, JobProfile]) -> tuple[list[tuple[int, ...]], list[DedupSignal]]:
    ids = sorted(profiles)
    groups = _UnionFind(ids)
    semantic_pairs: list[tuple[int, int]] = []
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1 :]:
            decision = match_jobs(profiles[left_id], profiles[right_id])
            if decision is None:
                continue
            if decision.signal is DedupSignal.semantic:
                semantic_pairs.append((left_id, right_id))
            else:
                groups.union(left_id, right_id, decision.signal)
    linked: set[int] = set()
    for left_id, right_id in semantic_pairs:
        left_root = groups.find(left_id)
        right_root = groups.find(right_id)
        if left_root == right_root or left_root in linked or right_root in linked:
            continue
        groups.union(left_id, right_id, DedupSignal.semantic)
        linked.add(groups.find(left_id))
    members: dict[int, list[int]] = {}
    for job_id in ids:
        members.setdefault(groups.find(job_id), []).append(job_id)
    ordered = [tuple(sorted(group)) for group in members.values() if len(group) > 1]
    ordered.sort(key=lambda group: group[0])
    return ordered, [groups.signal[groups.find(group[0])] for group in ordered]


def _merge_group(
    session: Session,
    member_ids: tuple[int, ...],
    signal: DedupSignal,
) -> tuple[MergeResult | None, BlockedMerge | None]:
    jobs = [session.get(Job, job_id) for job_id in member_ids]
    present = [job for job in jobs if job is not None]
    if len(present) < 2:
        return None, None
    applied = set(
        session.scalars(select(Application.job_id).where(Application.job_id.in_([job.id for job in present])))
    )
    applied_jobs = [job for job in present if job.id in applied]
    blocked: BlockedMerge | None = None
    if len(applied_jobs) > 1:
        primary = min(applied_jobs, key=lambda job: job.id)
        for other in applied_jobs:
            if other.id != primary.id:
                other.possible_duplicate_of = primary.id
        blocked = BlockedMerge(job_ids=tuple(sorted(job.id for job in applied_jobs)), signal=signal)
        incoming = [job for job in present if job.id not in applied]
        if not incoming:
            session.flush()
            return None, blocked
        keeper = primary
    else:
        keeper = applied_jobs[0] if applied_jobs else min(present, key=lambda job: job.id)
        incoming = [job for job in present if job.id != keeper.id]
    merged_ids = tuple(sorted(job.id for job in incoming))
    for job in incoming:
        _move_children(session, job, keeper)
    _fill_keeper(keeper)
    session.flush()
    for job in incoming:
        _retarget_duplicate_pointers(session, job.id, keeper.id)
        session.delete(job)
    session.flush()
    sources = list(session.scalars(select(JobSource).where(JobSource.job_id == keeper.id).order_by(JobSource.id)))
    apply_url = choose_preferred_url(tuple(_source_ref(source) for source in sources))
    preferred = next(source for source in sources if source.url == apply_url)
    keeper.apply_url = preferred.url
    keeper.canonical_apply_url = preferred.canonical_url
    keeper.ats_type = preferred.ats_type
    session.flush()
    return (
        MergeResult(
            keeper_id=keeper.id,
            merged_ids=merged_ids,
            signal=signal,
            apply_url=apply_url,
            source_ids=tuple(source.id for source in sources),
        ),
        blocked,
    )


def _move_children(session: Session, source_job: Job, keeper: Job) -> None:
    for posting in list(source_job.sources):
        source_job.sources.remove(posting)
        keeper.sources.append(posting)
    keeper_kinds = {requirement.kind for requirement in keeper.requirements}
    for requirement in list(source_job.requirements):
        source_job.requirements.remove(requirement)
        if requirement.kind in keeper_kinds and requirement.kind is not RequirementKind.other:
            session.delete(requirement)
            continue
        keeper.requirements.append(requirement)
        keeper_kinds.add(requirement.kind)
    locations = list(keeper.locations or [])
    for location in source_job.locations or []:
        if location not in locations:
            locations.append(location)
    keeper.locations = locations
    _prefer_description(keeper, source_job)
    session.flush()


def _prefer_description(keeper: Job, other: Job) -> None:
    if not other.description_text:
        return
    keeper_norm = normalize_description(keeper.description_text) or ""
    other_norm = normalize_description(other.description_text) or ""
    if len(other_norm) > len(keeper_norm) or (len(other_norm) == len(keeper_norm) and other.id < keeper.id and other_norm):
        keeper.description_text = other.description_text
        keeper.description_hash = content_hash(other.description_text)


def _fill_keeper(keeper: Job) -> None:
    if keeper.description_text and keeper.description_hash is None:
        keeper.description_hash = content_hash(keeper.description_text)


def _retarget_duplicate_pointers(session: Session, removed_id: int, keeper_id: int) -> None:
    for job in session.scalars(select(Job).where(Job.possible_duplicate_of == removed_id)):
        job.possible_duplicate_of = None if job.id == keeper_id else keeper_id
    keeper = session.get(Job, keeper_id)
    if keeper is not None and keeper.possible_duplicate_of == removed_id:
        keeper.possible_duplicate_of = None


def _profile(job: Job) -> JobProfile:
    return JobProfile(
        company=job.company_name,
        title=job.title,
        locations=tuple(job.locations or []),
        description_text=job.description_text,
        sources=tuple(_source_ref(source) for source in job.sources),
        apply_url=job.apply_url,
    )


def _source_ref(source: JobSource) -> SourceRef:
    return SourceRef(
        ats_type=source.ats_type,
        board_key=source.board_key,
        external_id=source.external_id,
        url=source.url,
        first_seen_at=source.first_seen_at,
    )
