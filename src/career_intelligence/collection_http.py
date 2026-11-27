"""Bounded GET policy shared by public job board adapters."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from random import random

import httpx

from career_intelligence.config import CollectionLimits

RETRYABLE = {408, 429, 500, 502, 503, 504}


class FetchError(Exception):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


def utc_now() -> datetime:
    return datetime.now(UTC)


class HttpPolicy:
    def __init__(
        self,
        client: httpx.AsyncClient,
        limits: CollectionLimits,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random,
    ):
        self.client = client
        self.limits = limits
        self.monotonic = monotonic
        self.now = now
        self.sleep = sleep
        self.jitter = jitter
        self.deadline = monotonic() + limits.run_deadline_seconds
        self.next_request: dict[str, float] = {}
        self.cooldowns: dict[str, float] = {}

    def _remaining(self) -> float:
        remaining = self.deadline - self.monotonic()
        if remaining <= 0:
            raise FetchError("run_deadline")
        return remaining

    def _retry_after(self, header: str) -> float | None:
        header = header.strip()
        if header.isascii() and header.isdecimal():
            # Very long digit strings still mean a wait beyond any practical budget.
            return float(header)
        try:
            deadline = parsedate_to_datetime(header)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            return max(0, (deadline - self.now()).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None

    async def get(self, url: str, *, params: dict[str, str]) -> bytes:
        host = httpx.URL(url).host
        for attempt in range(1, self.limits.max_attempts + 1):
            remaining = self._remaining()
            now = self.monotonic()
            cooldown = self.cooldowns.get(host, 0) - now
            if cooldown > self.limits.max_retry_wait_seconds or cooldown >= remaining:
                raise FetchError("retry_deferred")
            delay = max(cooldown, self.next_request.get(host, 0) - now)
            if delay >= remaining:
                raise FetchError("run_deadline")
            if delay > 0:
                await self.sleep(delay)
            remaining = self._remaining()
            timeout = httpx.Timeout(
                min(remaining, self.limits.read_timeout_seconds),
                connect=min(remaining, self.limits.connect_timeout_seconds),
            )
            self.next_request[host] = self.monotonic() + self.limits.min_request_interval_seconds
            retry_after = None
            try:
                async with asyncio.timeout(remaining):
                    async with self.client.stream(
                        "GET",
                        url,
                        params=params,
                        timeout=timeout,
                        follow_redirects=False,
                        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                    ) as response:
                        if response.status_code == 200:
                            return await self._read_body(response)
                        failure = FetchError("http_status", response.status_code)
                        if response.status_code not in RETRYABLE:
                            raise failure
                        retry_after = self._retry_after(response.headers.get("Retry-After", ""))
            except TimeoutError:
                raise FetchError("run_deadline") from None
            except httpx.TransportError:
                failure = FetchError("transport_error")

            if retry_after is not None:
                # A provider cooldown applies to subsequent boards on this host too.
                self.cooldowns[host] = max(
                    self.cooldowns.get(host, 0), self.monotonic() + retry_after
                )
            if attempt == self.limits.max_attempts:
                raise failure
            delay = retry_after if retry_after is not None else (2 ** (attempt - 1) + self.jitter())
            if delay > self.limits.max_retry_wait_seconds or delay >= self._remaining():
                raise FetchError("retry_deferred", failure.status)
            if delay:
                await self.sleep(delay)
        raise AssertionError("max_attempts must be positive")

    async def _read_body(self, response: httpx.Response) -> bytes:
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise FetchError("unsupported_encoding")
        length = response.headers.get("Content-Length", "")
        if (
            length.isascii()
            and length.isdecimal()
            and float(length) > self.limits.max_response_bytes
        ):
            raise FetchError("response_limit")
        payload = bytearray()
        # iter_bytes also supports already-buffered test transports. Encoding was checked above.
        async for chunk in response.aiter_bytes():
            self._remaining()
            if len(payload) + len(chunk) > self.limits.max_response_bytes:
                raise FetchError("response_limit")
            payload.extend(chunk)
        self._remaining()
        return bytes(payload)
