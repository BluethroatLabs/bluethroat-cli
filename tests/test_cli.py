import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from bluethroat.api import _with_ref
from bluethroat.cli import main
from bluethroat.config import DEFAULT_GITHUB_CLIENT_ID, Config
from bluethroat.credentials import MemoryStore, Session, _file_load, _file_save
from bluethroat.report import render


def _session() -> Session:
    return Session(
        access_token="ghu_test",
        refresh_token="ghr_test",
        expires_at=10**12,
        refresh_expires_at=10**12,
        login="octocat",
        user_id=7,
    )


def test_default_github_client_id(monkeypatch):
    monkeypatch.delenv("BLUETHROAT_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.setenv("BLUETHROAT_CONFIG_DIR", "/nonexistent-bluethroat-config")
    assert Config.load().github_client_id == DEFAULT_GITHUB_CLIENT_ID
    assert DEFAULT_GITHUB_CLIENT_ID == "Iv23liH0YU6oTHHz0i8O"


def test_device_flow_login(monkeypatch, capsys):
    monkeypatch.setenv("BLUETHROAT_GITHUB_CLIENT_ID", "Iv1.test")
    steps = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/device/code":
            return httpx.Response(200, json={
                "device_code": "device",
                "user_code": "WDJB-MJHT",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            })
        if request.url.path == "/login/oauth/access_token":
            steps["token"] += 1
            if steps["token"] == 1:
                return httpx.Response(200, json={"error": "authorization_pending"})
            if steps["token"] == 2:
                return httpx.Response(200, json={"error": "slow_down"})
            return httpx.Response(200, json={
                "access_token": "ghu_new",
                "refresh_token": "ghr_new",
                "expires_in": 28800,
                "refresh_token_expires_in": 15897600,
            })
        if request.url.path == "/user":
            assert request.headers["authorization"] == "Bearer ghu_new"
            return httpx.Response(200, json={"login": "octocat", "id": 7})
        raise AssertionError(request.url.path)

    store = MemoryStore()
    opened: list[str] = []
    slept: list[float] = []
    code = main(
        ["auth", "login"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        opener=opened.append,
        sleep=slept.append,
        now=lambda: 1_000.0,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "logged in as octocat" in captured.out
    assert "WDJB-MJHT" in captured.err
    assert store.session.login == "octocat"
    assert store.session.user_id == 7
    assert store.session.access_token == "ghu_new"
    assert opened == ["https://github.com/login/device"]
    assert slept == [5, 5, 10]


def test_cancelled_login(monkeypatch):
    monkeypatch.setenv("BLUETHROAT_GITHUB_CLIENT_ID", "Iv1.test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/device/code"):
            return httpx.Response(200, json={
                "device_code": "device",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
                "interval": 1,
            })
        return httpx.Response(200, json={"error": "access_denied"})

    code = main(
        ["auth", "login"],
        store=MemoryStore(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        opener=lambda _url: None,
        sleep=lambda _seconds: None,
        now=lambda: 1_000.0,
    )
    assert code == 6


def test_scan_waits_and_sends_github_token(monkeypatch, capsys):
    monkeypatch.setenv("BLUETHROAT_GITHUB_CLIENT_ID", "Iv1.test")
    monkeypatch.setenv("BLUETHROAT_API_URL", "https://scanner.example")
    polls = {"n": 0}
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        if request.url.path == "/scan.json":
            body = parse_qs(request.content.decode())
            seen["repo_url"] = body["repo_url"][0]
            return httpx.Response(202, json={
                "scan_id": "abc123",
                "status": "queued_deep",
                "worst_verdict": "clean",
                "source": "https://github.com/owner/repository/tree/main",
            })
        polls["n"] += 1
        if polls["n"] == 1:
            return httpx.Response(404, json={"error": "unknown scan_id"})
        return httpx.Response(200, json={
            "scan_id": "abc123",
            "status": "done",
            "complete": True,
            "verdict": "malicious",
            "score": 86,
            "source": "https://github.com/owner/repository/tree/main",
            "commit": "abc1234def",
            "reports": [{
                "skill_name": "demo",
                "verdict": "malicious",
                "risk_score": 86,
                "layers_run": ["static", "ast"],
                "findings": [{
                    "severity": "high",
                    "title": "Credential file reaches a network sink",
                    "layer": "ast",
                    "file": "scripts/sync.py",
                    "line": 12,
                    "detail": "Script reads a credential path and performs network I/O.",
                    "evidence": "open(key)",
                }],
            }],
        })

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["blueskills", "scan", "https://github.com/owner/repository", "--ref", "main"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
        clock=lambda: 0.0,
    )
    captured = capsys.readouterr()
    assert code == 2
    assert seen["authorization"] == "Bearer ghu_test"
    assert seen["repo_url"] == "https://github.com/owner/repository/tree/main"
    assert polls["n"] == 2
    assert "MALICIOUS" in captured.out
    assert "Credential file reaches a network sink" in captured.out
    assert "Waiting for the report" in captured.err


def test_rate_limit_exits_without_a_verdict(monkeypatch, capsys):
    monkeypatch.setenv("BLUETHROAT_GITHUB_CLIENT_ID", "Iv1.test")
    monkeypatch.setenv("BLUETHROAT_API_URL", "https://scanner.example")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "12"},
            json={"error": "Not right now", "retry_after": 12},
        )

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["blueskills", "scan", "https://github.com/owner/repository", "--json"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    captured = capsys.readouterr()
    assert code == 5
    assert "retry after 12s" in captured.err
    assert json.loads(captured.out)["retry_after"] == 12


def test_scan_requires_login():
    code = main(["blueskills", "scan", "https://github.com/owner/repository"], store=MemoryStore())
    assert code == 6


def test_skill_md_and_zip(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUETHROAT_GITHUB_CLIENT_ID", "Iv1.test")
    monkeypatch.setenv("BLUETHROAT_API_URL", "https://scanner.example")
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nhello\n", encoding="utf-8")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/scan.json":
            raise AssertionError(request.url.path)
        content_type = request.headers["content-type"]
        if content_type.startswith("multipart/"):
            seen["zip"] = "yes"
        else:
            seen["text"] = parse_qs(request.content.decode())["skill_md"][0]
        return httpx.Response(200, json={
            "status": "done",
            "complete": True,
            "worst_verdict": "clean",
            "source": "pasted",
            "reports": [{
                "skill_name": "demo",
                "verdict": "clean",
                "risk_score": 0,
                "layers_run": ["static"],
                "findings": [],
            }],
        })

    store = MemoryStore()
    store.save(_session())
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert main(["blueskills", "scan", str(skill)], store=store, client=client) == 0
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "SKILL.md").write_text("name: demo\n", encoding="utf-8")
    assert main(["blueskills", "scan", str(package)], store=store, client=client) == 0
    assert "hello" in seen["text"]
    assert seen["zip"] == "yes"


def test_full_sha_mismatch_prints_no_verdict(monkeypatch, capsys):
    monkeypatch.setenv("BLUETHROAT_GITHUB_CLIENT_ID", "Iv1.test")
    monkeypatch.setenv("BLUETHROAT_API_URL", "https://scanner.example")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "status": "done",
            "complete": True,
            "verdict": "clean",
            "score": 0,
            "commit": "ffff",
            "reports": [],
        })

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["blueskills", "scan", "https://github.com/owner/repository", "--ref", "ab" * 20],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    captured = capsys.readouterr()
    assert code == 4
    assert captured.out == ""
    assert "different commit" in captured.err


def test_ref_is_rejected_for_a_local_path(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUETHROAT_GITHUB_CLIENT_ID", "Iv1.test")
    skill = tmp_path / "SKILL.md"
    skill.write_text("hello", encoding="utf-8")
    store = MemoryStore()
    store.save(_session())
    code = main(["blueskills", "scan", str(skill), "--ref", "main"], store=store)
    assert code == 4


def test_credentials_file_is_private(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUETHROAT_CONFIG_DIR", str(tmp_path))
    _file_save(_session().to_json())
    path = tmp_path / "credentials.json"
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = _file_load()
    assert loaded is not None
    assert loaded.login == "octocat"


def test_text_report_leads_with_the_verdict():
    text = render({
        "source": "https://github.com/owner/repository",
        "commit": "abc1234",
        "worst_verdict": "malicious",
        "reports": [{
            "skill_name": "demo",
            "verdict": "malicious",
            "risk_score": 86,
            "layers_run": ["ast"],
            "findings": [{
                "severity": "high",
                "title": "Credential file reaches a network sink",
                "layer": "ast",
                "file": "scripts/sync.py",
                "line": 12,
                "detail": "Script reads a credential path.",
                "evidence": "open(key)",
            }],
        }],
    })
    assert text.splitlines()[0].endswith("MALICIOUS (risk 86/100)")
    assert "evidence: open(key)" in text


def test_repository_ref_rewrite():
    assert _with_ref("https://github.com/owner/repository", "main") == (
        "https://github.com/owner/repository/tree/main"
    )
    assert _with_ref("https://gitlab.com/owner/repository.git", "v1") == (
        "https://gitlab.com/owner/repository/-/tree/v1"
    )


def _no_local_secret(monkeypatch) -> None:
    monkeypatch.setenv("BLUETHROAT_GITHUB_CLIENT_ID", "Iv1.test")
    monkeypatch.delenv("BLUETHROAT_GITHUB_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("BLUETHROAT_CONFIG_DIR", "/nonexistent-bluethroat-config")
    monkeypatch.setenv("BLUETHROAT_API_URL", "https://scanner.example")


def test_refresh_does_not_need_a_client_secret(monkeypatch):
    _no_local_secret(monkeypatch)
    seen: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            seen.update(parse_qs(request.content.decode()))
            return httpx.Response(200, json={
                "access_token": "ghu_fresh",
                "refresh_token": "ghr_fresh",
                "expires_in": 28800,
                "refresh_token_expires_in": 15897600,
            })
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "octocat", "id": 7})
        raise AssertionError(request.url.path)

    store = MemoryStore()
    stale = _session()
    stale.expires_at = 0
    store.save(stale)
    code = main(
        ["auth", "status"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: 1_000.0,
    )
    assert code == 0
    assert "client_secret" not in seen
    assert seen["grant_type"] == ["refresh_token"]
    assert seen["refresh_token"] == ["ghr_test"]
    assert store.session.access_token == "ghu_fresh"


def test_logout_revokes_through_blueskills(monkeypatch, capsys):
    _no_local_secret(monkeypatch)
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers["authorization"]))
        return httpx.Response(204)

    store = MemoryStore()
    store.save(_session())
    code = main(
        ["auth", "logout"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert code == 0
    assert store.session is None
    assert seen == [("POST", "https://scanner.example/auth/github/revoke", "Bearer ghu_test")]
    assert "logged out" in capsys.readouterr().out


def test_logout_reports_a_failed_revoke(monkeypatch, capsys):
    _no_local_secret(monkeypatch)
    store = MemoryStore()
    store.save(_session())
    code = main(
        ["auth", "logout"],
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(503, json={}))),
    )
    assert code != 0
    assert store.session is None
    assert "GitHub Settings" in capsys.readouterr().err
