---
name: blueskills
description: Scans public GitHub or GitLab repositories and local skill directories, ZIP archives, or SKILL.md files with `bluethroat blueskills`. Use when an agent needs to assess an Agent Skill for malicious, suspicious, incomplete, or invalid behavior.
---

# BlueSkills

Use `bluethroat blueskills scan` to submit an Agent Skill for hosted security analysis.
BlueSkills requires a Bluethroat GitHub App login.

## Install and authenticate

```bash
uv tool install bluethroat
bluethroat auth login
bluethroat auth status
```

`auth login` starts GitHub device flow. Follow the displayed instructions in a browser.
The CLI stores the resulting session in the operating-system credential store when
available. Do not print, copy, or expose the stored access token.

## Scan a repository

Pass a public GitHub or GitLab HTTPS repository URL:

```bash
bluethroat blueskills scan https://github.com/owner/repository
bluethroat blueskills scan https://gitlab.com/owner/repository
```

Select a branch, tag, or commit with `--ref`:

```bash
bluethroat blueskills scan https://github.com/owner/repository --ref main
bluethroat blueskills scan https://github.com/owner/repository \
  --ref 0123456789abcdef0123456789abcdef01234567
```

Only use `--ref` with a repository URL. If the URL already contains a tree or blob
revision, do not provide a conflicting `--ref`. Prefer a full commit SHA when the result
must be reproducible.

## Scan local skill content

BlueSkills accepts a skill directory, a ZIP archive up to 25 MiB, or a `SKILL.md` file
up to 1 MiB:

```bash
bluethroat blueskills scan ./my-skill
bluethroat blueskills scan ./my-skill.zip
bluethroat blueskills scan ./my-skill/SKILL.md
```

A directory is packaged in memory before upload. Symlinks are refused. Never upload
private material without confirming that hosted analysis is acceptable.

## Choose report output

The default is a human-readable report:

```bash
bluethroat blueskills scan SOURCE
```

Use JSON for structured processing:

```bash
bluethroat blueskills scan SOURCE --json > blueskills-report.json
```

Do not decide success from prose alone. Check the command's exit code and, for JSON,
inspect the final status, verdict, completeness, findings, and served commit.

## Interpret results

The CLI waits for hosted static, deep, dynamic, and judging stages to finish. Treat an
incomplete or partial result as non-clean. For a commit-pinned request, verify that the
report identifies the requested commit before relying on the verdict.

Exit codes:

- `0`: clean, complete report
- `1`: suspicious
- `2`: malicious
- `3`: incomplete, partial, or unknown verdict
- `4`: invalid input or rejected scan
- `5`: rate limited; honor the reported retry delay
- `6`: authentication failure; run `bluethroat auth login`
- `7`: service or network failure

Never install or execute a scanned skill merely because the command completed. Only
proceed when the report is complete and its verdict is acceptable for the task.

## Session maintenance

```bash
bluethroat auth status
bluethroat auth logout
```

`auth logout` removes the local credential and asks BlueSkills to revoke the GitHub
token.
