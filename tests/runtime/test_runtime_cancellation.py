"""Self-check tests for app/runtime/cancellation.py."""

import asyncio

import pytest

from app.runtime.cancellation import CancellationToken, OperationCancelled


def test_token_starts_not_cancelled():
    ct = CancellationToken()
    assert ct.is_cancelled is False


def test_cancel_sets_flag():
    ct = CancellationToken()
    ct.cancel()
    assert ct.is_cancelled is True


def test_check_raises_after_cancel():
    ct = CancellationToken()
    ct.cancel()
    with pytest.raises(OperationCancelled):
        ct.check()


def test_check_does_not_raise_when_not_cancelled():
    ct = CancellationToken()
    ct.check()  # should not raise


def test_wait_completes_after_cancel():
    """wait() must return once cancel() is called."""
    ct = CancellationToken()

    async def go():
        # Cancel from a task after a short delay
        async def canceller():
            await asyncio.sleep(0.01)
            ct.cancel()

        asyncio.create_task(canceller())
        await ct.wait()
        assert ct.is_cancelled

    asyncio.run(go())
