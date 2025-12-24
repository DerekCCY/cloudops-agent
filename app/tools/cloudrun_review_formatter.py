from __future__ import annotations
from typing import Any, Dict, List

SEV_ORDER = ["HIGH", "MEDIUM", "LOW", "INFO"]

def _collect_findings(report: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flatten findings from either single report or auto multi-report."""
    if report.get("kind") == "auto" and isinstance(report.get("reports"), list):
        out = []
        for r in report["reports"]:
            if isinstance(r, dict) and r.get("findings"):
                out.extend(_collect_findings(r))
        return out

    findings = report.get("findings") or {}
    out = []
    for sev in SEV_ORDER:
        for f in findings.get(sev, []) or []:
            if isinstance(f, dict):
                f2 = dict(f)
                f2["severity"] = sev
                out.append(f2)
    return out


def _top_risks(findings: List[Dict[str, str]], n: int = 3) -> List[Dict[str, str]]:
    score = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
    findings_sorted = sorted(
        findings,
        key=lambda f: (-score.get(f.get("severity", ""), 0), f.get("code", ""))
    )
    return findings_sorted[:n]


def format_cloudrun_review(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns a structured, human-readable report:
    - summary counts
    - go/no-go
    - top risks (with recommendations)
    - a concise markdown string for chat output
    """
    # Merge summary
    if report.get("kind") == "auto" and isinstance(report.get("reports"), list):
        summaries = []
        for r in report["reports"]:
            if isinstance(r, dict) and r.get("summary"):
                summaries.append(r["summary"])
        summary = {sev: sum(s.get(sev, 0) for s in summaries) for sev in SEV_ORDER}
        service = "multiple"
    else:
        summary = report.get("summary") or {sev: 0 for sev in SEV_ORDER}
        service = report.get("service") or "(unknown)"

    findings = _collect_findings(report)
    top = _top_risks(findings, n=3)

    go = "NO-GO" if summary.get("HIGH", 0) > 0 else "GO"

    # Markdown output
    lines = []
    lines.append(f"### Cloud Run Review ({service})")
    lines.append("")
    lines.append(f"**Decision:** `{go}`")
    lines.append("")
    lines.append("**Summary:** " + ", ".join([f"{sev}: {summary.get(sev, 0)}" for sev in SEV_ORDER]))
    lines.append("")
    lines.append("#### Top risks")
    if not top:
        lines.append("- (none)")
    else:
        for f in top:
            sev = f.get("severity", "?")
            code = f.get("code", "")
            msg = f.get("message", "")
            rec = f.get("recommendation", "")
            lines.append(f"- **{sev} {code}** — {msg}")
            if rec:
                lines.append(f"  - Fix: {rec}")

    # If NO-GO, add a next-step line
    if go == "NO-GO":
        lines.append("")
        lines.append("#### Next step")
        lines.append("- Fix all **HIGH** findings, then re-run the review and deploy.")

    return {
        "decision": go,
        "summary": summary,
        "top_risks": top,
        "markdown": "\n".join(lines),
    }
