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
| `BLUETHROAT_GITHUB_CLIENT_ID` | GitHub App client ID. Required for login. |
| `BLUETHROAT_GITHUB_CLIENT_SECRET` | Optional. Enables token refresh and revocation. Do not put this in the distributed client. |
| `BLUETHROAT_API_URL` | BlueSkills API origin. Defaults to `https://blueskills.bluethroatlabs.com`. |

The same keys can live in `~/.config/bluethroat/config.json` as `github_client_id`, `github_client_secret`, and `api_url`. Environment variables win.

The GitHub session is stored in the operating-system credential store, or in a mode `0600` file at `~/.config/bluethroat/credentials.json` when that store is unavailable.

`scan` sends `Authorization: Bearer` with the GitHub user access token. BlueSkills does not check that header yet. When it does, it should verify the token and apply limits to the GitHub user id.
