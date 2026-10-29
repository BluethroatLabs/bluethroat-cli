"""BluePaper document conversion client."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from bluethroat.credentials import Session
from bluethroat.errors import AUTH, INVALID, RATE_LIMITED, SERVICE, CliError

_TERMINAL = {"succeeded", "failed", "cancelled"}
_WAIT = 15 * 60


@dataclass(frozen=True)
class ConversionResult:
    conversion: dict
    report: dict | None
    pdf: bytes | None

    def document(self, output: Path | None) -> dict:
        return {
            "conversion": self.conversion,
            "report": self.report,
            "output": str(output) if output is not None else None,
        }


class BluePaperClient:
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

    def convert(self, source: Path, *, ocr_lang: str | None) -> ConversionResult:
        source = _source_file(source)
        data = {"ocr_lang": ocr_lang} if ocr_lang else None
        print("Uploading to BluePaper…", file=_err(), flush=True)
        with source.open("rb") as stream:
            response = self._request(
                "POST",
                "/v1/conversions",
                data=data,
                files={"file": (source.name, stream, "application/octet-stream")},
            )
        accepted = _json(response)
        _raise_for_status(response, accepted)
        conversion_id = str(accepted.get("id") or "")
        if not conversion_id:
            raise CliError("BluePaper did not return a conversion id.", SERVICE)

        conversion = self._wait(conversion_id)
        status = str(conversion.get("status") or "")
        report = self._report(conversion_id, required=status == "succeeded")
        pdf = self._pdf(conversion_id) if status == "succeeded" else None
        return ConversionResult(conversion=conversion, report=report, pdf=pdf)

    def _wait(self, conversion_id: str) -> dict:
        print("Rebuilding the document…", file=_err(), flush=True)
        deadline = self._clock() + _WAIT
        interval = 2.0
        while self._clock() < deadline:
            self._sleep(interval)
            response = self._request("GET", f"/v1/conversions/{conversion_id}")
            body = _json(response)
            _raise_for_status(response, body)
            if str(body.get("status") or "") in _TERMINAL:
                return body
            interval = min(10.0, interval + 1.0)
        raise CliError(
            "The conversion was not ready after 15 minutes. "
            f"Conversion id: {conversion_id}",
            SERVICE,
        )

    def _report(self, conversion_id: str, *, required: bool) -> dict | None:
        response = self._request("GET", f"/v1/conversions/{conversion_id}/report")
        if not required and response.status_code == 409:
            return None
        body = _json(response)
        _raise_for_status(response, body)
        return body

    def _pdf(self, conversion_id: str) -> bytes:
        response = self._request("GET", f"/v1/conversions/{conversion_id}/pdf")
        if response.status_code >= 400:
            _raise_for_status(response, _json(response))
        if not response.content:
            raise CliError("BluePaper returned an empty PDF.", SERVICE)
        return response.content

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            return self._http.request(
                method,
                f"{self._api}{path}",
                headers={
                    "Authorization": f"Bearer {self._session.access_token}",
                    "User-Agent": "bluethroat",
                    "Accept": "application/json",
                },
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise CliError(
                f"Could not reach BluePaper ({exc.__class__.__name__}).",
                SERVICE,
            ) from exc


def default_output(source: Path) -> Path:
    return source.with_name(f"{source.stem}-safe.pdf")


def check_output(path: Path, *, force: bool) -> None:
    if path.is_symlink():
        raise CliError(f"{path} is a symlink and cannot be replaced.", INVALID)
    if path.exists() and not force:
        raise CliError(f"{path} already exists. Pass --force to replace it.", INVALID)
    if not path.parent.is_dir():
        raise CliError(f"Output directory does not exist: {path.parent}", INVALID)


def write_pdf(path: Path, payload: bytes, *, force: bool) -> None:
    check_output(path, force=force)
    mode = "wb" if force else "xb"
    try:
        with path.open(mode) as stream:
            stream.write(payload)
    except OSError as exc:
        raise CliError(f"Could not write {path} ({exc.__class__.__name__}).", INVALID) from exc


def render(result: ConversionResult, output: Path | None) -> str:
    conversion = result.conversion
    status = str(conversion.get("status") or "unknown").upper()
    lines = [f"BluePaper {status}"]
    if output is not None:
        lines.append(f"Safe PDF: {output}")
    if conversion.get("sha256"):
        lines.append(f"SHA-256: {conversion['sha256']}")
    if conversion.get("error"):
        lines.append(f"Error: {conversion['error']}")
    if result.report is not None:
        hits = result.report.get("hits")
        hit_list = hits if isinstance(hits, list) else []
        lines.append(f"Byte indicators: {len(hit_list)}")
        for hit in hit_list:
            if not isinstance(hit, dict):
                continue
            name = str(hit.get("id") or "unknown")
            count = hit.get("count") or 0
            offset = hit.get("first_offset")
            suffix = f", first offset {offset}" if offset is not None else ""
            lines.append(f"- {name}: {count}{suffix}")
        caveat = result.report.get("caveat")
        if caveat:
            lines.append(str(caveat))
    return "\n".join(lines)


def _source_file(source: Path) -> Path:
    path = source.expanduser()
    if path.is_symlink():
        raise CliError(f"{path} is a symlink and cannot be uploaded.", INVALID)
    if not path.exists():
        raise CliError(f"No such file: {path}", INVALID)
    if not path.is_file():
        raise CliError(f"Not a file: {path}", INVALID)
    return path


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = None
    return body if isinstance(body, dict) else {}


def _raise_for_status(response: httpx.Response, body: dict) -> None:
    code = response.status_code
    if code < 400:
        return
    detail = body.get("detail") or body.get("error") or f"BluePaper returned HTTP {code}."
    message = detail if isinstance(detail, str) else "BluePaper rejected the request."
    if code == 401:
        raise CliError("GitHub authorization failed. Run bluethroat auth login.", AUTH)
    if code == 429:
        retry = response.headers.get("retry-after") or body.get("retry_after") or "1"
        raise CliError(
            f"rate limited, retry after {retry}s",
            RATE_LIMITED,
            body={"error": message, "retry_after": _int(retry)},
        )
    if code in {400, 413, 415, 422}:
        raise CliError(message, INVALID)
    raise CliError(message, SERVICE)


def _int(value: object) -> int:
    try:
        return max(1, int(float(str(value))))
    except (TypeError, ValueError):
        return 1


def _err():
    import sys

    return sys.stderr
