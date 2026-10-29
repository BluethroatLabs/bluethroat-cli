from __future__ import annotations

import json

import httpx

from bluethroat.cli import main
from bluethroat.config import DEFAULT_BLUEPAPER_API_URL, Config
from bluethroat.credentials import MemoryStore, Session

_ID = "cnv_01J8Z3K4N5P6Q7R8S9T0"


def _session() -> Session:
    return Session(
        access_token="ghu_test",
        refresh_token="ghr_test",
        expires_at=10**12,
        refresh_expires_at=10**12,
        login="octocat",
        user_id=7,
    )


def _store() -> MemoryStore:
    store = MemoryStore()
    store.save(_session())
    return store


def _env(monkeypatch) -> None:
    monkeypatch.setenv("BLUETHROAT_GITHUB_CLIENT_ID", "Iv1.test")
    monkeypatch.setenv("BLUETHROAT_BLUEPAPER_API_URL", "https://paper.example")
    monkeypatch.setenv("BLUETHROAT_CONFIG_DIR", "/nonexistent-bluethroat-config")


def _status(value: str, **extra: object) -> dict:
    body = {
        "id": _ID,
        "status": value,
        "sha256": "a" * 64,
        "stage": None,
        "created_at": "2026-10-29T11:00:00Z",
        "started_at": "2026-10-29T11:00:01Z",
        "finished_at": None,
        "error": None,
    }
    body.update(extra)
    return body


def _report(*, hits: list[dict] | None = None) -> dict:
    return {
        "schema_version": "1.0.0",
        "conversion_id": _ID,
        "sha256": "a" * 64,
        "catalog_version": "1.0.0",
        "conversion_justified": True,
        "caveat": "Absence of patterns is not a malware verdict.",
        "hits": hits or [],
        "conversion": {
            "status": "succeeded",
            "pages": 1,
            "ocr_lang": None,
            "output_bytes": 9,
        },
    }


