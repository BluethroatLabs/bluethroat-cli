"""Process configuration. Environment variables override the config file."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Front Door in front of the API. blueskills.bluethroatlabs.com is the website
# (Vercel) and answers POST /scan.json with an HTML 404.
DEFAULT_API_URL = "https://blueskills-web-hzdtf5buftehbya8.z03.azurefd.net"
# BlueGate serves the site and the API from one origin, so this is both.
DEFAULT_BLUEGATE_API_URL = "https://bluegate.bluethroatlabs.com"
# BluePaper likewise serves its public site and conversion API from one origin.
DEFAULT_BLUEPAPER_API_URL = "https://bluepaper.bluethroatlabs.com"
# Public GitHub App client ID. The client secret stays out of the distributed CLI.
DEFAULT_GITHUB_CLIENT_ID = "Iv23liH0YU6oTHHz0i8O"


def config_dir() -> Path:
    override = (os.environ.get("BLUETHROAT_CONFIG_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "bluethroat"


def _file_values() -> dict[str, str]:
    path = config_dir() / "config.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in (
        "github_client_id",
        "github_client_secret",
        "api_url",
        "bluegate_api_url",
        "bluepaper_api_url",
    ):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def _pick(env: str, file_key: str, default: str = "") -> str:
    direct = (os.environ.get(env) or "").strip()
    if direct:
        return direct
    return _file_values().get(file_key, default)


@dataclass(frozen=True)
class Config:
    github_client_id: str
    github_client_secret: str
    api_url: str
    bluegate_api_url: str
    bluepaper_api_url: str

    @classmethod
    def load(cls) -> Config:
        return cls(
            github_client_id=_pick(
                "BLUETHROAT_GITHUB_CLIENT_ID",
                "github_client_id",
                DEFAULT_GITHUB_CLIENT_ID,
            ),
            github_client_secret=_pick("BLUETHROAT_GITHUB_CLIENT_SECRET", "github_client_secret"),
            api_url=_pick("BLUETHROAT_API_URL", "api_url", DEFAULT_API_URL).rstrip("/"),
            bluegate_api_url=_pick(
                "BLUETHROAT_BLUEGATE_API_URL",
                "bluegate_api_url",
                DEFAULT_BLUEGATE_API_URL,
            ).rstrip("/"),
            bluepaper_api_url=_pick(
                "BLUETHROAT_BLUEPAPER_API_URL",
                "bluepaper_api_url",
                DEFAULT_BLUEPAPER_API_URL,
            ).rstrip("/"),
        )
