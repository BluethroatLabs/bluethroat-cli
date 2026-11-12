# bluethroat

Command-line client for BlueSkills, BlueGate, BluePaper, and local BlueExif and BlueMask tools.
Log in once with the Bluethroat GitHub App for hosted tools; BlueExif and BlueMask run
locally and need no account.

```bash
uv tool install bluethroat
bluethroat auth login
bluethroat blueskills scan https://github.com/owner/repository --ref <commit>
bluethroat clone https://github.com/owner/repository --ref <commit>
bluethroat bluegate scan https://github.com/owner/repository --ref <commit>
bluethroat bluepaper convert suspicious.docx
bluethroat blueexif scan photo.jpg
bluethroat bluemask blur screenshot.png 40,20 180,70
```

`clone` and `bluegate scan` are the same BlueGate scan. Neither runs git. The text report ends with a clone command pinned to the scanned commit. `--json` prints the scan document from either product. On the BlueGate commands, `--agent` prints the fenced agent report. Without a format flag, the command prints text.

`bluepaper convert` uploads a document, waits for the isolated pixel rebuild, prints the byte-indicator report, and writes `<source stem>-safe.pdf`. Use `-o PATH` to choose another output, `--ocr-lang eng` to request OCR, and `--force` to replace an existing regular file. `--json` prints the conversion and report document; the safe PDF is still written. A successful rebuild exits 0 with no indicators or 1 with indicators. A failed rebuild exits 7 and does not write a PDF.

## BlueExif

BlueExif scans and changes metadata locally:

```bash
# Show field ids and values. Add --json for the complete metadata document.
bluethroat blueexif scan photo.jpg

# JSON values are required; quote string assignments for the shell.
bluethroat blueexif replace photo.jpg \
  'exif:ifd0:010e="Public caption"' \
  'exif:ifd0:0112=1'

# Remove selected fields, a removable group, or all nonessential metadata.
bluethroat blueexif remove song.mp3 id3:TPE1 id3:COMM
bluethroat blueexif remove report.pdf @group:pdf:info
bluethroat blueexif remove photo.jpg --all
```

Mutations never overwrite the source. They write `<source stem>-blueexif<source suffix>`;
use `-o PATH` to choose another file and `--force` to replace an existing regular file.
Symlink outputs and source/output aliasing are refused. `--all` keeps JPEG orientation and
ICC colour profiles, and removes cover art.

The Python backend supports EXIF edits on JPEG; ID3, Vorbis/FLAC/Ogg, MP4/M4A, WAV and
AIFF tags through Mutagen; PDF Info/XMP; SVG; and metadata XML in OOXML, OpenDocument,
and EPUB packages. Other image and media formats may be scan-only. This backend does
not reproduce every writer or hidden-data rule in the browser BlueExif engine; an
unsupported or unsafe mutation fails instead of remuxing or re-encoding the file.

## BlueMask

BlueMask replaces a rectangular image region with an opaque neutral mask by default:

```bash
bluethroat bluemask blur screenshot.png 40,20 180,70
```

Coordinates are `X,Y` corners in displayed-image pixels. `(0,0)` is the top-left, X
increases rightward, Y increases downward, and the rectangle's right and bottom edges
are exclusive. Either corner order is accepted.

Pass `--cosmetic` for appearance-only Gaussian blur. Cosmetic blur can leave details
recoverable and is not secure redaction:

```bash
bluethroat bluemask blur photo.jpg 120,80 360,300 --cosmetic -o photo-blurred.png
```

The command accepts PNG, JPEG, and WebP locally, applies EXIF orientation, flattens
transparency onto white, and writes a fresh metadata-free PNG. The default output is
`<source stem>-bluemask.png`. Existing files and symlinks are refused unless an existing
regular output is explicitly replaced with `--force`.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BLUETHROAT_GITHUB_CLIENT_ID` | GitHub App client ID. Defaults to the Bluethroat app (`Iv23liH0YU6oTHHz0i8O`). |
| `BLUETHROAT_GITHUB_CLIENT_SECRET` | Optional, for development only. Revokes directly at GitHub instead of through BlueSkills. Do not put this in the distributed client. |
| `BLUETHROAT_API_URL` | BlueSkills API origin. Defaults to `https://blueskills-web-hzdtf5buftehbya8.z03.azurefd.net`. |
| `BLUETHROAT_BLUEGATE_API_URL` | BlueGate API origin. Defaults to `https://bluegate.bluethroatlabs.com`. |
| `BLUETHROAT_BLUEPAPER_API_URL` | BluePaper API origin. Defaults to `https://bluepaper.bluethroatlabs.com`. |

The same keys can live in `~/.config/bluethroat/config.json` as `github_client_id`, `github_client_secret`, `api_url`, `bluegate_api_url`, and `bluepaper_api_url`. Environment variables win.

The GitHub session is stored in the operating-system credential store, or in a mode `0600` file at `~/.config/bluethroat/credentials.json` when that store is unavailable.

`blueskills scan`, `clone`, `bluegate scan`, and `bluepaper convert` send `Authorization: Bearer` with the GitHub user access token. Each API checks it against the Bluethroat CLI GitHub App; BluePaper also binds each conversion to that GitHub user id. The access token lasts 8 hours; the CLI refreshes it with GitHub on its own, which needs no client secret for a device-flow login. `auth logout` asks BlueSkills (`POST /auth/github/revoke`) to revoke the token, since only the server holds the secret. `blueexif` never uploads the file and does not use the stored credential.
