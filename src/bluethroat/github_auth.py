"""GitHub App device flow.

The device grant sends only the public client ID. The client secret is not
part of that request. A secret is used only to refresh or revoke a token, and
only when the operator has set one. A distributed CLI cannot keep that secret.

See GitHub's "Generating a user access token for a GitHub App".
"""

from __future__ import annotations

import json
import time
import webbrowser
from collections.abc import Callable
from typing import Any

import httpx

from bluethroat.credentials import Session
from bluethroat.errors import AUTH, SERVICE, CliError

GITHUB = "https://github.com"
API = "https://api.github.com"
_API_VERSION = "2026-03-10"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class GitHubAuth:
    def __init__(
        self,
        client: httpx.Client,
        client_id: str,
        *,
        client_secret: str = "",
        opener: Callable[[str], object] | None = None,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        if not client_id:
            raise CliError(
                "Set BLUETHROAT_GITHUB_CLIENT_ID to the GitHub App client ID.",
                AUTH,
            )
        self._http = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._open = opener or webbrowser.open
        self._sleep = sleep or time.sleep
        self._now = now or time.time

    def login(self) -> Session:
        """Run the device flow and return the session for the authorizing user."""
        pending = self._post_form(
            f"{GITHUB}/login/device/code",
            {"client_id": self._client_id},
        )
        if pending.get("error"):
            raise CliError(f"GitHub login failed ({pending['error']}).", AUTH)
        device_code = str(pending.get("device_code") or "")
        user_code = str(pending.get("user_code") or "")
        if not device_code or not user_code:
            raise CliError("GitHub did not return a device code.", SERVICE)
        uri = str(pending.get("verification_uri") or f"{GITHUB}/login/device")
        complete = pending.get("verification_uri_complete")
        print(f"Open {uri}", file=_err(), flush=True)
        print(f"Enter code {user_code}", file=_err(), flush=True)
        self._open(str(complete or uri))

        interval = max(1, int(pending.get("interval") or 5))
        deadline = self._now() + int(pending.get("expires_in") or 900)
        token = self._poll(device_code, interval, deadline)
        identity = self.identity(str(token["access_token"]))
        now = self._now()
        return Session(
            access_token=str(token["access_token"]),
            refresh_token=str(token.get("refresh_token") or ""),
            expires_at=now + int(token.get("expires_in") or 0),
            refresh_expires_at=now + int(token.get("refresh_token_expires_in") or 0),
            login=str(identity.get("login") or ""),
            user_id=int(identity.get("id") or 0),
        )

    def identity(self, access_token: str) -> dict[str, Any]:
        response = self._http.get(
            f"{API}/user",
            headers=self._headers(access_token),
        )
        if response.status_code != 200:
            raise CliError("GitHub rejected the new token.", AUTH)
        body = response.json()
        if not isinstance(body, dict) or "id" not in body:
            raise CliError("GitHub did not identify the authorizing user.", AUTH)
        return body

    def refresh(self, session: Session) -> Session:
        """Exchange a refresh token. Requires the GitHub App client secret."""
        if not self._client_secret:
            raise CliError("The GitHub session expired. Run bluethroat auth login.", AUTH)
        if not session.refresh_token:
            raise CliError("The GitHub session expired. Run bluethroat auth login.", AUTH)
        if session.refresh_expires_at and self._now() >= session.refresh_expires_at:
            raise CliError("The GitHub session expired. Run bluethroat auth login.", AUTH)
        token = self._post_form(
            f"{GITHUB}/login/oauth/access_token",
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": session.refresh_token,
            },
        )
        if token.get("error"):
            raise CliError("The GitHub session expired. Run bluethroat auth login.", AUTH)
        identity = self.identity(str(token["access_token"]))
        now = self._now()
        return Session(
            access_token=str(token["access_token"]),
            refresh_token=str(token.get("refresh_token") or session.refresh_token),
            expires_at=now + int(token.get("expires_in") or 0),
            refresh_expires_at=now + int(token.get("refresh_token_expires_in") or 0),
            login=str(identity.get("login") or session.login),
            user_id=int(identity.get("id") or session.user_id),
        )

    def revoke(self, session: Session) -> None:
        """Revoke the user token when a client secret is configured.

        GitHub's revocation endpoint authenticates with the app's client id and
        secret. Without the secret the local session is still discarded.
        """
        if not self._client_secret:
            return
        for token in (session.access_token, session.refresh_token):
            if not token:
                continue
            response = self._http.request(
                "DELETE",
                f"{API}/applications/{self._client_id}/token",
                auth=(self._client_id, self._client_secret),
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": _API_VERSION,
                },
                json={"access_token": token},
            )
            if response.status_code not in (204, 404):
                raise CliError(
                    "The local session was removed, but GitHub did not revoke the token. "
                    "Revoke Bluethroat under GitHub Settings → Applications.",
                    SERVICE,
                )

    def ensure(self, session: Session) -> Session:
        """Return a session whose access token is still valid."""
        if self._now() + 30 < session.expires_at:
            return session
        return self.refresh(session)

    def _poll(self, device_code: str, interval: int, deadline: float) -> dict[str, Any]:
        while self._now() < deadline:
            self._sleep(interval)
            body = self._post_form(
                f"{GITHUB}/login/oauth/access_token",
                {
                    "client_id": self._client_id,
                    "device_code": device_code,
                    "grant_type": _DEVICE_GRANT,
                },
            )
            error = str(body.get("error") or "")
            if not error and body.get("access_token"):
                return body
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error == "access_denied":
                raise CliError("GitHub authorization was cancelled.", AUTH)
            if error in {"expired_token", "token_expired"}:
                break
            raise CliError(f"GitHub login failed ({error or 'unknown error'}).", AUTH)
        raise CliError("The GitHub code expired. Run bluethroat auth login again.", AUTH)

    def _post_form(self, url: str, fields: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._http.post(
                url,
                data=fields,
                headers={"Accept": "application/json", "User-Agent": "bluethroat"},
            )
        except httpx.HTTPError as exc:
            raise CliError(f"Could not reach GitHub ({exc.__class__.__name__}).", SERVICE) from exc
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = None
        if not isinstance(body, dict):
            raise CliError(f"GitHub returned HTTP {response.status_code}.", SERVICE)
        if response.status_code >= 400 and not body.get("error"):
            raise CliError(f"GitHub returned HTTP {response.status_code}.", SERVICE)
        return body

    @staticmethod
    def _headers(access_token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "bluethroat",
        }


def _err():
    import sys

    return sys.stderr
