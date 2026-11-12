---
name: blueexif
description: Inspects, replaces, and removes local file metadata with `bluethroat blueexif`. Use when an agent needs to examine EXIF, media tags, PDF properties, or document-package metadata, or sanitize a file without uploading it.
---

# BlueExif

Use `bluethroat blueexif` for local metadata work. It does not require login and does not
upload files.

## Install

```bash
uv tool install bluethroat
```

Confirm the command is available with `bluethroat --version`.

## Workflow

1. Scan before changing anything:

   ```bash
   bluethroat blueexif scan INPUT --json
   ```

2. Read `groups[].fields[]`. Use each field's exact `id`, `kind`, `editable`, and
   `removable` values. Field IDs are opaque; never guess one.
3. Write a new file with `replace` or `remove`.
4. Scan the output and verify the intended fields changed and the payload still opens.

Do not pass `--force` unless replacing the named output was explicitly requested.
Mutations otherwise refuse existing files, symlinks, and the source path.

## Replace fields

Pass one or more `FIELD=JSON` arguments. JSON strings need both JSON quotes and shell
quotes:

```bash
bluethroat blueexif replace photo.jpg \
  'exif:ifd0:010e="Public caption"' \
  'exif:ifd0:0112=1' \
  -o photo-public.jpg
```

Use JSON arrays for list fields and objects for GPS fields:

```bash
bluethroat blueexif replace song.flac 'vorbis:ARTIST=["Artist One","Artist Two"]'
```

`raw` and `image` fields cannot be replaced by this CLI.

## Remove fields

Remove selected field IDs:

```bash
bluethroat blueexif remove report.pdf pdf:Author pdf:CreationDate -o report-clean.pdf
```

When scan marks a group `removable: true`, remove it with `@group:<group-id>`:

```bash
bluethroat blueexif remove report.pdf @group:pdf:info
```

Strip all nonessential metadata:

```bash
bluethroat blueexif remove photo.jpg --all
```

`--all` preserves JPEG orientation and ICC colour profiles and removes cover art. Do not
combine `--all` with field IDs.

## Output and errors

Without `-o`, mutations write `<stem>-blueexif<suffix>` and leave the source untouched.
Use `--force` only for an approved existing regular output file.

Exit code `0` means success. Exit code `4` means invalid input, an unsupported format or
mutation, a read-only field, or an unsafe output path. Other bluethroat exit codes retain
their normal meanings.

The Python backend edits JPEG EXIF; common audio tags; PDF Info/XMP; SVG; and metadata XML
in OOXML, OpenDocument, and EPUB. Other image/video formats may be scan-only. If
`canWrite` or `canStrip` is false, report the limitation; do not work around it by
re-encoding, remuxing, or calling an unrelated metadata tool.
