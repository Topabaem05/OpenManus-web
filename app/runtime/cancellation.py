"""Cancellation support for agent runs.

A CancellationToken is a lightweight cooperative-cancel primitive. The
runner checks it between steps; external callers call cancel() to
request termination.
"""

import asyncio


class OperationCancelled(Exception):
    """Raised when a cancellation token has been triggered."""


class CancellationToken:
    """Cooperative cancellation token.

    Usage:
        ct = CancellationToken()
        ct.cancel()       # request cancellation
        ct.check()        # raises OperationCancelled if cancelled
        ct.is_cancelled   # True after cancel()
    """

    def __init__(self):
        self._event = asyncio.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        """Request cancellation."""
        self._event.set()

    def check(self) -> None:
        """Raise OperationCancelled if cancellation was requested."""
        if self._event.is_set():
            raise OperationCancelled("Operation was cancelled")

    async def wait(self) -> None:
        """Block until cancelled."""
        await self._event.wait()
