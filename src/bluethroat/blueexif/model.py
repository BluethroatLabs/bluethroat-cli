"""BlueExif-compatible metadata documents and rendering helpers."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

from bluethroat.errors import INVALID, CliError

FieldValue = str | int | float | list[str] | dict[str, Any] | None


@dataclass(frozen=True)
class Edit:
    op: str
    value: FieldValue = None


def field(
    field_id: str,
    label: str,
    kind: str,
    value: FieldValue,
    *,
    tag: str | None = None,
    sensitivity: str | None = None,
    editable: bool = True,
    removable: bool = True,
    display: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": field_id,
        "label": label,
        "kind": kind,
        "value": value,
        "editable": editable,
        "removable": removable,
    }
    for key, item in (
        ("tag", tag),
        ("sensitivity", sensitivity),
        ("display", display),
    ):
        if item is not None:
            result[key] = item
    return result


def group(
    group_id: str,
    label: str,
    fields: list[dict[str, Any]],
    *,
    source: str | None = None,
    removable: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": group_id,
        "label": label,
        "fields": fields,
    }
    if source is not None:
        result["source"] = source
    if removable:
        result["removable"] = True
    return result


def metadata(
    format_id: str,
    name: str,
    mime: str,
    category: str,
    *,
    info: list[dict[str, str]] | None = None,
    groups: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    can_write: bool = False,
    can_strip: bool = False,
) -> dict[str, Any]:
    return {
        "format": {
            "id": format_id,
            "name": name,
            "mime": mime,
            "category": category,
        },
        "info": info or [],
        "groups": groups or [],
        "warnings": warnings or [],
        "canWrite": can_write,
        "canStrip": can_strip,
    }


def info(label: str, value: object) -> dict[str, str]:
    return {"label": label, "value": str(value)}


def humanize(name: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return text.replace("_", " ").replace("-", " ").strip().title()


def parse_assignments(items: list[str]) -> dict[str, Edit]:
    edits: dict[str, Edit] = {}
    for item in items:
        if "=" not in item:
            raise CliError(
                f"Invalid replacement {item!r}; expected FIELD=JSON.",
                INVALID,
            )
        field_id, encoded = item.split("=", 1)
        field_id = field_id.strip()
        if not field_id:
            raise CliError("A replacement field id cannot be empty.", INVALID)
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise CliError(
                f"Invalid JSON for {field_id}: {exc.msg}. "
                "Quote string values, for example 'id=\"value\"'.",
                INVALID,
            ) from exc
        if value is not None and not isinstance(value, (str, int, float, list, dict)):
            raise CliError(f"Unsupported JSON value for {field_id}.", INVALID)
        edits[field_id] = Edit("set", value)
    return edits


def index_fields(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["id"]): item
        for container in document.get("groups", [])
        for item in container.get("fields", [])
        if isinstance(item, dict) and item.get("id")
    }


def coerce_for_field(spec: dict[str, Any], value: FieldValue) -> FieldValue:
    kind = spec.get("kind")
    field_id = spec.get("id", "field")
    if kind in {"text", "multiline", "datetime", "enum"}:
        if not isinstance(value, str):
            raise CliError(f"{field_id} requires a JSON string.", INVALID)
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise CliError(f"{field_id} requires a JSON integer.", INVALID)
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CliError(f"{field_id} requires a JSON number.", INVALID)
    elif kind == "list":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise CliError(f"{field_id} requires an array of strings.", INVALID)
    elif kind == "gps":
        if not isinstance(value, dict) or not isinstance(value.get("lat"), (int, float)) or not isinstance(
            value.get("lon"), (int, float)
        ):
            raise CliError(f"{field_id} requires {{\"lat\": number, \"lon\": number}}.", INVALID)
        if abs(float(value["lat"])) > 90 or abs(float(value["lon"])) > 180:
            raise CliError(f"{field_id} contains invalid GPS coordinates.", INVALID)
    elif kind in {"image", "raw"}:
        raise CliError(f"{field_id} cannot be replaced by this CLI.", INVALID)
    return value


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return value


def render(document: dict[str, Any], *, file_name: str | None = None) -> str:
    fmt = document["format"]
    title = f"BlueExif — {fmt['name']}"
    if file_name:
        title += f" — {file_name}"
    lines = [title]
    for item in document.get("info", []):
        lines.append(f"{item['label']}: {item['value']}")
    groups = document.get("groups", [])
    if not groups:
        lines.append("No metadata fields found.")
    for container in groups:
        lines.append("")
        lines.append(f"[{container['label']}]")
        for item in container.get("fields", []):
            value = item.get("display")
            if value is None:
                value = json.dumps(json_safe(item.get("value")), ensure_ascii=False)
            lines.append(f"{item['id']} = {value}")
    for warning in document.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)
