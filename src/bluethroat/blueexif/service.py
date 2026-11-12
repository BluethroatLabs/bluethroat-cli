"""Safe file orchestration for BlueExif operations."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from bluethroat.blueexif.backends import backend_for
from bluethroat.blueexif.model import Edit, coerce_for_field, index_fields, parse_assignments
from bluethroat.errors import INVALID, CliError


def scan_file(source: str | Path) -> tuple[Path, dict[str, Any]]:
    path = _source_file(Path(source).expanduser())
    try:
        return path, backend_for(path).scan(path)
    except CliError:
        raise
    except Exception as exc:
        raise CliError(f"Could not scan {path.name}: {exc}", INVALID) from exc


def replace_file(
    source: str | Path,
    assignments: list[str],
    *,
    output: str | Path | None = None,
    force: bool = False,
) -> tuple[Path, dict[str, Any]]:
    path, document = scan_file(source)
    if not document.get("canWrite"):
        raise CliError(f"{path.name} is read-only in the Python BlueExif backend.", INVALID)
    requested = parse_assignments(assignments)
    if not requested:
        raise CliError("At least one FIELD=JSON replacement is required.", INVALID)
    indexed = index_fields(document)
    edits: dict[str, Edit] = {}
    for field_id, edit in requested.items():
        spec = indexed.get(field_id)
        if spec is None:
            raise CliError(f"Unknown metadata field: {field_id}. Run scan first.", INVALID)
        if not spec.get("editable"):
            raise CliError(f"{field_id} is read-only.", INVALID)
        edits[field_id] = Edit("set", coerce_for_field(spec, edit.value))
    destination = _destination(path, output)
    _mutate(path, destination, edits, force=force, strip_all=False)
    return destination, backend_for(destination).scan(destination)


def remove_file(
    source: str | Path,
    fields: list[str],
    *,
    remove_all: bool,
    output: str | Path | None = None,
    force: bool = False,
) -> tuple[Path, dict[str, Any]]:
    path, document = scan_file(source)
    if remove_all:
        if fields:
            raise CliError("Do not combine field ids with --all.", INVALID)
        if not document.get("canStrip"):
            raise CliError(f"{path.name} cannot be stripped by the Python BlueExif backend.", INVALID)
        edits: dict[str, Edit] = {}
    else:
        if not fields:
            raise CliError("Provide one or more field ids, or pass --all.", INVALID)
        if not document.get("canWrite"):
            raise CliError(f"{path.name} is read-only in the Python BlueExif backend.", INVALID)
        indexed = index_fields(document)
        removable_groups = {
            f"@group:{item['id']}"
            for item in document.get("groups", [])
            if isinstance(item, dict) and item.get("removable")
        }
        edits = {}
        for field_id in fields:
            spec = indexed.get(field_id)
            if spec is None and field_id not in removable_groups:
                raise CliError(f"Unknown metadata field: {field_id}. Run scan first.", INVALID)
            if spec is not None and not spec.get("removable"):
                raise CliError(f"{field_id} cannot be removed.", INVALID)
            edits[field_id] = Edit("remove")
    destination = _destination(path, output)
    _mutate(path, destination, edits, force=force, strip_all=remove_all)
    return destination, backend_for(destination).scan(destination)


def default_output(source: Path) -> Path:
    return source.with_name(f"{source.stem}-blueexif{source.suffix}")


def _destination(source: Path, output: str | Path | None) -> Path:
    return Path(output).expanduser() if output is not None else default_output(source)


def _mutate(
    source: Path,
    destination: Path,
    edits: dict[str, Edit],
    *,
    force: bool,
    strip_all: bool,
) -> None:
    _check_output(source, destination, force=force)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=destination.suffix,
        dir=destination.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.unlink()
        backend_for(source).mutate(source, temporary, edits, strip_all=strip_all)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise CliError("The metadata writer produced an empty file.", INVALID)
        os.replace(temporary, destination)
    except CliError:
        raise
    except OSError as exc:
        raise CliError(f"Could not write {destination} ({exc.__class__.__name__}).", INVALID) from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _check_output(source: Path, destination: Path, *, force: bool) -> None:
    if destination.is_symlink():
        raise CliError(f"{destination} is a symlink and cannot be replaced.", INVALID)
    try:
        if source.resolve() == destination.resolve():
            raise CliError("The output path must be different from the source file.", INVALID)
    except OSError as exc:
        raise CliError(f"Could not resolve the output path: {exc}", INVALID) from exc
    if destination.exists() and not force:
        raise CliError(f"{destination} already exists. Pass --force to replace it.", INVALID)
    if not destination.parent.is_dir():
        raise CliError(f"Output directory does not exist: {destination.parent}", INVALID)


def _source_file(path: Path) -> Path:
    if path.is_symlink():
        raise CliError(f"{path} is a symlink and cannot be processed.", INVALID)
    if not path.exists():
        raise CliError(f"No such file: {path}", INVALID)
    if not path.is_file():
        raise CliError(f"Not a file: {path}", INVALID)
    return path
