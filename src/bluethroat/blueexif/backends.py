"""Format backends for the local BlueExif command."""

from __future__ import annotations

import copy
import gzip
import mimetypes
import shutil
import zipfile
from fractions import Fraction
from pathlib import Path
from typing import Any

from bluethroat.blueexif.model import Edit, field, group, humanize, info, json_safe, metadata
from bluethroat.errors import INVALID, CliError

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".png",
    ".apng",
    ".webp",
    ".gif",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
    ".dng",
    ".cr2",
    ".nef",
    ".arw",
    ".orf",
    ".rw2",
    ".pef",
}
AUDIO_EXTENSIONS = {
    ".mp3",
    ".mp2",
    ".flac",
    ".ogg",
    ".oga",
    ".opus",
    ".spx",
    ".wav",
    ".wave",
    ".bwf",
    ".aif",
    ".aiff",
    ".aifc",
    ".m4a",
    ".m4b",
    ".m4r",
}
VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".mov",
    ".qt",
    ".3gp",
    ".3g2",
    ".mkv",
    ".webm",
    ".avi",
}
OOXML_EXTENSIONS = {
    ".docx",
    ".docm",
    ".dotx",
    ".dotm",
    ".xlsx",
    ".xlsm",
    ".xltx",
    ".xltm",
    ".xlsb",
    ".pptx",
    ".pptm",
    ".potx",
    ".potm",
    ".ppsx",
    ".ppsm",
    ".vsdx",
}
ODF_EXTENSIONS = {
    ".odt",
    ".ott",
    ".odm",
    ".oth",
    ".ods",
    ".ots",
    ".odp",
    ".otp",
    ".odg",
    ".otg",
    ".odf",
    ".odc",
    ".odi",
}

_NS_PREFIX = {
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://purl.org/dc/terms/": "dcterms",
    "http://schemas.openxmlformats.org/package/2006/metadata/core-properties": "cp",
    "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties": "ep",
    "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties": "custom",
    "urn:oasis:names:tc:opendocument:xmlns:meta:1.0": "meta",
    "urn:oasis:names:tc:opendocument:xmlns:office:1.0": "office",
    "http://www.idpf.org/2007/opf": "opf",
}


def backend_for(path: Path) -> "Backend":
    ext = _extension(path)
    if ext in IMAGE_EXTENSIONS:
        return ImageBackend()
    if ext in AUDIO_EXTENSIONS:
        return AudioBackend()
    if ext == ".pdf":
        return PdfBackend()
    if ext in OOXML_EXTENSIONS:
        return ZipXmlBackend("ooxml")
    if ext in ODF_EXTENSIONS:
        return ZipXmlBackend("odf")
    if ext == ".epub":
        return ZipXmlBackend("epub")
    if ext in {".svg", ".svgz"} or path.name.lower().endswith(".svg.gz"):
        return SvgBackend()
    if ext in VIDEO_EXTENSIONS:
        if ext in {".mp4", ".m4v", ".mov", ".3gp", ".3g2"}:
            try:
                if _mutagen_file(path) is not None:
                    return AudioBackend(category="video")
            except Exception:
                pass
        return MediaBackend()
    raise CliError(f"Unsupported file format: {path.name}", INVALID)


class Backend:
    def scan(self, path: Path) -> dict[str, Any]:
        raise NotImplementedError

    def mutate(
        self,
        source: Path,
        target: Path,
        edits: dict[str, Edit],
        *,
        strip_all: bool,
    ) -> None:
        raise NotImplementedError


