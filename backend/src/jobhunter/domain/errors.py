class DuplicateApplication(Exception):
    """Raised when a second application is opened for a job that already has one."""


class ApplicationTransitionError(Exception):
    """Raised when an application status change is not allowed."""


class ResumeVersionError(Exception):
    """Raised when a resume version breaks the master/generated split."""
