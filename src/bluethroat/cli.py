"""``bluethroat`` command line.

``blueskills scan`` submits a skill, waits for the hosted report, and prints it.
``clone`` and ``bluegate scan`` do the same for a GitHub repository on BlueGate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable

import httpx

from bluethroat import __version__
from bluethroat.api import BlueSkillsClient
from bluethroat.bluegate import BlueGateClient
from bluethroat.config import Config
from bluethroat.credentials import CredentialStore, SessionStore
from bluethroat.errors import AUTH, INVALID, SERVICE, CliError, code_for
from bluethroat.github_auth import GitHubAuth
from bluethroat.report import _commit, outcome, render


def main(
    argv: list[str] | None = None,
    *,
    store: SessionStore | None = None,
    client: httpx.Client | None = None,
    opener: Callable[[str], object] | None = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], float] | None = None,
    clock: Callable[[], float] | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return INVALID
    owns_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(10.0, read=120.0))
    try:
        return handler(
            args,
            store=store or CredentialStore(),
            http=http,
            opener=opener,
            sleep=sleep,
            now=now,
            clock=clock,
        )
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        if getattr(args, "json", False) and exc.body is not None:
            json.dump(exc.body, sys.stdout)
            sys.stdout.write("\n")
        return exc.code
    finally:
        if owns_client:
            http.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bluethroat",
        description="Scan an agent skill with BlueSkills, or a GitHub repository with BlueGate.",
    )
    parser.add_argument("--version", action="version", version=f"bluethroat {__version__}")
    commands = parser.add_subparsers(dest="command")

    auth = commands.add_parser("auth", help="Log in with the Bluethroat GitHub App.")
    auth_commands = auth.add_subparsers(dest="auth_command")
    login = auth_commands.add_parser("login", help="Authorize this client once via GitHub device flow.")
    login.set_defaults(handler=_login)
    status = auth_commands.add_parser("status", help="Show the logged-in GitHub account.")
    status.set_defaults(handler=_status)
    logout = auth_commands.add_parser("logout", help="Remove the local credential and revoke it.")
    logout.set_defaults(handler=_logout)
    auth.set_defaults(handler=_auth_help)

    blueskills = commands.add_parser("blueskills", help="BlueSkills scans.")
    blueskills_commands = blueskills.add_subparsers(dest="blueskills_command")
    scan = blueskills_commands.add_parser(
        "scan",
        help="Scan a public repository, directory, zip, or SKILL.md and print the report.",
    )
    scan.add_argument(
        "source",
        help="Public GitHub or GitLab URL, a skill directory, a .zip, or a SKILL.md file.",
    )
    scan.add_argument("--ref", help="Branch, tag, or commit. Only valid for a repository URL.")
    scan.add_argument(
        "--json",
        action="store_true",
        help="Print the hosted scan document as JSON. The default is a text report.",
    )
    scan.set_defaults(handler=_scan)
    blueskills.set_defaults(handler=_blueskills_help)

    _add_bluegate_scan(
        commands,
        "clone",
        help="Scan a public GitHub repository with BlueGate (same as bluegate scan).",
    )
    bluegate = commands.add_parser("bluegate", help="BlueGate scans.")
    bluegate_commands = bluegate.add_subparsers(dest="bluegate_command")
    _add_bluegate_scan(
        bluegate_commands,
        "scan",
        help="Scan a public GitHub repository with BlueGate and print the report.",
    )
    bluegate.set_defaults(handler=_bluegate_help)
    return parser


def _add_bluegate_scan(commands: argparse._SubParsersAction, name: str, *, help: str) -> None:
    scan = commands.add_parser(name, help=help)
    scan.add_argument(
        "repo",
        help="Public GitHub repository: https://github.com/owner/repo, github.com/owner/repo, or owner/repo.",
    )
    scan.add_argument("--ref", help="Branch, tag, or commit.")
    output = scan.add_mutually_exclusive_group()
    output.add_argument(
        "--json",
        action="store_true",
        help="Print the hosted scan document as JSON. The default is the text report.",
    )
    output.add_argument(
        "--agent",
        action="store_true",
        help="Print the agent-facing report: verdict and commit first, repository text fenced.",
    )
    scan.set_defaults(handler=_bluegate_scan)


def _auth_help(_args: argparse.Namespace, **_kwargs: object) -> int:
    print("usage: bluethroat auth {login,status,logout}", file=sys.stderr)
    return INVALID


def _blueskills_help(_args: argparse.Namespace, **_kwargs: object) -> int:
    print("usage: bluethroat blueskills scan <source> [--ref REF] [--json]", file=sys.stderr)
    return INVALID


def _bluegate_help(_args: argparse.Namespace, **_kwargs: object) -> int:
    print("usage: bluethroat bluegate scan <repo> [--ref REF] [--json] [--agent]", file=sys.stderr)
    return INVALID


def _github(http: httpx.Client, **kwargs: object) -> GitHubAuth:
    config = Config.load()
    return GitHubAuth(
        http,
        config.github_client_id,
        client_secret=config.github_client_secret,
        opener=kwargs.get("opener"),  # type: ignore[arg-type]
        sleep=kwargs.get("sleep"),  # type: ignore[arg-type]
        now=kwargs.get("now"),  # type: ignore[arg-type]
    )


def _login(_args: argparse.Namespace, *, store: SessionStore, http: httpx.Client, **kwargs: object) -> int:
    session = _github(http, **kwargs).login()
    store.save(session)
    print(f"logged in as {session.login}")
    return 0


def _status(_args: argparse.Namespace, *, store: SessionStore, http: httpx.Client, **kwargs: object) -> int:
    session = store.load()
    if session is None:
        raise CliError("not logged in", AUTH)
    session = _github(http, **kwargs).ensure(session)
    store.save(session)
    print(f"logged in as {session.login} (id {session.user_id})")
    return 0


def _logout(_args: argparse.Namespace, *, store: SessionStore, http: httpx.Client, **kwargs: object) -> int:
    session = store.load()
    store.clear()
    if session is None:
        print("logged out")
        return 0
    config = Config.load()
    if config.github_client_secret:
        _github(http, **kwargs).revoke(session)
    else:
        BlueSkillsClient(http, config.api_url, session).revoke()
    print("logged out")
    return 0


def _scan(
    args: argparse.Namespace,
    *,
    store: SessionStore,
    http: httpx.Client,
    **kwargs: object,
) -> int:
    session = store.load()
    if session is None:
        raise CliError("not logged in. Run bluethroat auth login.", AUTH)
    auth = _github(http, **kwargs)
    session = auth.ensure(session)
    store.save(session)
    doc = BlueSkillsClient(
        http,
        Config.load().api_url,
        session,
        sleep=kwargs.get("sleep"),  # type: ignore[arg-type]
        clock=kwargs.get("clock"),  # type: ignore[arg-type]
    ).scan(args.source, ref=args.ref)
    _check_ref(args.ref, _commit(doc))
    verdict, complete = outcome(doc)
    if args.json:
        json.dump(doc, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(render(doc))
    if doc.get("status") == "failed":
        return SERVICE
    return code_for(verdict, complete=complete)


def _bluegate_scan(
    args: argparse.Namespace,
    *,
    store: SessionStore,
    http: httpx.Client,
    **kwargs: object,
) -> int:
    session = store.load()
    if session is None:
        raise CliError("not logged in. Run bluethroat auth login.", AUTH)
    auth = _github(http, **kwargs)
    session = auth.ensure(session)
    store.save(session)
    client = BlueGateClient(
        http,
        Config.load().bluegate_api_url,
        session,
        sleep=kwargs.get("sleep"),  # type: ignore[arg-type]
        clock=kwargs.get("clock"),  # type: ignore[arg-type]
    )
    doc = client.scan(args.repo, ref=args.ref)
    _check_ref(args.ref, _commit(doc))
    verdict, complete = outcome(doc)
    if args.json:
        json.dump(doc, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        text = client.report(str(doc.get("scan_id") or ""), fmt="agent" if args.agent else "text")
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
    if doc.get("status") == "failed":
        return SERVICE
    return code_for(verdict, complete=complete)


def _check_ref(ref: str | None, served: str) -> None:
    if not ref or len(ref) != 40 or any(char not in "0123456789abcdefABCDEF" for char in ref):
        return
    wanted = ref.lower()
    got = served.lower()
    if got and (wanted.startswith(got) or got.startswith(wanted)):
        return
    raise CliError(
        "The host served a different commit than --ref. No verdict.",
        INVALID,
    )


if __name__ == "__main__":
    raise SystemExit(main())
