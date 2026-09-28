# bluethroat

Command-line client for BlueSkills. Log in once with the Bluethroat GitHub App, then `scan` waits for the report and prints it.

```bash
uv tool install .
bluethroat auth login
bluethroat blueskills scan https://github.com/owner/repository --ref <commit>
```

Add `--json` for the scan document returned by the API. Without it, the command prints text.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BLUETHROAT_GITHUB_CLIENT_ID` | GitHub App client ID. Defaults to the Bluethroat app (`Iv23liH0YU6oTHHz0i8O`). |
| `BLUETHROAT_GITHUB_CLIENT_SECRET` | Optional, for development only. Revokes directly at GitHub instead of through BlueSkills. Do not put this in the distributed client. |
| `BLUETHROAT_API_URL` | BlueSkills API origin. Defaults to `https://blueskills-web-hzdtf5buftehbya8.z03.azurefd.net`. |

The same keys can live in `~/.config/bluethroat/config.json` as `github_client_id`, `github_client_secret`, and `api_url`. Environment variables win.

The GitHub session is stored in the operating-system credential store, or in a mode `0600` file at `~/.config/bluethroat/credentials.json` when that store is unavailable.

`scan` sends `Authorization: Bearer` with the GitHub user access token. BlueSkills checks it against the Bluethroat CLI GitHub App and applies limits to the GitHub user id. The access token lasts 8 hours; the CLI refreshes it with GitHub on its own, which needs no client secret for a device-flow login. `auth logout` asks BlueSkills (`POST /auth/github/revoke`) to revoke the token, since only the server holds the secret.
