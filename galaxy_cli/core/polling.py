"""Shared polling policy for blocking Galaxy operations."""

import time


ADAPTIVE_POLL_DELAYS = (5.0, 10.0, 20.0, 30.0)


def poll_delay(attempt, poll_interval=None):
    """Return the next bounded delay.

    ``poll_interval`` is a backwards-compatible fixed override.  When it is
    omitted, callers use the shared 5, 10, 20, 30, 30... adaptive sequence.
    """
    if poll_interval is not None:
        return max(0.0, float(poll_interval))
    index = min(max(0, int(attempt)), len(ADAPTIVE_POLL_DELAYS) - 1)
    return ADAPTIVE_POLL_DELAYS[index]


def deadline_after(timeout):
    """Return one absolute monotonic deadline for an operation."""
    return time.monotonic() + max(0.0, float(timeout))


def remaining(deadline):
    """Return non-negative seconds remaining before an absolute deadline."""
    return max(0.0, float(deadline) - time.monotonic())


def sleep_for_poll(attempt, deadline, poll_interval=None):
    """Sleep for the next policy delay without crossing ``deadline``."""
    delay = min(poll_delay(attempt, poll_interval), remaining(deadline))
    time.sleep(delay)
    return attempt + 1
