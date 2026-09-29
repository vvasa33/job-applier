"""Allowed application statuses and the transitions between them. No I/O."""

from jobhunter.domain.enums import ApplicationStatus
from jobhunter.domain.errors import ApplicationTransitionError

TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.found: frozenset(
        {ApplicationStatus.matched, ApplicationStatus.saved, ApplicationStatus.withdrawn}
    ),
    ApplicationStatus.matched: frozenset({ApplicationStatus.saved, ApplicationStatus.withdrawn}),
    ApplicationStatus.saved: frozenset({ApplicationStatus.tailoring, ApplicationStatus.withdrawn}),
    ApplicationStatus.tailoring: frozenset(
        {ApplicationStatus.ready_to_apply, ApplicationStatus.saved, ApplicationStatus.withdrawn}
    ),
    ApplicationStatus.ready_to_apply: frozenset(
        {ApplicationStatus.applying, ApplicationStatus.tailoring, ApplicationStatus.withdrawn}
    ),
    ApplicationStatus.applying: frozenset(
        {
            ApplicationStatus.waiting_for_user,
            ApplicationStatus.submitted,
            ApplicationStatus.ready_to_apply,
            ApplicationStatus.withdrawn,
        }
    ),
    ApplicationStatus.waiting_for_user: frozenset(
        {
            ApplicationStatus.applying,
            ApplicationStatus.ready_to_apply,
            ApplicationStatus.submitted,
            ApplicationStatus.withdrawn,
        }
    ),
    ApplicationStatus.submitted: frozenset(
        {
            ApplicationStatus.rejected,
            ApplicationStatus.interview,
            ApplicationStatus.offer,
            ApplicationStatus.withdrawn,
        }
    ),
    ApplicationStatus.interview: frozenset(
        {ApplicationStatus.offer, ApplicationStatus.rejected, ApplicationStatus.withdrawn}
    ),
    ApplicationStatus.offer: frozenset({ApplicationStatus.withdrawn}),
    ApplicationStatus.rejected: frozenset(),
    ApplicationStatus.withdrawn: frozenset(),
}

# Entering ready_to_apply is the point a tailored resume must already be attached.
REQUIRES_RESUME_VERSION = frozenset({ApplicationStatus.ready_to_apply})


def allowed_targets(status: ApplicationStatus) -> tuple[ApplicationStatus, ...]:
    return tuple(sorted(TRANSITIONS[status], key=lambda item: item.value))


def require_transition(current: ApplicationStatus, target: ApplicationStatus) -> None:
    if target in TRANSITIONS[current]:
        return
    allowed = ", ".join(item.value for item in allowed_targets(current)) or "none"
    raise ApplicationTransitionError(
        f"cannot move an application from {current.value} to {target.value}; allowed: {allowed}"
    )
