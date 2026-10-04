"""Step interface: a wrapper around one API endpoint."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar

import requests

if TYPE_CHECKING:
    from model.Group import Group
    from model.User import User

REDACTED_KEYS = frozenset({"password", "token"})
TEXT_PREVIEW_LENGTH = 500


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class StepRequest:
    """Description of the HTTP request a step wants to send.

    If `user` is set and has a cached token, a bearer Authorization header is
    added, unless `headers` already contains one. Negative tests can rely on
    this behavior to send no token or a forged one.
    """

    method: str
    path: str
    params: dict[str, Any] | None = None
    json: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    # field name -> (filename, content, content type)
    files: dict[str, tuple[str, bytes, str]] | None = None
    user: User | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class StepResult:
    """Everything that happened when a step ran. Never raised, always returned."""

    step: str
    group: str
    started_at: str = field(default_factory=utc_now)

    # Request
    method: str | None = None
    url: str | None = None
    request_params: dict[str, Any] | None = None
    request_json: dict[str, Any] | None = None
    request_data: dict[str, Any] | None = None
    request_files: dict[str, str] | None = None   # field name -> filename
    authenticated: bool = False

    # Response
    status_code: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    content_type: str | None = None
    response_json: Any = None
    content: bytes | None = field(default=None, repr=False)
    elapsed: float | None = None                  # seconds

    # Transport or build failure (timeout, connection refused, ...)
    error: str | None = None

    # Step-specific decoded data, e.g. a decrypted RMAP payload.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def responded(self) -> bool:
        """True if the server answered, whatever the status code."""
        return self.status_code is not None

    @property
    def ok(self) -> bool:
        """True if the server answered with a 2xx status and no error occurred."""
        return self.error is None and self.status_code is not None \
            and 200 <= self.status_code < 300

    def to_dict(self) -> dict[str, Any]:
        """Report-friendly representation. Secrets are redacted, bytes summarized."""
        preview = None
        if self.content and self.response_json is None and self.content_type \
                and self.content_type.startswith("text/"):
            preview = self.content[:TEXT_PREVIEW_LENGTH].decode("utf-8", "replace")
        return {
            "step": self.step,
            "group": self.group,
            "started_at": self.started_at,
            "elapsed": self.elapsed,
            "request": {
                "method": self.method,
                "url": self.url,
                "params": redact(self.request_params),
                "json": redact(self.request_json),
                "data": redact(self.request_data),
                "files": self.request_files,
                "authenticated": self.authenticated,
            },
            "response": {
                "status_code": self.status_code,
                "content_type": self.content_type,
                "json": redact(self.response_json),
                "content_length": len(self.content) if self.content is not None else None,
                "text_preview": preview,
            },
            "error": self.error,
            "extra": redact(self.extra),
        }


class Step(ABC):
    """Wrapper around one API endpoint.

    A step only sends its request and returns a StepResult. It performs no
    verification, which is the scenario's job. Subclasses receive what they
    need (users, documents, versions, ...) through their constructor and
    implement build_request(). They may override after_response() for side
    effects tied to the call itself, such as caching a login token or
    decrypting an RMAP payload into result.extra.

    Example:

        class Healthz(Step):
            def build_request(self, group):
                return StepRequest("GET", "/api/healthz")
    """

    #: Display name in logs and reports. Defaults to the class name.
    name: ClassVar[str | None] = None

    @property
    def display_name(self) -> str:
        return self.name or type(self).__name__

    @abstractmethod
    def build_request(self, group: Group) -> StepRequest:
        """Describe the HTTP request to send to `group`."""

    def after_response(self, group: Group, result: StepResult) -> None:
        """Hook called after a response is received (any status code)."""

    def run(self, group: Group) -> StepResult:
        """Send the request to `group` and return what happened. Never raises."""
        result = StepResult(step=self.display_name, group=group.name)

        try:
            request = self.build_request(group)
        except Exception as exc:  # e.g. a missing local PDF
            result.error = f"could not build request: {type(exc).__name__}: {exc}"
            return result

        headers = dict(request.headers)
        if request.user is not None and request.user.token \
                and not any(k.lower() == "authorization" for k in headers):
            headers.update(request.user.auth_headers())

        result.method = request.method.upper()
        result.url = group.url(request.path)
        result.request_params = request.params
        result.request_json = request.json
        result.request_data = request.data
        result.request_files = ({k: v[0] for k, v in request.files.items()}
                                if request.files else None)
        result.authenticated = any(k.lower() == "authorization" for k in headers)

        start = time.perf_counter()
        try:
            response = group.session.request(
                result.method, result.url,
                params=request.params, json=request.json, data=request.data,
                files=request.files, headers=headers, timeout=group.timeout,
            )
        except requests.RequestException as exc:
            result.elapsed = time.perf_counter() - start
            result.error = f"{type(exc).__name__}: {exc}"
            return result
        result.elapsed = time.perf_counter() - start

        result.status_code = response.status_code
        result.response_headers = dict(response.headers)
        result.content_type = response.headers.get("Content-Type")
        result.content = response.content
        result.response_json = _parse_json(response)

        try:
            self.after_response(group, result)
        except Exception as exc:
            result.error = f"after_response failed: {type(exc).__name__}: {exc}"
        return result


def _parse_json(response: requests.Response) -> Any:
    """Parse the body as JSON if it is declared as or looks like JSON."""
    content_type = response.headers.get("Content-Type", "")
    looks_like_json = response.content[:1] in (b"{", b"[")
    if "json" not in content_type and not looks_like_json:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def redact(value: Any) -> Any:
    """Recursively replace values of sensitive keys with '***'."""
    if isinstance(value, dict):
        return {k: "***" if k in REDACTED_KEYS and v is not None else redact(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
