# bluethroat

Command-line client for BlueSkills, BlueGate, and BluePaper. Log in once with the Bluethroat GitHub App.

```bash
uv tool install bluethroat
bluethroat auth login
bluethroat blueskills scan https://github.com/owner/repository --ref <commit>
bluethroat clone https://github.com/owner/repository --ref <commit>
bluethroat bluegate scan https://github.com/owner/repository --ref <commit>
bluethroat bluepaper convert suspicious.docx
```

`clone` and `bluegate scan` are the same BlueGate scan. Neither runs git. The text report ends with a clone command pinned to the scanned commit. `--json` prints the scan document from either product. On the BlueGate commands, `--agent` prints the fenced agent report. Without a format flag, the command prints text.

`bluepaper convert` uploads a document, waits for the isolated pixel rebuild, prints the byte-indicator report, and writes `<source stem>-safe.pdf`. Use `-o PATH` to choose another output, `--ocr-lang eng` to request OCR, and `--force` to replace an existing regular file. `--json` prints the conversion and report document; the safe PDF is still written. A successful rebuild exits 0 with no indicators or 1 with indicators. A failed rebuild exits 7 and does not write a PDF.

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

`blueskills scan`, `clone`, `bluegate scan`, and `bluepaper convert` send `Authorization: Bearer` with the GitHub user access token. Each API checks it against the Bluethroat CLI GitHub App; BluePaper also binds each conversion to that GitHub user id. The access token lasts 8 hours; the CLI refreshes it with GitHub on its own, which needs no client secret for a device-flow login. `auth logout` asks BlueSkills (`POST /auth/github/revoke`) to revoke the token, since only the server holds the secret.
