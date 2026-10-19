"""BlueGate HTTP API.

``POST /scans`` admits a scan of one public GitHub repository. A new scan
answers ``202`` with a ``scan_id``. ``GET /scans/{scan_id}`` is that scan's
status; ``scan`` polls it until ``done`` or ``failed``. A cached, still-valid
result answers ``200`` immediately.

``bluethroat clone`` and ``bluethroat bluegate scan`` both call
:meth:`BlueGateClient.scan`. Neither runs git. The text report ends with a
clone command pinned to the scanned commit.

Every request sends the GitHub App user access token as
``Authorization: Bearer``, the same session as BlueSkills. BlueGate checks it
against the Bluethroat CLI GitHub App and applies its quota to the GitHub
user id.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from bluethroat.api import _public, _raise_for_status
from bluethroat.credentials import Session
from bluethroat.errors import AUTH, INVALID, SERVICE, CliError

_TERMINAL = {"done", "failed"}
_RUNNING = {"queued", "fetching", "analysing"}
_WAIT = 30 * 60
_SCAN_ID = re.compile(r"^[0-9a-f]{32}$")
_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,38}$")
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_GITHUB = {"github.com", "www.github.com"}


class BlueGateClient:
    def __init__(
        self,
        client: httpx.Client,
        api_url: str,
        session: Session,
        *,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._http = client
        self._api = api_url.rstrip("/")
        self._session = session
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic

    def scan(self, repo: str, *, ref: str | None) -> dict:
        repo_url, chosen = _prepare(repo, ref)
        body: dict[str, str] = {"repo_url": repo_url}
        if chosen:
            body["ref"] = chosen
        print("Scanning…", file=_err(), flush=True)
        code, doc = self._request("POST", "/scans", json=body)
        _raise_bluegate(code, doc)
        if _finished(doc):
            return _public(doc)
        scan_id = str(doc.get("scan_id") or "")
        if not _SCAN_ID.fullmatch(scan_id):
            raise CliError("The scan did not return an id to wait on.", SERVICE)
        return self._wait(scan_id)

    def report(self, scan_id: str, *, fmt: str) -> str:
        """The server-rendered report. ``fmt`` is ``text`` or ``agent``."""
        if not _SCAN_ID.fullmatch(scan_id):
            raise CliError("The scan did not return an id.", SERVICE)
        if fmt not in {"text", "agent"}:
            raise CliError("format must be text or agent.", INVALID)
        try:
            response = self._http.request(
                "GET",
                f"{self._api}/scans/{scan_id}",
                params={"format": fmt},
                headers=self._headers(accept="text/plain"),
            )
        except httpx.HTTPError as exc:
            raise CliError(f"Could not reach BlueGate ({exc.__class__.__name__}).", SERVICE) from exc
        if response.status_code != 200:
            _raise_bluegate(response.status_code, _json_body(response))
            raise CliError("The report was not ready.", SERVICE)
        return response.text

    def _wait(self, scan_id: str) -> dict:
        print("Waiting for the report…", file=_err(), flush=True)
        deadline = self._clock() + _WAIT
        fast_until = self._clock() + 30
        interval = 2.0
        while self._clock() < deadline:
            self._sleep(interval)
            if self._clock() >= fast_until:
                interval = 10.0
            code, doc = self._request("GET", f"/scans/{scan_id}")
            if code == 404:
                continue
            _raise_bluegate(code, doc)
            if _finished(doc):
                return _public(doc)
        raise CliError(
            "The report was not ready after 30 minutes. Run the same scan again.",
            SERVICE,
        )

    def _headers(self, *, accept: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._session.access_token}",
            "User-Agent": "bluethroat",
            "Accept": accept,
        }

    def _request(self, method: str, path: str, **kwargs: object) -> tuple[int, dict]:
        try:
            response = self._http.request(
                method,
                f"{self._api}{path}",
                headers=self._headers(accept="application/json"),
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise CliError(f"Could not reach BlueGate ({exc.__class__.__name__}).", SERVICE) from exc
        body = _json_body(response)
        return response.status_code, body | {"_retry_after": response.headers.get("retry-after")}


def _finished(doc: dict) -> bool:
    status = str(doc.get("status") or "")
    if status in _TERMINAL:
        return True
    if status in _RUNNING:
        return False
    return False


def _raise_bluegate(code: int, doc: dict) -> None:
    if code == 401:
        raise CliError(
            str(doc.get("error") or "BlueGate refused the GitHub token. Run bluethroat auth login."),
            AUTH,
        )
    if code == 404:
        raise CliError(
            str(doc.get("error") or (
                "BlueGate returned HTTP 404. Check BLUETHROAT_BLUEGATE_API_URL "
                "if this was not a missing repository."
            )),
            INVALID,
        )
    if code in {403, 451}:
        raise CliError(str(doc.get("error") or f"BlueGate returned HTTP {code}."), INVALID)
    _raise_for_status(code, doc, service="BlueGate")


def _json_body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _prepare(source: str, ref: str | None) -> tuple[str, str | None]:
    """A GitHub repository argument and the ref to send beside it.

    Accepted shapes match BlueGate's admission parser: an https URL, a
    ``github.com/owner/repo`` URL with the scheme omitted, or ``owner/repo``.
    A ref already present in a ``/tree/``, ``/blob/``, or ``/commit/`` URL must
    agree with ``--ref``.
    """
    text = source.strip()
    chosen = ref.strip() if isinstance(ref, str) else None
    if chosen == "":
        chosen = None
    if not text or any(ch.isspace() for ch in text) or "\\" in text:
        raise CliError(
            "Scan a public GitHub repository: https://github.com/owner/repo, "
            "github.com/owner/repo, or owner/repo.",
            INVALID,
        )
    lowered = text.lower()
    if lowered.startswith(("git@", "ssh://", "git://", "git+ssh://", "ssh+git://")):
        raise CliError("ssh and git:// URLs are not accepted; use the https URL.", INVALID)
    if "://" not in text and not lowered.startswith(("github.com/", "www.github.com/")):
        owner, sep, name = text.partition("/")
        if (
            sep
            and "/" not in name
            and _OWNER.fullmatch(owner)
            and _NAME.fullmatch(name)
            and name.strip(".") != ""
        ):
            return text, chosen
        raise CliError(
            "Scan a public GitHub repository: https://github.com/owner/repo, "
            "github.com/owner/repo, or owner/repo.",
            INVALID,
        )
    raw = text if "://" in text else "https://" + text
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise CliError("That does not look like a GitHub repository URL.", INVALID) from exc
    if parts.scheme != "https":
        raise CliError("Only https:// GitHub URLs are accepted.", INVALID)
    host = (parts.hostname or "").lower()
    if host not in _GITHUB:
        raise CliError("BlueGate scans GitHub repositories.", INVALID)
    if parts.username or parts.password or "@" in parts.netloc:
        raise CliError("URLs with embedded credentials are not accepted.", INVALID)
    segs = [segment for segment in parts.path.split("/") if segment]
    if len(segs) < 2:
        raise CliError("That URL does not look like a repository.", INVALID)
    existing = _url_ref(segs)
    if chosen and existing and existing != chosen:
        raise CliError("The URL already names a different revision than --ref.", INVALID)
    return raw, chosen


def _url_ref(segs: list[str]) -> str | None:
    tail = segs[2:]
    if len(tail) >= 2 and tail[0] in {"tree", "blob"}:
        return tail[1]
    if len(tail) == 2 and tail[0] == "commit":
        return tail[1]
    return None


def _err():
    import sys

    return sys.stderr
