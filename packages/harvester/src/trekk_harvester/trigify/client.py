"""Request logic behind the generated `TrigifyClient`: auth header, paths, query, body, errors."""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Self
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, ValidationError

from trekk_harvester.trigify.errors import TrigifyApiError
from trekk_harvester.trigify.operation import Operation
from trekk_harvester.trigify.spec import AUTH_HEADER

type QueryValue = str | int | float | bool | None


class BaseClient:
    """Async Trigify client core. The key travels only in the `x-api-key` header."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 5.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Trigify API key is empty")
        self._http = httpx2.AsyncClient(
            base_url=base_url, headers={AUTH_HEADER: api_key}, timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _call[M: BaseModel](
        self,
        op: Operation,
        response: type[M],
        *,
        path: tuple[str, ...] = (),
        query: Mapping[str, QueryValue] | None = None,
        body: BaseModel | None = None,
    ) -> M:
        url = op.path.format_map(
            {name: quote(value, safe="") for name, value in zip(op.path_params, path, strict=True)}
        )
        params = {k: v for k, v in (query or {}).items() if v is not None}
        payload = (
            None if body is None else body.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
        answer = await self._http.request(op.method, url, params=params, json=payload)
        if answer.is_success:
            return response.model_validate_json(answer.content)
        raise _api_error(answer)


def _api_error(answer: httpx2.Response) -> TrigifyApiError:
    # Deferred: _generated imports this module for BaseClient.
    from trekk_harvester.trigify._generated import ErrorResponse

    try:
        error: ErrorResponse | None = ErrorResponse.model_validate_json(answer.content)
    except ValidationError:
        error = None
    header = answer.headers.get("Retry-After")
    retry_after = (
        _retry_after_seconds(header)
        if header is not None
        else (error.error.retryAfterSeconds if error is not None else None)
    )
    return TrigifyApiError(answer.status_code, error, retry_after)


def _retry_after_seconds(header: str) -> float:
    """`Retry-After` (RFC 9110): delay seconds, or an HTTP-date turned into seconds from now."""
    if re.fullmatch(r"\d+(\.\d+)?", header.strip()):
        return float(header)
    return max(0.0, (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds())
