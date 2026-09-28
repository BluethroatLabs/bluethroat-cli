"""``bluethroat`` command line.

``scan`` is synchronous: it submits a source, waits for the hosted report, and
prints it. Callers do not poll.
"""

from __future__ import annotations

import argparse
import sys

from bluethroat import __version__


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 4
    return handler(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bluethroat",
        description="Scan an agent skill with BlueSkills before installing it.",
    )
    parser.add_argument("--version", action="version", version=f"bluethroat {__version__}")
    commands = parser.add_subparsers(dest="command")

    auth = commands.add_parser("auth", help="Log in with the Bluethroat GitHub App.")
    auth_commands = auth.add_subparsers(dest="auth_command")
    login = auth_commands.add_parser("login", help="Authorize this client once via GitHub device flow.")
    login.set_defaults(handler=_not_ready)
    status = auth_commands.add_parser("status", help="Show the logged-in GitHub account.")
    status.set_defaults(handler=_not_ready)
    logout = auth_commands.add_parser("logout", help="Remove the local credential and revoke it.")
    logout.set_defaults(handler=_not_ready)
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
    scan.add_argument(
        "--ref",
        help="Branch, tag, or commit. Only valid for a repository URL.",
    )
    scan.add_argument(
        "--json",
        action="store_true",
        help="Print the hosted scan document as JSON. The default is a text report.",
    )
    scan.set_defaults(handler=_not_ready)
    blueskills.set_defaults(handler=_blueskills_help)
    return parser


def _auth_help(_args: argparse.Namespace) -> int:
    print("usage: bluethroat auth {login,status,logout}", file=sys.stderr)
    return 4


def _blueskills_help(_args: argparse.Namespace) -> int:
    print("usage: bluethroat blueskills scan <source> [--ref REF] [--json]", file=sys.stderr)
    return 4


def _not_ready(_args: argparse.Namespace) -> int:
    print("bluethroat: this command is not implemented yet", file=sys.stderr)
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
