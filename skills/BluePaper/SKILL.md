---
name: bluepaper
description: Rebuilds an untrusted document as a safe PDF with `bluethroat bluepaper`, then reports byte indicators found in the rebuilt output. Use when an agent needs a flattened PDF representation of a suspicious or untrusted document.
---

# BluePaper

Use `bluethroat bluepaper convert` to upload a document for isolated pixel rebuilding
and write the rebuilt result as a PDF. BluePaper is a hosted service and requires a
Bluethroat GitHub App login.

## Install and authenticate

```bash
uv tool install bluethroat
bluethroat auth login
bluethroat auth status
```

`auth login` starts GitHub device flow. Follow the displayed instructions in a browser.
Do not print, copy, or expose the stored access token.

## Convert a document

```bash
bluethroat bluepaper convert suspicious.docx
```

The CLI uploads the source, waits for the isolated rebuild, downloads the safe PDF,
prints the conversion and byte-indicator report, and writes:

```text
<source stem>-safe.pdf
```

For example, `suspicious.docx` becomes `suspicious-safe.pdf`. The source file is never
overwritten.

Because the source is uploaded, confirm that hosted processing is permitted before
submitting confidential, regulated, or private material. Symlink sources are refused.

## Choose the output

Use `-o` or `--output` to select a different PDF path:

```bash
bluethroat bluepaper convert suspicious.docx -o reviewed/safe-copy.pdf
```

The output directory must already exist. Existing output files are refused by default.
Only pass `--force` when replacing that exact regular output file was explicitly
requested:

```bash
bluethroat bluepaper convert suspicious.docx \
  -o reviewed/safe-copy.pdf --force
```

The output must differ from the source. Symlink outputs are always refused.

## Request OCR

Pass a Tesseract language code when OCR is needed:

```bash
bluethroat bluepaper convert scanned-document.pdf --ocr-lang eng
```

Use the language appropriate to the document. Do not assume `eng` for content in another
language.

## Structured output

Use `--json` for automation:

```bash
bluethroat bluepaper convert suspicious.docx \
  -o suspicious-safe.pdf --json > bluepaper-report.json
```

JSON mode still writes the safe PDF. Its output document contains the conversion
status, byte-indicator report, and written output path.

## Verify the result

1. Confirm the conversion status is `succeeded`.
2. Confirm the reported output path exists.
3. Inspect any byte-indicator hits and their counts.
4. Open the rebuilt PDF and verify that required visible content survived.
5. Continue treating failed or indicator-bearing conversions according to the task's
   security policy.

A successful rebuild with indicators is not equivalent to a clean result. The report
can include an indicator ID, count, first offset, and caveat.

Exit codes:

- `0`: rebuild succeeded with no byte indicators
- `1`: rebuild succeeded but byte indicators were found
- `4`: invalid source, output path, format, size, or request
- `5`: rate limited; honor the reported retry delay
- `6`: authentication failure; run `bluethroat auth login`
- `7`: conversion, service, network, or download failure; no PDF is written for a
  failed rebuild

## Session maintenance

```bash
bluethroat auth status
bluethroat auth logout
```
