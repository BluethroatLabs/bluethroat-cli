"""BlueSkills HTTP API.

``POST /scan.json`` starts a scan. A hosted scan answers ``202`` with a
``scan_id`` while deeper analysis is still running. ``GET /scans/{scan_id}``
is the status of that scan and is not a command the user runs: ``scan`` polls
it until the report is finished.

Every request sends the GitHub App user access token as
``Authorization: Bearer``. BlueSkills checks it with the app's credentials and
keys its limits on the numeric GitHub user id. ``POST /auth/github/revoke``
revokes that token at GitHub for ``auth logout``.
"""

from __future__ import annotations

import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from bluethroat.credentials import Session
from bluethroat.errors import INVALID, RATE_LIMITED, SERVICE, CliError

_TERMINAL = {"done", "failed"}
_RUNNING = {"queued", "static", "queued_deep", "deep", "dynamic", "judging"}
_MAX_ZIP = 25 * 1024 * 1024
_MAX_TEXT = 1 * 1024 * 1024
_WAIT = 15 * 60


class BlueSkillsClient:
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

    def scan(self, source: str, *, ref: str | None) -> dict:
        kind, payload, filename = _prepare(source, ref)
        print("Scanning…", file=_err(), flush=True)
        if kind == "url":
            code, doc = self._request("POST", "/scan.json", data={"repo_url": payload})
        elif kind == "text":
            code, doc = self._request("POST", "/scan.json", data={"skill_md": payload})
        else:
            code, doc = self._request(
                "POST",
                "/scan.json",
                files={"file": (filename, payload, "application/zip")},
            )
        _raise_for_status(code, doc)
        if _finished(code, doc):
            return _public(doc)
        scan_id = str(doc.get("scan_id") or "")
        if not scan_id:
            raise CliError("The scan did not return an id to wait on.", SERVICE)
        return self._wait(scan_id)

    def revoke(self) -> None:
        """Ask BlueSkills to revoke the session's access token at GitHub."""
        code, _doc = self._request("POST", "/auth/github/revoke")
        if code != 204:
            raise CliError(
                "The local session was removed, but GitHub did not revoke the token. "
                "Revoke Bluethroat under GitHub Settings → Applications.",
                SERVICE,
            )

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
            _raise_for_status(code, doc)
            if _finished(code, doc):
                return _public(doc)
        raise CliError(
            "The report was not ready after 15 minutes. Run the same scan again.",
            SERVICE,
        )

    def _request(self, method: str, path: str, **kwargs: object) -> tuple[int, dict]:
        headers = {
            "Authorization": f"Bearer {self._session.access_token}",
            "User-Agent": "bluethroat",
            "Accept": "application/json",
        }
        try:
            response = self._http.request(method, f"{self._api}{path}", headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise CliError(f"Could not reach BlueSkills ({exc.__class__.__name__}).", SERVICE) from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        return response.status_code, body | {"_retry_after": response.headers.get("retry-after")}


def _public(doc: dict) -> dict:
    return {key: value for key, value in doc.items() if not str(key).startswith("_")}


def _finished(code: int, doc: dict) -> bool:
    status = str(doc.get("status") or "")
    if status in _TERMINAL:
        return True
    if status in _RUNNING:
        return False
    return code == 200 and ("worst_verdict" in doc or "verdict" in doc)


def _raise_for_status(code: int, doc: dict, *, service: str = "BlueSkills") -> None:
    if code == 429:
        retry = doc.get("retry_after")
        if retry is None:
            retry = doc.get("_retry_after") or "1"
        raise CliError(
            f"rate limited, retry after {retry}s",
            RATE_LIMITED,
            body={"error": str(doc.get("error") or "Not right now"), "retry_after": _int(retry)},
        )
    if code in {400, 413, 422}:
        raise CliError(str(doc.get("error") or "The scan was rejected."), INVALID)
    if code >= 400:
        raise CliError(str(doc.get("error") or f"{service} returned HTTP {code}."), SERVICE)


def _int(value: object) -> int:
    try:
        return max(1, int(float(str(value))))
    except (TypeError, ValueError):
        return 1


def _prepare(source: str, ref: str | None) -> tuple[str, str | bytes, str]:
    text = source.strip()
    if _is_repo_url(text):
        return "url", _with_ref(text, ref), ""
    if ref:
        raise CliError("--ref is only valid for a GitHub or GitLab URL.", INVALID)
    path = Path(text).expanduser()
    if not path.exists():
        raise CliError(f"No such file: {path}", INVALID)
    if path.is_symlink():
        raise CliError(f"{path} is a symlink and cannot be scanned.", INVALID)
    if path.is_dir():
        return "zip", _zip_dir(path), f"{path.name}.zip"
    if path.suffix.lower() == ".zip":
        data = path.read_bytes()
        if len(data) > _MAX_ZIP:
            raise CliError("That archive is larger than the 25 MiB limit.", INVALID)
        return "zip", data, path.name
    if path.name.lower() == "skill.md":
        data = path.read_text(encoding="utf-8")
        if len(data.encode("utf-8")) > _MAX_TEXT:
            raise CliError("That SKILL.md is larger than the 1 MiB limit.", INVALID)
        return "text", data, ""
    raise CliError(
        "Scan a public GitHub or GitLab URL, a skill directory, a .zip, or a SKILL.md file.",
        INVALID,
    )


def _is_repo_url(value: str) -> bool:
    if "://" not in value and not value.startswith(("github.com/", "gitlab.com/")):
        return False
    raw = value if "://" in value else "https://" + value
    try:
        host = (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return False
    return host in {"github.com", "www.github.com", "gitlab.com", "www.gitlab.com"}


def _with_ref(url: str, ref: str | None) -> str:
    raw = url if "://" in url else "https://" + url
    parts = urlsplit(raw)
    if parts.scheme != "https":
        raise CliError("Only https:// repository URLs are accepted.", INVALID)
    host = (parts.hostname or "").lower()
    segs = [segment for segment in parts.path.split("/") if segment]
    if len(segs) < 2:
        raise CliError("That URL does not look like a repository.", INVALID)
    gitlab = host.endswith("gitlab.com")
    existing = _existing_ref(segs, gitlab=gitlab)
    if ref and existing and existing != ref:
        raise CliError("The URL already names a different revision than --ref.", INVALID)
    chosen = ref or existing
    if not chosen or existing:
        path = "/" + "/".join(_strip_git(segs))
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    owner, repo, *rest = _strip_git(segs)
    middle = ["-", "tree", chosen] if gitlab else ["tree", chosen]
    path = "/" + "/".join([owner, repo, *middle, *rest])
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _strip_git(segs: list[str]) -> list[str]:
    if len(segs) >= 2 and segs[1].endswith(".git"):
        return [segs[0], segs[1][:-4], *segs[2:]]
    return segs


def _existing_ref(segs: list[str], *, gitlab: bool) -> str | None:
    tail = segs[2:]
    if gitlab and tail and tail[0] == "-":
        tail = tail[1:]
    if len(tail) >= 2 and tail[0] in {"tree", "blob"}:
        return tail[1]
    return None


def _zip_dir(root: Path) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise CliError(f"{path} is a symlink and cannot be scanned.", INVALID)
            if not path.is_file():
                continue
            archive.write(path, path.relative_to(root).as_posix())
    data = buf.getvalue()
    if len(data) > _MAX_ZIP:
        raise CliError("That directory zips to more than the 25 MiB limit.", INVALID)
    return data


def _err():
    import sys

    return sys.stderr
