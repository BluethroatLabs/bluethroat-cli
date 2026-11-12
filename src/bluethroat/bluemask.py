"""Local image masking for the BlueMask command."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError

from bluethroat.errors import INVALID, CliError


FILL = (110, 113, 118)
COSMETIC_RADIUS = 10
MAX_FILE_BYTES = 35 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_SIDE = 12_000
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}


def default_output(source: Path) -> Path:
    """Return the default fresh-PNG destination for ``source``."""

    return source.with_name(f"{source.stem}-bluemask.png")


def mask_file(
    source: str | Path,
    first: str,
    second: str,
    *,
    cosmetic: bool = False,
    output: str | Path | None = None,
    force: bool = False,
) -> Path:
    """Mask the rectangle between two ``X,Y`` image coordinates."""

    source_path = _source_file(Path(source).expanduser())
    destination = Path(output).expanduser() if output is not None else default_output(source_path)
    _check_output(source_path, destination, force=force)

    try:
        with Image.open(source_path) as opened:
            image_format = (opened.format or "").upper()
            if image_format not in SUPPORTED_FORMATS:
                raise CliError("BlueMask accepts PNG, JPEG, and WebP images.", INVALID)
            image = ImageOps.exif_transpose(opened)
            image.load()
    except CliError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise CliError(f"Could not decode {source_path.name} as an image.", INVALID) from exc

    width, height = image.size
    if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
        raise CliError(
            f"Image is too large ({width}x{height}); maximum is {MAX_SIDE}px per side "
            f"and {MAX_PIXELS:,} pixels.",
            INVALID,
        )

    box = _rectangle(first, second, width, height)
    flattened = Image.alpha_composite(
        Image.new("RGBA", image.size, "white"),
        image.convert("RGBA"),
    ).convert("RGB")

    if cosmetic:
        blurred = flattened.crop(box).filter(ImageFilter.GaussianBlur(COSMETIC_RADIUS))
        flattened.paste(blurred, box)
    else:
        ImageDraw.Draw(flattened).rectangle(
            (box[0], box[1], box[2] - 1, box[3] - 1),
            fill=FILL,
        )

    _write_png(flattened, destination)
    return destination


def _coordinate(value: str) -> tuple[int, int]:
    parts = value.split(",")
    if len(parts) != 2:
        raise CliError(f"Invalid coordinate {value!r}; use X,Y, for example 40,20.", INVALID)
    try:
        x, y = (int(part.strip()) for part in parts)
    except ValueError as exc:
        raise CliError(f"Invalid coordinate {value!r}; X and Y must be integers.", INVALID) from exc
    return x, y


def _rectangle(first: str, second: str, width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1 = _coordinate(first)
    x2, y2 = _coordinate(second)
    if not (0 <= x1 <= width and 0 <= x2 <= width and 0 <= y1 <= height and 0 <= y2 <= height):
        raise CliError(
            f"Coordinates must be within 0,0 and {width},{height} for this image.",
            INVALID,
        )
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    if left == right or top == bottom:
        raise CliError("The two coordinates must enclose a non-empty rectangle.", INVALID)
    return left, top, right, bottom


def _source_file(path: Path) -> Path:
    if path.is_symlink():
        raise CliError(f"{path} is a symlink and cannot be processed.", INVALID)
    if not path.exists():
        raise CliError(f"No such file: {path}", INVALID)
    if not path.is_file():
        raise CliError(f"Not a file: {path}", INVALID)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise CliError("Image is larger than BlueMask's 35 MB limit.", INVALID)
    return path


def _check_output(source: Path, destination: Path, *, force: bool) -> None:
    if destination.suffix.lower() != ".png":
        raise CliError("BlueMask output must use a .png extension.", INVALID)
    if destination.is_symlink():
        raise CliError(f"{destination} is a symlink and cannot be replaced.", INVALID)
    try:
        if source.resolve() == destination.resolve():
            raise CliError("The output path must be different from the source image.", INVALID)
    except OSError as exc:
        raise CliError(f"Could not resolve the output path: {exc}", INVALID) from exc
    if destination.exists():
        if not destination.is_file():
            raise CliError(f"Output is not a regular file: {destination}", INVALID)
        if not force:
            raise CliError(f"{destination} already exists. Pass --force to replace it.", INVALID)
    if not destination.parent.is_dir():
        raise CliError(f"Output directory does not exist: {destination.parent}", INVALID)


def _write_png(image: Image.Image, destination: Path) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".png",
        dir=destination.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        image.save(temporary, "PNG")
        if temporary.stat().st_size == 0:
            raise CliError("The image writer produced an empty file.", INVALID)
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
