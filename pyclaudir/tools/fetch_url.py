"""fetch_url — SSRF-safe URL fetcher replacing CC built-in WebFetch."""

from __future__ import annotations

import logging

import httpx
from pydantic import BaseModel, Field

from ..security import validate_url_not_ssrf
from .base import BaseTool, ToolResult

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0
_MAX_REDIRECTS = 3


class FetchUrlArgs(BaseModel):
    url: str = Field(description="URL to fetch. Must be a public address.")
    max_bytes: int = Field(
        default=65536,
        ge=1,
        le=1_048_576,
        description="Truncate response body to this many bytes.",
    )


async def _fetch_validated(
    client: httpx.AsyncClient, url: str
) -> tuple[httpx.Response, ToolResult | None]:
    """GET ``url``, following redirects manually and re-checking SSRF on
    every hop so an intermediate 3xx to an internal IP is never fetched.

    Returns ``(response, None)`` on success or ``(response, error_result)``
    when a hop is blocked or the redirect cap is exceeded.
    """
    response = None
    for _ in range(_MAX_REDIRECTS + 1):
        response = await client.get(url)
        if not response.is_redirect:
            return response, None
        next_url = str(httpx.URL(url).join(response.headers.get("location", "")))
        redirect_err = validate_url_not_ssrf(next_url)
        if redirect_err:
            log.warning("fetch_url redirect blocked: %s", redirect_err)
            return response, ToolResult(
                content=f"Blocked after redirect: {redirect_err}", is_error=True
            )
        url = next_url
    return response, ToolResult(content="Too many redirects", is_error=True)


class FetchUrlTool(BaseTool):
    name = "fetch_url"
    description = (
        "Fetch a URL safely. Validates against SSRF (blocks private/internal IPs) "
        "before fetching. Use instead of WebFetch."
    )
    args_model = FetchUrlArgs

    async def run(self, args: FetchUrlArgs) -> ToolResult:
        ssrf_error = validate_url_not_ssrf(args.url)
        if ssrf_error:
            log.warning("fetch_url blocked: %s", ssrf_error)
            return ToolResult(content=f"Blocked: {ssrf_error}", is_error=True)

        try:
            async with httpx.AsyncClient(
                follow_redirects=False, timeout=_TIMEOUT_SECONDS
            ) as client:
                response, err = await _fetch_validated(client, args.url)
                if err is not None:
                    return err
                body = response.text[: args.max_bytes]
                truncated = len(response.text) > args.max_bytes
                suffix = (
                    f"\n\n[truncated to {args.max_bytes} bytes]" if truncated else ""
                )
                return ToolResult(
                    content=body + suffix,
                    data={
                        "status_code": response.status_code,
                        "final_url": str(response.url),
                        "content_type": response.headers.get("content-type", ""),
                        "truncated": truncated,
                    },
                )
        except httpx.TimeoutException:
            return ToolResult(
                content=f"Request timed out after {_TIMEOUT_SECONDS}s", is_error=True
            )
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                content=f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
                is_error=True,
            )
        except httpx.RequestError as exc:
            return ToolResult(content=f"Request failed: {exc}", is_error=True)
