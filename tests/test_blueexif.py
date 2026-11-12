from __future__ import annotations

import json
import zipfile
from pathlib import Path

import piexif
import pikepdf
from PIL import Image

from bluethroat.blueexif import remove_file, replace_file, scan_file
from bluethroat.cli import main
from bluethroat.errors import INVALID


def _jpeg(path: Path) -> Path:
    exif = {
        "0th": {
            piexif.ImageIFD.ImageDescription: b"Private caption",
            piexif.ImageIFD.Make: b"Secret Camera",
            piexif.ImageIFD.Orientation: 6,
        },
        "Exif": {},
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }
    Image.new("RGB", (8, 6), "red").save(path, "JPEG", exif=piexif.dump(exif))
    return path


def _pdf(path: Path) -> Path:
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(100, 100))
        pdf.docinfo["/Author"] = "Secret Author"
        pdf.docinfo["/Title"] = "Private title"
        pdf.save(path)
    return path


def _docx(path: Path) -> Path:
    core = b"""<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties
 xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
 <dc:title>Private report</dc:title>
 <dc:creator>Secret Author</dc:creator>
</cp:coreProperties>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("docProps/core.xml", core)
        archive.writestr("word/document.xml", "<document/>")
    return path


def _fields(document: dict) -> dict[str, dict]:
    return {
        item["id"]: item
        for container in document["groups"]
        for item in container["fields"]
    }


def test_scan_prints_blueexif_schema(tmp_path: Path, capsys) -> None:
    source = _jpeg(tmp_path / "photo.jpg")

    assert main(["blueexif", "scan", str(source), "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["format"] == {
        "id": "jpeg",
        "name": "JPEG image",
        "mime": "image/jpeg",
        "category": "image",
    }
    assert _fields(document)["exif:ifd0:010e"]["value"] == "Private caption"
    assert document["canWrite"] is True
    assert document["canStrip"] is True


def test_replace_and_remove_jpeg_metadata_safely(tmp_path: Path) -> None:
    source = _jpeg(tmp_path / "photo.jpg")

    replaced, document = replace_file(
        source,
        ['exif:ifd0:010e="Public caption"'],
    )
    assert replaced == tmp_path / "photo-blueexif.jpg"
    assert source.read_bytes() != replaced.read_bytes()
    assert _fields(document)["exif:ifd0:010e"]["value"] == "Public caption"
    assert _fields(scan_file(source)[1])["exif:ifd0:010e"]["value"] == "Private caption"

    removed, document = remove_file(
        source,
        ["exif:ifd0:010f"],
        remove_all=False,
        output=tmp_path / "without-make.jpg",
    )
    assert "exif:ifd0:010f" not in _fields(document)
    with Image.open(removed) as image:
        assert image.size == (8, 6)


def test_remove_all_keeps_orientation_but_drops_other_jpeg_metadata(tmp_path: Path) -> None:
    source = _jpeg(tmp_path / "photo.jpg")

    output, document = remove_file(
        source,
        [],
        remove_all=True,
        output=tmp_path / "clean.jpg",
    )

    fields = _fields(document)
    assert fields["exif:ifd0:0112"]["value"] == 6
    assert "exif:ifd0:010e" not in fields
    assert "exif:ifd0:010f" not in fields
    with Image.open(output) as image:
        assert image.size == (8, 6)


def test_pdf_and_ooxml_fields_can_be_replaced_and_stripped(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "report.pdf")
    pdf_output, document = replace_file(
        pdf,
        ['pdf:Author="Anonymous"'],
        output=tmp_path / "edited.pdf",
    )
    assert _fields(document)["pdf:Author"]["value"] == "Anonymous"
    removed_pdf, document = remove_file(
        pdf_output,
        ["pdf:Author"],
        remove_all=False,
        output=tmp_path / "without-author.pdf",
    )
    assert "pdf:Author" not in _fields(document)
    clean_pdf, document = remove_file(
        removed_pdf,
        [],
        remove_all=True,
        output=tmp_path / "clean.pdf",
    )
    assert not _fields(document)
    with pikepdf.open(clean_pdf) as parsed:
        assert len(parsed.pages) == 1

    docx = _docx(tmp_path / "report.docx")
    edited_docx, document = replace_file(
        docx,
        ['ooxml:core:dc:creator="Anonymous"'],
        output=tmp_path / "edited.docx",
    )
    assert _fields(document)["ooxml:core:dc:creator"]["value"] == "Anonymous"
    _, document = remove_file(
        edited_docx,
        [],
        remove_all=True,
        output=tmp_path / "clean.docx",
    )
    assert not _fields(document)


def test_output_and_field_validation(tmp_path: Path, capsys) -> None:
    source = _jpeg(tmp_path / "photo.jpg")
    existing = tmp_path / "existing.jpg"
    existing.write_bytes(b"do not replace")

    assert (
        main(
            [
                "blueexif",
                "replace",
                str(source),
                'exif:ifd0:010e="Changed"',
                "-o",
                str(existing),
            ]
        )
        == INVALID
    )
    assert "already exists" in capsys.readouterr().err
    assert existing.read_bytes() == b"do not replace"

    assert main(["blueexif", "remove", str(source)]) == INVALID
    assert "field ids, or pass --all" in capsys.readouterr().err

    assert main(["blueexif", "replace", str(source), "missing=not-json"]) == INVALID
    assert "Invalid JSON" in capsys.readouterr().err


def test_missing_blueexif_subcommand_is_invalid(capsys) -> None:
    assert main(["blueexif"]) == INVALID
    assert "bluethroat blueexif" in capsys.readouterr().err
