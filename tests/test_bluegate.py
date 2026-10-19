import json

import httpx
import pytest

from bluethroat.cli import main
from bluethroat.config import DEFAULT_BLUEGATE_API_URL, Config
from bluethroat.credentials import MemoryStore, Session

_SHA = "ab" * 20
_SCAN = "a" * 32


def _session() -> Session:
    return Session(
        access_token="ghu_test",
        refresh_token="ghr_test",
        expires_at=10**12,
        refresh_expires_at=10**12,
        login="octocat",
        user_id=7,
    )


def _env(monkeypatch) -> None:
    monkeypatch.setenv("BLUETHROAT_GITHUB_CLIENT_ID", "Iv1.test")
    monkeypatch.setenv("BLUETHROAT_API_URL", "https://skills.example")
    monkeypatch.setenv("BLUETHROAT_BLUEGATE_API_URL", "https://gate.example")
    monkeypatch.setenv("BLUETHROAT_CONFIG_DIR", "/nonexistent-bluethroat-config")


def _done(**extra: object) -> dict:
    doc = {
        "scan_id": _SCAN,
        "status": "done",
        "complete": True,
        "verdict": "malicious",
        "score": 86,
        "input": {
            "repo": "https://github.com/owner/repository",
            "commit": _SHA,
        },
    }
    doc.update(extra)
    return doc


@pytest.mark.parametrize(
    "argv",
    [
        ["bluegate", "scan", "https://github.com/owner/repository", "--ref", "main"],
        ["clone", "https://github.com/owner/repository", "--ref", "main"],
    ],
)
def test_clone_and_bluegate_scan_wait_and_print_the_report(monkeypatch, capsys, argv):
    _env(monkeypatch)
    polls = {"n": 0}
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        assert request.url.host == "gate.example"
        if request.method == "POST" and request.url.path == "/scans":
            seen["body"] = json.loads(request.content)
            return httpx.Response(202, json={
                "scan_id": _SCAN,
                "status": "queued",
                "repo": "https://github.com/owner/repository",
                "commit": _SHA,
                "ref": "main",
                "poll": f"/scans/{_SCAN}",
            })
        if request.url.params.get("format") == "text":
            seen["text"] = True
            return httpx.Response(200, text="BlueGate MALICIOUS owner/repository\n")
        polls["n"] += 1
        if polls["n"] == 1:
            return httpx.Response(202, json={"scan_id": _SCAN, "status": "analysing"})
        return httpx.Response(200, json=_done())

    store = MemoryStore()
    store.save(_session())
    code = main(
        argv,
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
        clock=lambda: 0.0,
    )
    captured = capsys.readouterr()
    assert code == 2
    assert seen["authorization"] == "Bearer ghu_test"
    assert seen["body"] == {
        "repo_url": "https://github.com/owner/repository",
        "ref": "main",
    }
    assert polls["n"] == 2
    assert seen["text"] is True
    assert captured.out == "BlueGate MALICIOUS owner/repository\n"
    assert "Waiting for the report" in captured.err


def test_json_prints_the_document_without_fetching_text(monkeypatch, capsys):
    _env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("format"):
            raise AssertionError(request.url)
        if request.method == "POST":
            assert json.loads(request.content) == {"repo_url": "owner/repository"}
            return httpx.Response(200, json=_done())
        raise AssertionError(request.url.path)

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["clone", "owner/repository", "--json"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    captured = capsys.readouterr()
    assert code == 2
    assert json.loads(captured.out)["verdict"] == "malicious"
    assert "Waiting for the report" not in captured.err


def test_agent_report_uses_the_agent_format(monkeypatch, capsys):
    _env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=_done(verdict="clean", score=0))
        assert request.url.params.get("format") == "agent"
        return httpx.Response(200, text="BlueGate verdict: CLEAN\n")

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["bluegate", "scan", "github.com/owner/repository", "--agent"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert code == 0
    assert capsys.readouterr().out == "BlueGate verdict: CLEAN\n"


def test_partial_is_incomplete(monkeypatch):
    _env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=_done(verdict="partial", complete=False, score=4))
        return httpx.Response(200, text="BlueGate PARTIAL\n")

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["clone", "https://github.com/owner/repository"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert code == 3


def test_failed_scan_prints_the_report_and_exits_service(monkeypatch, capsys):
    _env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=_done(status="failed", verdict=None, complete=False))
        return httpx.Response(200, text="BlueGate NO-VERDICT\n")

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["clone", "https://github.com/owner/repository"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    captured = capsys.readouterr()
    assert code == 7
    assert captured.out == "BlueGate NO-VERDICT\n"


def test_rate_limit_exits_without_a_verdict(monkeypatch, capsys):
    _env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "12"},
            json={"error": "Not right now", "retry_after": 12},
        )

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["bluegate", "scan", "https://github.com/owner/repository", "--json"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    captured = capsys.readouterr()
    assert code == 5
    assert "retry after 12s" in captured.err
    assert json.loads(captured.out)["retry_after"] == 12


def test_missing_repository_is_invalid(monkeypatch, capsys):
    _env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "No such repository.", "code": "REPO_NOT_FOUND"})

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["clone", "https://github.com/owner/missing"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    captured = capsys.readouterr()
    assert code == 4
    assert "No such repository" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["clone", "https://github.com/owner/repository"],
        ["bluegate", "scan", "https://github.com/owner/repository"],
    ],
)
def test_scan_requires_login(argv):
    assert main(argv, store=MemoryStore()) == 6


def test_bluegate_without_a_subcommand():
    assert main(["bluegate"], store=MemoryStore()) == 4


def test_full_sha_mismatch_prints_no_verdict(monkeypatch, capsys):
    _env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("format"):
            raise AssertionError("report fetched for the wrong commit")
        return httpx.Response(200, json=_done(input={"commit": "ff" * 20, "repo": "https://github.com/owner/repository"}))

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["clone", "https://github.com/owner/repository", "--ref", "ab" * 20],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    captured = capsys.readouterr()
    assert code == 4
    assert captured.out == ""
    assert "different commit" in captured.err


@pytest.mark.parametrize(
    "repo",
    [
        "https://gitlab.com/owner/repository",
        "https://github.com/owner/repository/tree/main",
        "./SKILL.md",
        "not a repo",
    ],
)
def test_rejects_a_source_bluegate_cannot_scan(monkeypatch, repo):
    _env(monkeypatch)
    called = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(500, json={})

    store = MemoryStore()
    store.save(_session())
    ref = ["--ref", "dev"] if repo.endswith("/tree/main") else []
    code = main(
        ["clone", repo, *ref],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert code == 4
    assert called["n"] == 0


def test_default_bluegate_api_url(monkeypatch):
    monkeypatch.delenv("BLUETHROAT_BLUEGATE_API_URL", raising=False)
    monkeypatch.setenv("BLUETHROAT_CONFIG_DIR", "/nonexistent-bluethroat-config")
    assert Config.load().bluegate_api_url == DEFAULT_BLUEGATE_API_URL
    assert DEFAULT_BLUEGATE_API_URL == "https://bluegate.bluethroatlabs.com"


def test_bluegate_api_url_is_separate_from_blueskills(monkeypatch, tmp_path):
    monkeypatch.setenv("BLUETHROAT_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"api_url": "https://file-skills.example", "bluegate_api_url": "https://file-gate.example/"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("BLUETHROAT_API_URL", "https://skills.example")
    monkeypatch.delenv("BLUETHROAT_BLUEGATE_API_URL", raising=False)
    loaded = Config.load()
    assert loaded.api_url == "https://skills.example"
    assert loaded.bluegate_api_url == "https://file-gate.example"
