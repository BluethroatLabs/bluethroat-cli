"""Text rendering of a BlueSkills scan document.

The JSON from ``POST /scan.json`` and the JSON from ``GET /scans/{id}`` use
different envelopes. Both become the same text report.
"""

from __future__ import annotations

_ICONS = {
    "clean": "✓",
    "suspicious": "⚠",
    "malicious": "✗",
    "partial": "◐",
    "invalid": "⊘",
    "error": "✗",
}
_SEVERITY = {
    "critical": "CRIT",
    "high": "HIGH",
    "medium": "MED",
    "low": "LOW",
    "info": "INFO",
}


def outcome(doc: dict) -> tuple[str, bool]:
    """Headline verdict and whether the scan is a complete result."""
    units = _units(doc)
    verdict = _headline(doc, units)
    if str(doc.get("status") or "") == "failed" or verdict == "error":
        return verdict or "error", False
    complete = True
    if doc.get("complete") is False or doc.get("truncated") or doc.get("deadline_skipped"):
        complete = False
    if verdict in {"partial", "invalid"}:
        complete = False
    for unit in units:
        stats = unit.get("stats")
        if isinstance(stats, dict) and stats.get("incomplete"):
            complete = False
    return verdict, complete


def render(doc: dict) -> str:
    units = _units(doc)
    verdict = _headline(doc, units)
    lines: list[str] = []
    score = _headline_score(doc, units, verdict)
    lines.append(_headline_line(doc, verdict, score, several=len(units) > 1))
    commit = _commit(doc)
    if commit:
        lines.append(f"  commit: {_plain(commit)}")
    source = _source(doc)
    if source:
        lines.append(f"  source: {_plain(source)}")
    if doc.get("truncated"):
        lines.append("  ⚠ the package has more skills than this scan covered")
    if len(units) > 1:
        lines.append(f"  {len(units)} skills scanned. Review every block below.")
    if len(units) == 1:
        lines.extend(_unit_body(units[0], header=False))
    else:
        ordered = sorted(
            units,
            key=lambda unit: (
                -_rank(str(unit.get("verdict") or "")),
                str(unit.get("skill_name") or ""),
            ),
        )
        for unit in ordered:
            lines.append("")
            lines.extend(_unit_body(unit, header=True))
    return "\n".join(lines)


def _units(doc: dict) -> list[dict]:
    reports = doc.get("reports")
    if isinstance(reports, list) and reports:
        return [item for item in reports if isinstance(item, dict)]
    if "verdict" in doc:
        return [doc]
    return []


def _headline(doc: dict, units: list[dict]) -> str:
    for key in ("worst_verdict", "verdict"):
        value = doc.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    if not units:
        return "error"
    return max((str(unit.get("verdict") or "clean") for unit in units), key=_rank).lower()


def _headline_score(doc: dict, units: list[dict], verdict: str) -> int | None:
    if verdict in {"invalid", "error"}:
        return None
    if "score" in doc and "worst_verdict" not in doc:
        score = doc.get("score")
        return int(score) if isinstance(score, int) else None
    scores = [
        int(unit["risk_score"])
        for unit in units
        if str(unit.get("verdict") or "").lower() == verdict and isinstance(unit.get("risk_score"), int)
    ]
    if scores:
        return max(scores)
    for unit in units:
        if isinstance(unit.get("risk_score"), int):
            return int(unit["risk_score"])
    return None


def _headline_line(doc: dict, verdict: str, score: int | None, *, several: bool) -> str:
    icon = _ICONS.get(verdict, "?")
    name = _plain(str(_source(doc) or doc.get("slug") or "scan"))
    label = verdict.upper()
    if several:
        label = f"headline {label}"
    if verdict == "partial" and score is not None:
        return f"{icon} {name} — PARTIAL (observed risk {score}/100; CLEAN unreachable)"
    if score is None:
        reason = _plain(str(doc.get("error") or verdict))
        return f"{icon} {name} — {label} — not scored ({reason})"
    return f"{icon} {name} — {label} (risk {score}/100)"


def _unit_body(unit: dict, *, header: bool) -> list[str]:
    lines: list[str] = []
    verdict = str(unit.get("verdict") or "").lower()
    if header:
        score = unit.get("risk_score")
        shown = f"{score}/100" if isinstance(score, int) else "not scored"
        name = _plain(str(unit.get("skill") or unit.get("skill_name") or "skill"))
        lines.append(f"{_ICONS.get(verdict, '?')} {name} — {verdict.upper()} (risk {shown})")
    stats = unit.get("stats") if isinstance(unit.get("stats"), dict) else {}
    if stats.get("incomplete") or unit.get("complete") is False:
        missing = stats.get("missing_layers")
        detail = ", ".join(missing) if isinstance(missing, list) and missing else "a detection layer"
        lines.append(f"  ⚠ INCOMPLETE SCAN — {_plain(detail)} did not run")
    layers = unit.get("layers_run")
    if isinstance(layers, list) and layers:
        lines.append(f"  layers: {_plain(', '.join(str(item) for item in layers))}")
    findings = [item for item in unit.get("findings") or [] if isinstance(item, dict)]
    lines.append(f"  findings: {len(findings)}")
    for finding in findings:
        severity = _SEVERITY.get(str(finding.get("severity") or "").lower(), "INFO")
        title = _plain(str(finding.get("title") or finding.get("message") or "Finding"))
        layer = _plain(str(finding.get("layer") or ""))
        where = _plain(str(finding.get("file") or "(skill)"))
        if finding.get("line"):
            where += f":{finding['line']}"
        detail = _plain(str(finding.get("detail") or ""))
        lines.append(f"  [{severity}] {title}  ({layer})")
        lines.append(f"         {where} — {detail}")
        if finding.get("evidence"):
            lines.append(f"         evidence: {_plain(str(finding['evidence']))}")
    return lines


def _source(doc: dict) -> str:
    raw = doc.get("source")
    if isinstance(raw, str) and raw:
        return raw
    entered = doc.get("input")
    if isinstance(entered, dict) and isinstance(entered.get("source"), str):
        return str(entered["source"])
    return ""


def _commit(doc: dict) -> str:
    if isinstance(doc.get("commit"), str):
        return str(doc["commit"])
    entered = doc.get("input")
    if isinstance(entered, dict) and isinstance(entered.get("commit"), str):
        return str(entered["commit"])
    return ""


def _rank(verdict: str) -> int:
    return {
        "clean": 0,
        "suspicious": 1,
        "partial": 2,
        "malicious": 3,
        "invalid": 4,
        "error": 5,
    }.get(verdict.lower(), 0)


def _plain(text: str) -> str:
    flattened = " ".join(text.split())
    if len(flattened) > 400:
        return flattened[:399] + "…"
    return flattened
