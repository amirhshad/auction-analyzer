import time
import asyncio


class RateLimiter:
    """Enforces minimum delay between calls for respectful scraping."""

    def __init__(self, delay_seconds: float = 2.5):
        self.delay_seconds = delay_seconds
        self._last_call: float = 0.0

    def wait(self) -> None:
        """Block until enough time has passed since the last call."""
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_call = time.monotonic()

    async def async_wait(self) -> None:
        """Async version of wait."""
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.delay_seconds:
            await asyncio.sleep(self.delay_seconds - elapsed)
        self._last_call = time.monotonic()
