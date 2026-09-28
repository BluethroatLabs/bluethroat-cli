# bluethroat

Command-line client for [BlueSkills](https://github.com/BluethroatLabs/blueskills-public). One GitHub login, then `scan` waits for the report and prints it.

```bash
uv tool install bluethroat
bluethroat auth login
bluethroat blueskills scan https://github.com/owner/repository --ref <commit>
```

Add `--json` for the machine-readable report. Without it, the command prints text.
