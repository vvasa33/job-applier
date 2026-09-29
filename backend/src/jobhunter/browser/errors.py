"""Failures from the visible browser. None of these submit a form."""


class BrowserError(Exception):
    """The browser could not complete an action."""


class NavigationTimeout(BrowserError):
    """A page did not load within the navigation timeout."""


class UnexpectedPage(BrowserError):
    """Navigation finished on a page other than the one that was required."""


class SubmitRefused(BrowserError):
    """The action would submit a form, which this milestone never does."""
