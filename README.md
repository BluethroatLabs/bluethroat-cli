# bluethroat

Command-line client for BlueSkills and BlueGate. Log in once with the Bluethroat GitHub App. A scan waits for the report and prints it.

```bash
uv tool install bluethroat
bluethroat auth login
bluethroat blueskills scan https://github.com/owner/repository --ref <commit>
bluethroat clone https://github.com/owner/repository --ref <commit>
bluethroat bluegate scan https://github.com/owner/repository --ref <commit>
```

`clone` and `bluegate scan` are the same BlueGate scan. Neither runs git. The text report ends with a clone command pinned to the scanned commit. `--json` prints the scan document from either product. On the BlueGate commands, `--agent` prints the fenced agent report. Without a format flag, the command prints text.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BLUETHROAT_GITHUB_CLIENT_ID` | GitHub App client ID. Defaults to the Bluethroat app (`Iv23liH0YU6oTHHz0i8O`). |
| `BLUETHROAT_GITHUB_CLIENT_SECRET` | Optional, for development only. Revokes directly at GitHub instead of through BlueSkills. Do not put this in the distributed client. |
| `BLUETHROAT_API_URL` | BlueSkills API origin. Defaults to `https://blueskills-web-hzdtf5buftehbya8.z03.azurefd.net`. |
| `BLUETHROAT_BLUEGATE_API_URL` | BlueGate API origin. Defaults to `https://bluegate.bluethroatlabs.com`. |

The same keys can live in `~/.config/bluethroat/config.json` as `github_client_id`, `github_client_secret`, `api_url`, and `bluegate_api_url`. Environment variables win.

The GitHub session is stored in the operating-system credential store, or in a mode `0600` file at `~/.config/bluethroat/credentials.json` when that store is unavailable.

`blueskills scan`, `clone`, and `bluegate scan` send `Authorization: Bearer` with the GitHub user access token. Each API checks it against the Bluethroat CLI GitHub App and applies limits to the GitHub user id. The access token lasts 8 hours; the CLI refreshes it with GitHub on its own, which needs no client secret for a device-flow login. `auth logout` asks BlueSkills (`POST /auth/github/revoke`) to revoke the token, since only the server holds the secret.