def test_convert_uploads_polls_and_writes_safe_pdf(monkeypatch, tmp_path, capsys):
    _env(monkeypatch)
    source = tmp_path / "memo.docx"
    source.write_bytes(b"unsafe document")
    polls = {"count": 0}
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer ghu_test"
        assert request.url.host == "paper.example"
        if request.method == "POST":
            body = request.read()
            seen["body"] = body
            seen["content_type"] = request.headers["content-type"]
            return httpx.Response(202, json={
                "id": _ID,
                "status": "queued",
                "sha256": "a" * 64,
                "bytes": len(source.read_bytes()),
                "created_at": "2026-10-29T11:00:00Z",
            })
        if request.url.path.endswith("/report"):
            return httpx.Response(200, json=_report())
        if request.url.path.endswith("/pdf"):
            return httpx.Response(200, content=b"%PDF-safe")
        polls["count"] += 1
        if polls["count"] == 1:
            return httpx.Response(200, json=_status("running", stage="converting"))
        return httpx.Response(
            200,
            json=_status("succeeded", finished_at="2026-10-29T11:00:02Z"),
        )

    code = main(
        ["bluepaper", "convert", str(source), "--ocr-lang", "eng"],
        store=_store(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
        clock=lambda: 0.0,
    )
    captured = capsys.readouterr()
    output = tmp_path / "memo-safe.pdf"
    assert code == 0
    assert output.read_bytes() == b"%PDF-safe"
    assert polls["count"] == 2
    assert str(seen["content_type"]).startswith("multipart/form-data")
    assert b"unsafe document" in seen["body"]
    assert b"ocr_lang" in seen["body"]
    assert b"eng" in seen["body"]
    assert "BluePaper SUCCEEDED" in captured.out
    assert f"Safe PDF: {output}" in captured.out
    assert "Uploading to BluePaper" in captured.err
    assert "Rebuilding the document" in captured.err


def test_json_report_with_hits_exits_suspicious(monkeypatch, tmp_path, capsys):
    _env(monkeypatch)
    source = tmp_path / "memo.pdf"
    source.write_bytes(b"%PDF-input")
    output = tmp_path / "rebuilt.pdf"
    report = _report(
        hits=[{"id": "pdf.javascript", "count": 2, "first_offset": 12}],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": _ID, "status": "queued"})
        if request.url.path.endswith("/report"):
            return httpx.Response(200, json=report)
        if request.url.path.endswith("/pdf"):
            return httpx.Response(200, content=b"%PDF-safe")
        return httpx.Response(200, json=_status("succeeded"))

    code = main(
        [
            "bluepaper",
            "convert",
            str(source),
            "--output",
            str(output),
            "--json",
        ],
        store=_store(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
        clock=lambda: 0.0,
    )
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert code == 1
    assert document["conversion"]["status"] == "succeeded"
    assert document["report"]["hits"][0]["id"] == "pdf.javascript"
    assert document["output"] == str(output)
    assert output.read_bytes() == b"%PDF-safe"


def test_failed_conversion_prints_report_but_no_pdf(monkeypatch, tmp_path, capsys):
    _env(monkeypatch)
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-broken")
    fetched_pdf = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": _ID})
        if request.url.path.endswith("/report"):
            return httpx.Response(
                200,
                json=_report(
                    hits=[{"id": "pdf.javascript", "count": 1, "first_offset": 4}],
                ),
            )
        if request.url.path.endswith("/pdf"):
            fetched_pdf["value"] = True
            raise AssertionError("PDF fetched for a failed conversion")
        return httpx.Response(
            200,
            json=_status("failed", error="conversion failed"),
        )

    code = main(
        ["bluepaper", "convert", str(source)],
        store=_store(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
        clock=lambda: 0.0,
    )
    captured = capsys.readouterr()
    assert code == 7
    assert not (tmp_path / "broken-safe.pdf").exists()
    assert fetched_pdf["value"] is False
    assert "BluePaper FAILED" in captured.out
    assert "conversion failed" in captured.out
    assert "pdf.javascript" in captured.out


def test_rate_limit_is_reported_as_json(monkeypatch, tmp_path, capsys):
    _env(monkeypatch)
    source = tmp_path / "memo.pdf"
    source.write_bytes(b"%PDF")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                429,
                headers={"retry-after": "12"},
                json={"detail": "concurrency limit"},
            ),
        ),
    )
    code = main(
        ["bluepaper", "convert", str(source), "--json"],
        store=_store(),
        client=client,
    )
    captured = capsys.readouterr()
    assert code == 5
    assert "retry after 12s" in captured.err
    assert json.loads(captured.out) == {
        "error": "concurrency limit",
        "retry_after": 12,
    }


def test_existing_output_is_not_replaced_without_force(monkeypatch, tmp_path, capsys):
    _env(monkeypatch)
    source = tmp_path / "memo.pdf"
    source.write_bytes(b"%PDF")
    output = tmp_path / "memo-safe.pdf"
    output.write_bytes(b"keep")
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(500)

    code = main(
        ["bluepaper", "convert", str(source)],
        store=_store(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert code == 4
    assert output.read_bytes() == b"keep"
    assert calls["count"] == 0
    assert "--force" in capsys.readouterr().err


def test_bluepaper_requires_login(tmp_path):
    source = tmp_path / "memo.pdf"
    source.write_bytes(b"%PDF")
    assert main(
        ["bluepaper", "convert", str(source)],
        store=MemoryStore(),
    ) == 6


def test_bluepaper_without_subcommand():
    assert main(["bluepaper"], store=MemoryStore()) == 4


def test_default_bluepaper_api_url(monkeypatch):
    monkeypatch.delenv("BLUETHROAT_BLUEPAPER_API_URL", raising=False)
    monkeypatch.setenv("BLUETHROAT_CONFIG_DIR", "/nonexistent-bluethroat-config")
    assert Config.load().bluepaper_api_url == DEFAULT_BLUEPAPER_API_URL
    assert DEFAULT_BLUEPAPER_API_URL == "https://bluepaper.bluethroatlabs.com"


def test_bluepaper_api_url_can_come_from_config(monkeypatch, tmp_path):
    monkeypatch.setenv("BLUETHROAT_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"bluepaper_api_url": "https://file-paper.example/"}),
        encoding="utf-8",
    )
    monkeypatch.delenv("BLUETHROAT_BLUEPAPER_API_URL", raising=False)
    assert Config.load().bluepaper_api_url == "https://file-paper.example"
