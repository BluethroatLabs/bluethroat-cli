"""``bluethroat`` command line."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

import httpx

from bluethroat import __version__
from bluethroat.api import BlueSkillsClient
from bluethroat.blueexif import (
    json_safe as blueexif_json_safe,
    remove_file as blueexif_remove_file,
    render as render_blueexif,
    replace_file as blueexif_replace_file,
    scan_file as blueexif_scan_file,
)
from bluethroat.bluegate import BlueGateClient
from bluethroat.bluepaper import (
    BluePaperClient,
    check_output,
    default_output,
    render as render_bluepaper,
    write_pdf,
)
from bluethroat.config import Config
from bluethroat.credentials import CredentialStore, SessionStore
from bluethroat.errors import AUTH, CLEAN, INVALID, SERVICE, SUSPICIOUS, CliError, code_for
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
        description="Use BlueSkills, BlueGate, BluePaper, and BlueExif from one CLI.",
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

    bluepaper = commands.add_parser("bluepaper", help="BluePaper document sanitization.")
    bluepaper_commands = bluepaper.add_subparsers(dest="bluepaper_command")
    convert = bluepaper_commands.add_parser(
        "convert",
        help="Rebuild a document as a safe PDF.",
    )
    convert.add_argument("source", help="Document to upload.")
    convert.add_argument(
        "-o",
        "--output",
        help="Output PDF path. Defaults to <source stem>-safe.pdf.",
    )
    convert.add_argument("--ocr-lang", help="Tesseract OCR language code, for example eng.")
    convert.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output file (never follows symlinks).",
    )
    convert.add_argument(
        "--json",
        action="store_true",
        help="Print conversion status and the byte-indicator report as JSON.",
    )
    convert.set_defaults(handler=_bluepaper_convert)
    bluepaper.set_defaults(handler=_bluepaper_help)

    blueexif = commands.add_parser("blueexif", help="Inspect and edit local file metadata.")
    blueexif_commands = blueexif.add_subparsers(dest="blueexif_command")
    exif_scan = blueexif_commands.add_parser(
        "scan",
        help="Scan a local file and print its metadata.",
    )
    exif_scan.add_argument("source", help="File to inspect.")
    exif_scan.add_argument(
        "--json",
        action="store_true",
        help="Print the BlueExif metadata document as JSON.",
    )
    exif_scan.set_defaults(handler=_blueexif_scan)

    replace = blueexif_commands.add_parser(
        "replace",
        help="Replace metadata fields and write a safe copy.",
    )
    replace.add_argument("source", help="File whose metadata should be changed.")
    replace.add_argument(
        "replacements",
        nargs="+",
        metavar="FIELD=JSON",
        help='Field assignment from scan output, for example \'pdf:Author="Anonymous"\'.',
    )
    _add_blueexif_output(replace)
    replace.set_defaults(handler=_blueexif_replace)

    remove = blueexif_commands.add_parser(
        "remove",
        help="Remove selected metadata fields or strip all metadata.",
    )
    remove.add_argument("source", help="File whose metadata should be removed.")
    remove.add_argument("fields", nargs="*", metavar="FIELD", help="Field ids from scan output.")
    remove.add_argument(
        "--all",
        action="store_true",
        dest="remove_all",
        help="Strip all nonessential metadata while preserving orientation and colour profiles.",
    )
    _add_blueexif_output(remove)
    remove.set_defaults(handler=_blueexif_remove)
    blueexif.set_defaults(handler=_blueexif_help)
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


def _add_blueexif_output(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "-o",
        "--output",
        help="Output path. Defaults to <source stem>-blueexif<source suffix>.",
    )
    command.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output file (never follows symlinks).",
    )


def _auth_help(_args: argparse.Namespace, **_kwargs: object) -> int:
    print("usage: bluethroat auth {login,status,logout}", file=sys.stderr)
    return INVALID


def _blueskills_help(_args: argparse.Namespace, **_kwargs: object) -> int:
    print("usage: bluethroat blueskills scan <source> [--ref REF] [--json]", file=sys.stderr)
    return INVALID


def _bluegate_help(_args: argparse.Namespace, **_kwargs: object) -> int:
    print("usage: bluethroat bluegate scan <repo> [--ref REF] [--json] [--agent]", file=sys.stderr)
    return INVALID


def _bluepaper_help(_args: argparse.Namespace, **_kwargs: object) -> int:
    print(
        "usage: bluethroat bluepaper convert <document> "
        "[-o OUTPUT] [--ocr-lang LANG] [--force] [--json]",
        file=sys.stderr,
    )
    return INVALID


def _blueexif_help(_args: argparse.Namespace, **_kwargs: object) -> int:
    print("usage: bluethroat blueexif {scan,replace,remove} ...", file=sys.stderr)
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


def _bluepaper_convert(
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

    source = Path(args.source).expanduser()
    output = Path(args.output).expanduser() if args.output else default_output(source)
    if source.resolve() == output.resolve():
        raise CliError("The output path must be different from the source document.", INVALID)
    check_output(output, force=args.force)
    result = BluePaperClient(
        http,
        Config.load().bluepaper_api_url,
        session,
        sleep=kwargs.get("sleep"),  # type: ignore[arg-type]
        clock=kwargs.get("clock"),  # type: ignore[arg-type]
    ).convert(source, ocr_lang=args.ocr_lang)

    written: Path | None = None
    if result.pdf is not None:
        write_pdf(output, result.pdf, force=args.force)
        written = output
    if args.json:
        json.dump(result.document(written), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(render_bluepaper(result, written))

    if str(result.conversion.get("status") or "") != "succeeded":
        return SERVICE
    hits = result.report.get("hits", []) if result.report is not None else []
    return SUSPICIOUS if isinstance(hits, list) and hits else CLEAN


def _blueexif_scan(args: argparse.Namespace, **_kwargs: object) -> int:
    source, document = blueexif_scan_file(args.source)
    if args.json:
        json.dump(blueexif_json_safe(document), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(render_blueexif(document, file_name=source.name))
    return CLEAN


def _blueexif_replace(args: argparse.Namespace, **_kwargs: object) -> int:
    output, document = blueexif_replace_file(
        args.source,
        args.replacements,
        output=args.output,
        force=args.force,
    )
    print(f"Wrote: {output}")
    print(render_blueexif(document, file_name=output.name))
    return CLEAN


def _blueexif_remove(args: argparse.Namespace, **_kwargs: object) -> int:
    output, document = blueexif_remove_file(
        args.source,
        args.fields,
        remove_all=args.remove_all,
        output=args.output,
        force=args.force,
    )
    print(f"Wrote: {output}")
    print(render_blueexif(document, file_name=output.name))
    return CLEAN


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