class ImageBackend(Backend):
    _IFDS = (("0th", "ifd0", "Image"), ("Exif", "exif", "Photo"), ("GPS", "gps", "GPS"))

    def scan(self, path: Path) -> dict[str, Any]:
        from PIL import Image

        warnings: list[str] = []
        groups: list[dict[str, Any]] = []
        technical: list[dict[str, str]] = []
        try:
            with Image.open(path) as image:
                technical.extend(
                    [
                        info("Dimensions", f"{image.width} × {image.height}"),
                        info("Mode", image.mode),
                    ]
                )
                if getattr(image, "n_frames", 1) > 1:
                    technical.append(info("Frames", image.n_frames))
                format_name = image.format or _extension(path).lstrip(".").upper()
                mime = Image.MIME.get(image.format or "", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        except Exception as exc:
            format_name = _extension(path).lstrip(".").upper() or "image"
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            warnings.append(f"Image details could not be decoded: {exc}")

        exif = _load_exif(path, warnings)
        for native_ifd, field_ifd, label in self._IFDS:
            items: list[dict[str, Any]] = []
            for tag_id, raw in sorted(exif.get(native_ifd, {}).items()):
                if not isinstance(tag_id, int):
                    continue
                spec = _exif_spec(native_ifd, tag_id, raw)
                items.append(
                    field(
                        f"exif:{field_ifd}:{tag_id:04x}",
                        spec["label"],
                        spec["kind"],
                        spec["value"],
                        tag=spec["tag"],
                        sensitivity=_sensitivity(spec["label"]),
                        editable=_extension(path) in {".jpg", ".jpeg", ".jpe", ".jfif"} and spec["editable"],
                        removable=_extension(path) in {".jpg", ".jpeg", ".jpe", ".jfif"},
                        display=spec.get("display"),
                    )
                )
            if items:
                groups.append(group(f"exif:{field_ifd}", f"{label} metadata", items, source="EXIF", removable=True))

        ext = _extension(path)
        writable = ext in {".jpg", ".jpeg", ".jpe", ".jfif"}
        return metadata(
            "jpeg" if writable else ext.lstrip("."),
            f"{format_name} image",
            mime,
            "image",
            info=technical,
            groups=groups,
            warnings=warnings,
            can_write=writable,
            can_strip=writable,
        )

    def mutate(
        self,
        source: Path,
        target: Path,
        edits: dict[str, Edit],
        *,
        strip_all: bool,
    ) -> None:
        if _extension(source) not in {".jpg", ".jpeg", ".jpe", ".jfif"}:
            raise CliError(f"{source.suffix} images are scan-only in the Python backend.", INVALID)
        if strip_all:
            target.write_bytes(_strip_jpeg(source.read_bytes()))
            return

        import piexif

        shutil.copyfile(source, target)
        exif = _load_exif(source, [])
        for field_id, edit in edits.items():
            if field_id.startswith("@group:exif:"):
                name = field_id.rsplit(":", 1)[-1]
                native = {"ifd0": "0th", "exif": "Exif", "gps": "GPS"}.get(name)
                if native:
                    exif[native] = {}
                continue
            parts = field_id.split(":")
            if len(parts) != 3 or parts[0] != "exif":
                raise CliError(f"{field_id} is not writable for this image.", INVALID)
            native = {"ifd0": "0th", "exif": "Exif", "gps": "GPS"}.get(parts[1])
            try:
                tag_id = int(parts[2], 16)
            except ValueError as exc:
                raise CliError(f"Invalid EXIF field id: {field_id}", INVALID) from exc
            if native is None or tag_id not in exif.get(native, {}):
                raise CliError(f"Unknown EXIF field: {field_id}", INVALID)
            if edit.op == "remove":
                exif[native].pop(tag_id, None)
            else:
                exif[native][tag_id] = _encode_exif(native, tag_id, edit.value)
        try:
            payload = piexif.dump(exif)
            if any(exif.get(name) for name in ("0th", "Exif", "GPS", "1st")):
                piexif.insert(payload, str(target))
            else:
                piexif.remove(str(target))
        except Exception as exc:
            raise CliError(f"Could not rewrite EXIF metadata: {exc}", INVALID) from exc


class AudioBackend(Backend):
    def __init__(self, *, category: str = "audio") -> None:
        self.category = category

    def scan(self, path: Path) -> dict[str, Any]:
        audio = _mutagen_file(path)
        if audio is None:
            return MediaBackend(category=self.category).scan(path)
        technical: list[dict[str, str]] = []
        stream = getattr(audio, "info", None)
        if stream is not None:
            if getattr(stream, "length", None) is not None:
                technical.append(info("Duration", f"{float(stream.length):.3f} s"))
            if getattr(stream, "bitrate", None):
                technical.append(info("Bit rate", f"{int(stream.bitrate)} bit/s"))
            if getattr(stream, "sample_rate", None):
                technical.append(info("Sample rate", f"{int(stream.sample_rate)} Hz"))
            if getattr(stream, "channels", None):
                technical.append(info("Channels", int(stream.channels)))

        fields: list[dict[str, Any]] = []
        source_name = "Tags"
        prefix = "tag"
        tags = getattr(audio, "tags", None)
        if tags is not None:
            prefix, source_name = _mutagen_prefix(tags)
            for key, value in tags.items():
                kind, converted, display, editable = _mutagen_value(value)
                fields.append(
                    field(
                        f"{prefix}:{key}",
                        humanize(str(key).split(":", 1)[0]),
                        kind,
                        converted,
                        tag=str(key),
                        sensitivity=_sensitivity(str(key)),
                        editable=editable,
                        removable=True,
                        display=display,
                    )
                )
        groups = [group(prefix, source_name, fields, source=source_name, removable=True)] if fields else []
        format_name = type(audio).__name__.replace("File", "")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return metadata(
            _extension(path).lstrip("."),
            f"{format_name} {'video' if self.category == 'video' else 'audio'}",
            mime,
            self.category,
            info=technical,
            groups=groups,
            can_write=tags is not None,
            can_strip=tags is not None,
        )

    def mutate(
        self,
        source: Path,
        target: Path,
        edits: dict[str, Edit],
        *,
        strip_all: bool,
    ) -> None:
        shutil.copyfile(source, target)
        audio = _mutagen_file(target)
        if audio is None or getattr(audio, "tags", None) is None:
            raise CliError(f"{source.name} has no writable tag container.", INVALID)
        tags = audio.tags
        prefix, _ = _mutagen_prefix(tags)
        if strip_all:
            try:
                audio.delete()
            except Exception as exc:
                raise CliError(f"Could not strip media tags: {exc}", INVALID) from exc
            return
        for field_id, edit in edits.items():
            if field_id == f"@group:{prefix}":
                tags.clear()
                continue
            wanted = f"{prefix}:"
            if not field_id.startswith(wanted):
                raise CliError(f"{field_id} is not writable for this file.", INVALID)
            key = field_id[len(wanted) :]
            if key not in tags:
                raise CliError(f"Unknown media field: {field_id}", INVALID)
            if edit.op == "remove":
                del tags[key]
            else:
                _set_mutagen(tags, key, edit.value)
        try:
            audio.save()
        except Exception as exc:
            raise CliError(f"Could not rewrite media tags: {exc}", INVALID) from exc


class PdfBackend(Backend):
    def scan(self, path: Path) -> dict[str, Any]:
        import pikepdf

        fields: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            with pikepdf.open(path) as pdf:
                for key, value in pdf.docinfo.items():
                    name = str(key).lstrip("/")
                    fields.append(
                        field(
                            f"pdf:{name}",
                            humanize(name),
                            "text",
                            str(value),
                            tag=f"/{name}",
                            sensitivity=_sensitivity(name),
                        )
                    )
                technical = [info("Pages", len(pdf.pages))]
                try:
                    with pdf.open_metadata(set_pikepdf_as_editor=False) as xmp:
                        for key, value in xmp.items():
                            if value is None:
                                continue
                            fields.append(
                                field(
                                    _xmp_field_id(str(key)),
                                    humanize(str(key).split(":")[-1]),
                                    "list" if isinstance(value, list) else "text",
                                    json_safe(value),
                                    tag=str(key),
                                    sensitivity=_sensitivity(str(key)),
                                )
                            )
                except Exception as exc:
                    warnings.append(f"XMP could not be read: {exc}")
        except pikepdf.PasswordError as exc:
            raise CliError("This PDF requires a password; password input is not supported.", INVALID) from exc
        except pikepdf.PdfError as exc:
            raise CliError(f"Could not read PDF metadata: {exc}", INVALID) from exc
        groups: list[dict[str, Any]] = []
        pdf_fields = [item for item in fields if item["id"].startswith("pdf:")]
        xmp_fields = [item for item in fields if item["id"].startswith("xmp:")]
        if pdf_fields:
            groups.append(group("pdf:info", "PDF document information", pdf_fields, source="PDF Info", removable=True))
        if xmp_fields:
            groups.append(group("pdf:xmp", "XMP metadata", xmp_fields, source="XMP", removable=True))
        return metadata(
            "pdf",
            "PDF document",
            "application/pdf",
            "document",
            info=technical,
            groups=groups,
            warnings=warnings,
            can_write=True,
            can_strip=True,
        )

    def mutate(
        self,
        source: Path,
        target: Path,
        edits: dict[str, Edit],
        *,
        strip_all: bool,
    ) -> None:
        import pikepdf

        try:
            with pikepdf.open(source) as pdf:
                if strip_all:
                    _clear_pdf_info(pdf.docinfo)
                    for key in ("/Metadata", "/OpenAction", "/AA", "/AcroForm"):
                        if key in pdf.Root:
                            del pdf.Root[key]
                    if "/Names" in pdf.Root:
                        names = pdf.Root["/Names"]
                        for key in ("/EmbeddedFiles", "/JavaScript"):
                            if key in names:
                                del names[key]
                    for name in list(pdf.attachments):
                        del pdf.attachments[name]
                else:
                    xmp_edits: list[tuple[str, Edit]] = []
                    for field_id, edit in edits.items():
                        if field_id in {"@group:pdf:info", "@group:pdf:xmp"}:
                            if field_id.endswith("info"):
                                _clear_pdf_info(pdf.docinfo)
                            else:
                                if "/Metadata" in pdf.Root:
                                    del pdf.Root["/Metadata"]
                            continue
                        if field_id.startswith("pdf:"):
                            key = "/" + field_id[4:]
                            if edit.op == "remove":
                                if key in pdf.docinfo:
                                    del pdf.docinfo[key]
                            else:
                                pdf.docinfo[key] = str(edit.value)
                        elif field_id.startswith("xmp:"):
                            xmp_edits.append((_xmp_key(field_id), edit))
                        else:
                            raise CliError(f"{field_id} is not writable for this PDF.", INVALID)
                    if xmp_edits:
                        with pdf.open_metadata(set_pikepdf_as_editor=False) as xmp:
                            for key, edit in xmp_edits:
                                if edit.op == "remove":
                                    xmp.pop(key, None)
                                else:
                                    xmp[key] = edit.value
                pdf.save(target)
        except pikepdf.PdfError as exc:
            raise CliError(f"Could not rewrite PDF metadata: {exc}", INVALID) from exc


class ZipXmlBackend(Backend):
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def scan(self, path: Path) -> dict[str, Any]:
        from lxml import etree

        fields: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            with zipfile.ZipFile(path) as archive:
                for member in self._metadata_members(archive):
                    try:
                        root = etree.fromstring(archive.read(member))
                    except (KeyError, etree.XMLSyntaxError) as exc:
                        warnings.append(f"{member} could not be read: {exc}")
                        continue
                    for field_id, element in self._elements(member, root):
                        value = element.text or ""
                        fields.append(
                            field(
                                field_id,
                                humanize(_local_name(element.tag)),
                                "text",
                                value,
                                tag=_qualified_name(element.tag, root.nsmap),
                                sensitivity=_sensitivity(field_id),
                                editable=not self._required_epub(field_id),
                                removable=not self._required_epub(field_id),
                            )
                        )
                technical = [info("Package entries", len(archive.infolist()))]
        except (zipfile.BadZipFile, OSError) as exc:
            raise CliError(f"Could not read {self.kind.upper()} package: {exc}", INVALID) from exc
        label = {"ooxml": "Office Open XML", "odf": "OpenDocument", "epub": "EPUB"}[self.kind]
        groups = [group(f"{self.kind}:metadata", f"{label} metadata", fields, source=label, removable=True)] if fields else []
        return metadata(
            self.kind,
            f"{label} document",
            mimetypes.guess_type(path.name)[0] or "application/zip",
            "document",
            info=technical,
            groups=groups,
            warnings=warnings,
            can_write=True,
            can_strip=True,
        )

    def mutate(
        self,
        source: Path,
        target: Path,
        edits: dict[str, Edit],
        *,
        strip_all: bool,
    ) -> None:
        from lxml import etree

        try:
            with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(target, "w") as outgoing:
                members = set(self._metadata_members(incoming))
                for item in incoming.infolist():
                    payload = incoming.read(item.filename)
                    if item.filename in members:
                        root = etree.fromstring(payload)
                        indexed = list(self._elements(item.filename, root))
                        if strip_all:
                            for field_id, element in indexed:
                                if self._required_epub(field_id):
                                    if "modified" in field_id:
                                        element.text = "1970-01-01T00:00:00Z"
                                    continue
                                parent = element.getparent()
                                if parent is not None:
                                    parent.remove(element)
                        else:
                            for field_id, element in indexed:
                                edit = edits.get(field_id)
                                if edit is None:
                                    continue
                                if edit.op == "remove":
                                    parent = element.getparent()
                                    if parent is not None:
                                        parent.remove(element)
                                else:
                                    element.text = str(edit.value)
                            if f"@group:{self.kind}:metadata" in edits:
                                for field_id, element in indexed:
                                    if not self._required_epub(field_id):
                                        parent = element.getparent()
                                        if parent is not None:
                                            parent.remove(element)
                        payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
                    copied = copy.copy(item)
                    if strip_all:
                        copied.date_time = (1980, 1, 1, 0, 0, 0)
                        copied.comment = b""
                    outgoing.writestr(copied, payload)
                if strip_all:
                    outgoing.comment = b""
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            raise CliError(f"Could not rewrite {self.kind.upper()} metadata: {exc}", INVALID) from exc

    def _metadata_members(self, archive: zipfile.ZipFile) -> list[str]:
        names = set(archive.namelist())
        if self.kind == "ooxml":
            return [name for name in ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml") if name in names]
        if self.kind == "odf":
            return ["meta.xml"] if "meta.xml" in names else []
        container = "META-INF/container.xml"
        if container not in names:
            return []
        from lxml import etree

        root = etree.fromstring(archive.read(container))
        return [
            value
            for value in root.xpath("//*[local-name()='rootfile']/@full-path")
            if isinstance(value, str) and value in names
        ]

    def _elements(self, member: str, root: Any) -> list[tuple[str, Any]]:
        result: list[tuple[str, Any]] = []
        counts: dict[str, int] = {}
        if self.kind == "epub":
            candidates = root.xpath("//*[local-name()='metadata']/*")
        else:
            candidates = [element for element in root.iterdescendants() if len(element) == 0]
        for element in candidates:
            if not isinstance(element.tag, str):
                continue
            local = _local_name(element.tag)
            if self.kind == "ooxml":
                part = "core" if member.endswith("core.xml") else "app" if member.endswith("app.xml") else "custom"
                prefix = _prefix(element.tag)
                base = f"ooxml:{part}:{prefix}:{local}"
            elif self.kind == "odf":
                base = f"odf:{_prefix(element.tag)}:{local}"
            else:
                prefix = _prefix(element.tag)
                if local == "meta" and element.get("property"):
                    base = f"epub:meta:{element.get('property')}"
                else:
                    base = f"epub:{prefix}:{local}"
            index = counts.get(base, 0)
            counts[base] = index + 1
            result.append((base if index == 0 else f"{base}:{index}", element))
        return result

    @staticmethod
    def _required_epub(field_id: str) -> bool:
        return field_id.startswith(("epub:dc:title", "epub:dc:language", "epub:dc:identifier")) or "dcterms:modified" in field_id


class SvgBackend(Backend):
    def scan(self, path: Path) -> dict[str, Any]:
        from lxml import etree

        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        try:
            payload = path.read_bytes()
            if _extension(path) == ".svgz":
                payload = gzip.decompress(payload)
            root = etree.fromstring(payload, parser)
        except (OSError, etree.XMLSyntaxError) as exc:
            raise CliError(f"Could not read SVG metadata: {exc}", INVALID) from exc
        fields: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for element in root.xpath("//*[local-name()='title' or local-name()='desc' or local-name()='metadata']"):
            local = _local_name(element.tag)
            index = counts.get(local, 0)
            counts[local] = index + 1
            field_id = f"svg:{local}" + (f":{index}" if index else "")
            raw = "".join(element.itertext()).strip()
            fields.append(
                field(
                    field_id,
                    humanize(local),
                    "multiline" if local != "metadata" else "raw",
                    raw if local != "metadata" else None,
                    sensitivity="content",
                    editable=local != "metadata",
                    removable=True,
                    display=raw if local == "metadata" else None,
                )
            )
        groups = [group("svg:metadata", "SVG metadata", fields, source="SVG", removable=True)] if fields else []
        return metadata(
            "svg",
            "SVG image",
            "image/svg+xml",
            "image",
            groups=groups,
            can_write=True,
            can_strip=True,
        )

    def mutate(
        self,
        source: Path,
        target: Path,
        edits: dict[str, Edit],
        *,
        strip_all: bool,
    ) -> None:
        from lxml import etree

        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        payload = source.read_bytes()
        compressed = _extension(source) == ".svgz"
        if compressed:
            payload = gzip.decompress(payload)
        root = etree.fromstring(payload, parser)
        counts: dict[str, int] = {}
        for element in list(root.xpath("//*[local-name()='title' or local-name()='desc' or local-name()='metadata']")):
            local = _local_name(element.tag)
            index = counts.get(local, 0)
            counts[local] = index + 1
            field_id = f"svg:{local}" + (f":{index}" if index else "")
            edit = edits.get(field_id)
            if strip_all or f"@group:svg:metadata" in edits or (edit and edit.op == "remove"):
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
            elif edit is not None:
                element.text = str(edit.value)
        output = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
        target.write_bytes(gzip.compress(output, mtime=0) if compressed else output)


class MediaBackend(Backend):
    def __init__(self, *, category: str = "video") -> None:
        self.category = category

    def scan(self, path: Path) -> dict[str, Any]:
        from pymediainfo import MediaInfo

        try:
            parsed = MediaInfo.parse(str(path))
        except Exception as exc:
            raise CliError(f"Could not inspect media metadata: {exc}", INVALID) from exc
        groups: list[dict[str, Any]] = []
        for index, track in enumerate(parsed.tracks):
            fields: list[dict[str, Any]] = []
            for key, value in track.to_data().items():
                if value in (None, "", [], {}):
                    continue
                fields.append(
                    field(
                        f"mediainfo:{index}:{key}",
                        humanize(key),
                        "raw",
                        None,
                        tag=key,
                        editable=False,
                        removable=False,
                        display=str(value),
                    )
                )
            if fields:
                groups.append(group(f"mediainfo:{index}", f"{track.track_type} track", fields, source="MediaInfo"))
        return metadata(
            _extension(path).lstrip("."),
            f"{_extension(path).lstrip('.').upper()} media",
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            self.category,
            groups=groups,
            warnings=["This format is scan-only in the Python backend."],
            can_write=False,
            can_strip=False,
        )

    def mutate(
        self,
        source: Path,
        target: Path,
        edits: dict[str, Edit],
        *,
        strip_all: bool,
    ) -> None:
        raise CliError(f"{source.suffix} media is scan-only in the Python backend.", INVALID)


def _extension(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".svg.gz"):
        return ".svgz"
    return path.suffix.lower()


def _load_exif(path: Path, warnings: list[str]) -> dict[str, Any]:
    import piexif

    try:
        result = piexif.load(str(path))
    except Exception as exc:
        warnings.append(f"EXIF could not be read: {exc}")
        result = {}
    result.setdefault("0th", {})
    result.setdefault("Exif", {})
    result.setdefault("GPS", {})
    result.setdefault("1st", {})
    result.setdefault("thumbnail", None)
    return result


def _exif_spec(ifd: str, tag_id: int, value: Any) -> dict[str, Any]:
    import piexif

    tag = piexif.TAGS.get(ifd, {}).get(tag_id, {})
    name = str(tag.get("name") or f"Tag 0x{tag_id:04X}")
    tag_type = tag.get("type")
    if tag_type == 2 and isinstance(value, bytes):
        converted: Any = value.rstrip(b"\0").decode("utf-8", "replace")
        kind = "text"
        editable = True
    elif tag_type in {3, 4, 9} and isinstance(value, int):
        converted = value
        kind = "integer"
        editable = True
    elif tag_type in {5, 10}:
        converted = _rational_value(value)
        kind = "number" if isinstance(converted, (int, float)) else "list"
        editable = isinstance(converted, (int, float))
    elif isinstance(value, bytes):
        return {
            "label": humanize(name),
            "kind": "raw",
            "value": None,
            "tag": f"{name} (0x{tag_id:04X})",
            "editable": False,
            "display": f"{len(value)} bytes",
        }
    else:
        converted = json_safe(value)
        kind = "list" if isinstance(converted, list) else "text"
        editable = False
    return {
        "label": humanize(name),
        "kind": kind,
        "value": converted,
        "tag": f"{name} (0x{tag_id:04X})",
        "editable": editable,
    }


def _rational_value(value: Any) -> Any:
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(item, int) for item in value):
        return value[0] / value[1] if value[1] else 0
    if isinstance(value, tuple):
        return [_rational_value(item) for item in value]
    return value


def _encode_exif(ifd: str, tag_id: int, value: Any) -> Any:
    import piexif

    tag_type = piexif.TAGS.get(ifd, {}).get(tag_id, {}).get("type")
    if tag_type == 2:
        if not isinstance(value, str):
            raise CliError(f"EXIF tag 0x{tag_id:04x} requires a string.", INVALID)
        return value.encode("utf-8")
    if tag_type in {3, 4, 9}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise CliError(f"EXIF tag 0x{tag_id:04x} requires an integer.", INVALID)
        return value
    if tag_type in {5, 10}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CliError(f"EXIF tag 0x{tag_id:04x} requires a number.", INVALID)
        fraction = Fraction(float(value)).limit_denominator(1_000_000)
        return fraction.numerator, fraction.denominator
    raise CliError(f"EXIF tag 0x{tag_id:04x} cannot be replaced safely.", INVALID)


def _strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        raise CliError("The file extension says JPEG but the signature does not match.", INVALID)
    orientation: int | None = None
    try:
        import piexif

        orientation = piexif.load(data).get("0th", {}).get(piexif.ImageIFD.Orientation)
    except Exception:
        pass
    output = bytearray(data[:2])
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            output.extend(data[offset:])
            break
        marker = data[offset + 1]
        if marker == 0xDA:
            output.extend(data[offset:])
            break
        if marker in {0xD8, 0xD9}:
            output.extend(data[offset : offset + 2])
            offset += 2
            continue
        length = int.from_bytes(data[offset + 2 : offset + 4], "big")
        end = offset + 2 + length
        if length < 2 or end > len(data):
            raise CliError("Malformed JPEG segment.", INVALID)
        segment = data[offset:end]
        keep = marker == 0xE0 or (marker == 0xE2 and data[offset + 4 : offset + 16].startswith(b"ICC_PROFILE"))
        if keep or not (0xE0 <= marker <= 0xEF or marker == 0xFE):
            output.extend(segment)
        offset = end
    if orientation and orientation != 1:
        import piexif

        exif = {"0th": {piexif.ImageIFD.Orientation: orientation}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        try:
            payload = piexif.dump(exif)
            segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
            output[2:2] = segment
        except Exception:
            return bytes(output)
    return bytes(output)


def _mutagen_file(path: Path):
    import mutagen

    return mutagen.File(path, easy=False)


def _mutagen_prefix(tags: Any) -> tuple[str, str]:
    module = type(tags).__module__
    if "id3" in module:
        return "id3", "ID3"
    if "mp4" in module:
        return "mp4:ilst", "iTunes metadata"
    if any(token in module for token in ("flac", "ogg", "vorbis")):
        return "vorbis", "Vorbis comments"
    return "tag", type(tags).__name__


def _mutagen_value(value: Any) -> tuple[str, Any, str | None, bool]:
    if hasattr(value, "mime") and hasattr(value, "data"):
        return "image", {"mime": value.mime, "data": json_safe(value.data)}, None, False
    if hasattr(value, "text"):
        values = [str(item) for item in value.text]
        return ("text", values[0], None, True) if len(values) == 1 else ("list", values, None, True)
    if isinstance(value, list):
        if value and isinstance(value[0], bytes):
            return "raw", None, f"{sum(len(item) for item in value)} bytes", False
        values = [str(item) for item in value]
        return ("text", values[0], None, True) if len(values) == 1 else ("list", values, None, True)
    if isinstance(value, bytes):
        return "raw", None, f"{len(value)} bytes", False
    if isinstance(value, (str, int, float)):
        kind = "number" if isinstance(value, (int, float)) else "text"
        return kind, value, None, True
    return "raw", None, str(value), False


def _set_mutagen(tags: Any, key: str, value: Any) -> None:
    current = tags[key]
    if hasattr(current, "text"):
        values = value if isinstance(value, list) else [value]
        current.text = [str(item) for item in values]
        tags[key] = current
        return
    values = value if isinstance(value, list) else [value]
    try:
        tags[key] = values
    except (TypeError, ValueError) as exc:
        raise CliError(f"{key} cannot be set to that value: {exc}", INVALID) from exc


def _clear_pdf_info(docinfo: Any) -> None:
    for key in list(docinfo.keys()):
        del docinfo[key]


def _sensitivity(name: str) -> str | None:
    lower = name.lower()
    for words, label in (
        (("gps", "location", "latitude", "longitude", "city", "country"), "location"),
        (("author", "artist", "creator", "owner", "email", "account", "company"), "identity"),
        (("make", "model", "serial", "lens", "device"), "device"),
        (("date", "time", "created", "modified", "year"), "time"),
        (("software", "application", "producer", "encoder", "generator"), "software"),
        (("comment", "description", "title", "subject", "keyword", "thumbnail", "cover"), "content"),
    ):
        if any(word in lower for word in words):
            return label
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _prefix(tag: str) -> str:
    return _NS_PREFIX.get(_namespace(tag), "meta")


def _qualified_name(tag: str, nsmap: dict[Any, Any]) -> str:
    uri = _namespace(tag)
    prefix = next((key for key, value in nsmap.items() if value == uri and key), None)
    return f"{prefix}:{_local_name(tag)}" if prefix else _local_name(tag)


def _xmp_field_id(key: str) -> str:
    if key.startswith("{") and "}" in key:
        uri, local = key[1:].split("}", 1)
        return f"xmp:{uri}|{local}"
    return f"xmp:{key}"


def _xmp_key(field_id: str) -> str:
    key = field_id[4:]
    if "|" in key:
        uri, local = key.rsplit("|", 1)
        return f"{{{uri}}}{local}"
    return key
