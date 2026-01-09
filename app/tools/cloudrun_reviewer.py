from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional,  Literal
import re
from pydantic import BaseModel, Field
import yaml
from langchain_core.tools import tool
from app.tools.cloudrun_review_formatter import *
from pathlib import Path
import json
from app.utils import pick_workspace_root
from datetime import datetime
from app.runtime import *
RUN_ENV = get_run_env()



@dataclass
class Finding:
    severity: str  # HIGH | MEDIUM | LOW | INFO
    code: str
    message: str
    recommendation: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "recommendation": self.recommendation,
        }


def _to_int(x: Any) -> Optional[int]:
    try:
        return int(str(x))
    except Exception:
        return None


def _get(d: Dict[str, Any], path: List[str], default=None):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _has_secret_ref_yaml(container: Dict[str, Any]) -> bool:
    for env in container.get("env", []) or []:
        if isinstance(env, dict) and env.get("valueFrom", {}).get("secretKeyRef"):
            return True
    return False


def _has_plaintext_key_yaml(container: Dict[str, Any]) -> bool:
    for env in container.get("env", []) or []:
        if not isinstance(env, dict):
            continue
        name = str(env.get("name", "")).upper()
        if any(k in name for k in ["KEY", "TOKEN", "SECRET", "PASSWORD"]):
            if "value" in env and env.get("value"):
                return True
    return False


def review_service_yaml(yaml_text: str) -> Dict[str, Any]:
    doc = yaml.safe_load(yaml_text)

    findings: List[Finding] = []

    name = _get(doc, ["metadata", "name"], "")
    labels = _get(doc, ["metadata", "labels"], {}) or {}
    ann = _get(doc, ["spec", "template", "metadata", "annotations"], {}) or {}
    spec = _get(doc, ["spec", "template", "spec"], {}) or {}

    # Labels
    if not labels.get("app") or not labels.get("env"):
        findings.append(Finding(
            "LOW", "YAML001",
            "Missing recommended labels (app/env).",
            "Add metadata.labels.app and metadata.labels.env for ops/billing filtering."
        ))

    # Ingress
    ingress = ann.get("run.googleapis.com/ingress")
    if ingress in (None, "", "all"):
        findings.append(Finding(
            "INFO", "YAML010",
            f"Ingress is '{ingress or 'default(all)'}'.",
            "If this should be internal-only, set run.googleapis.com/ingress: internal."
        ))

    # Scaling
    min_scale = _to_int(ann.get("autoscaling.knative.dev/minScale"))
    max_scale = _to_int(ann.get("autoscaling.knative.dev/maxScale"))
    if min_scale is not None and min_scale > 0:
        findings.append(Finding(
            "MEDIUM", "YAML020",
            f"minScale is {min_scale} (instances stay warm -> cost).",
            "Set minScale to 0 unless you need consistently low latency."
        ))
    if max_scale is None:
        findings.append(Finding(
            "MEDIUM", "YAML021",
            "maxScale not set (no explicit cap).",
            "Set autoscaling.knative.dev/maxScale to limit cost/blast radius."
        ))

    # Runtime SA
    sa = spec.get("serviceAccountName")
    if not sa:
        findings.append(Finding(
            "HIGH", "YAML030",
            "No runtime service account specified.",
            "Set spec.template.spec.serviceAccountName to a dedicated SA (least privilege)."
        ))
    elif sa.endswith("-compute@developer.gserviceaccount.com"):
        findings.append(Finding(
            "MEDIUM", "YAML031",
            f"Using default compute service account: {sa}.",
            "Use a dedicated runtime SA and grant only needed roles (e.g., secretAccessor)."
        ))

    # Concurrency/timeout
    conc = _to_int(spec.get("containerConcurrency"))
    timeout = _to_int(spec.get("timeoutSeconds"))
    if conc is not None and conc >= 50:
        findings.append(Finding(
            "MEDIUM", "YAML040",
            f"containerConcurrency is {conc} (may hurt LLM/CPU-bound latency).",
            "Consider 5–20 for CPU/LLM workloads; benchmark and tune."
        ))
    if timeout is not None and timeout > 900:
        findings.append(Finding(
            "LOW", "YAML041",
            f"timeoutSeconds is {timeout} (quite high).",
            "Lower timeout unless truly needed; long timeouts can increase cost & stuck requests."
        ))

    # CPU boost / throttling
    if ann.get("run.googleapis.com/cpu-throttling") == "false":
        findings.append(Finding(
            "INFO", "YAML050",
            "CPU throttling disabled (cpu-boost-like behavior).",
            "This can improve latency but may increase cost; keep if you need it."
        ))

    # Container checks
    containers = spec.get("containers", []) or []
    if not containers:
        findings.append(Finding(
            "HIGH", "YAML060",
            "No containers defined.",
            "Define spec.template.spec.containers with image/ports/env/resources."
        ))
    else:
        c0 = containers[0]
        img = str(c0.get("image", "") or "")
        if not img or "IMAGE_PLACEHOLDER" in img:
            findings.append(Finding(
                "HIGH", "YAML061",
                "Container image is missing or placeholder.",
                "Set containers[0].image to your Artifact Registry image (with tag)."
            ))

        if _has_plaintext_key_yaml(c0):
            findings.append(Finding(
                "HIGH", "YAML070",
                "Possible plaintext secret in env (KEY/TOKEN/SECRET with value).",
                "Move secrets to Secret Manager and reference with valueFrom.secretKeyRef."
            ))

        if not _has_secret_ref_yaml(c0):
            findings.append(Finding(
                "MEDIUM", "YAML071",
                "No Secret Manager references found in env.",
                "If you use API keys, reference Secret Manager via env.valueFrom.secretKeyRef."
            ))

        # Resources sanity
        limits = (c0.get("resources") or {}).get("limits") or {}
        if not limits.get("cpu") or not limits.get("memory"):
            findings.append(Finding(
                "LOW", "YAML080",
                "CPU/memory limits not fully specified.",
                "Set resources.limits.cpu and resources.limits.memory for predictability."
            ))

    return _format_report("yaml", name or "(unknown)", findings)


def _sh_has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) is not None


def _sh_capture(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1) if m else None


def review_deploy_sh(sh_text: str) -> Dict[str, Any]:
    findings: List[Finding] = []

    # Auth exposure
    if _sh_has(r"--allow-unauthenticated\b", sh_text):
        findings.append(Finding(
            "HIGH", "SH001",
            "Service allows unauthenticated access (public).",
            "Use --no-allow-unauthenticated for private services, and grant roles/run.invoker to callers."
        ))

    # Runtime SA
    sa = _sh_capture(r"--service-account\s+\"?([^\s\"\\]+)", sh_text)
    if not sa:
        findings.append(Finding(
            "MEDIUM", "SH010",
            "No --service-account specified (may use default compute SA).",
            "Specify a dedicated runtime SA via --service-account and grant least-privilege roles."
        ))
    elif sa.endswith("-compute@developer.gserviceaccount.com"):
        findings.append(Finding(
            "MEDIUM", "SH011",
            f"Using default compute SA: {sa}.",
            "Use a dedicated runtime SA (e.g., cloudrun-runtime@...) and grant only required roles."
        ))

    # Secrets
    if _sh_has(r"--set-env-vars\s+.*(KEY|TOKEN|SECRET|PASSWORD)\s*=", sh_text) and not _sh_has(r"--set-secrets\b", sh_text):
        findings.append(Finding(
            "HIGH", "SH020",
            "Potential secret is being set via --set-env-vars (plaintext).",
            "Use Secret Manager and pass via --set-secrets instead."
        ))
    if not _sh_has(r"--set-secrets\b", sh_text):
        findings.append(Finding(
            "MEDIUM", "SH021",
            "No --set-secrets found.",
            "If the app needs API keys, use --set-secrets VAR=secret:version and grant secretAccessor."
        ))

    # Scaling
    min_instances = _sh_capture(r"--min-instances\s+\"?(\d+)", sh_text)
    if min_instances is not None and int(min_instances) > 0:
        findings.append(Finding(
            "MEDIUM", "SH030",
            f"min-instances is {min_instances} (always-on cost).",
            "Set --min-instances 0 unless you need warm instances for latency."
        ))

    max_instances = _sh_capture(r"--max-instances\s+\"?(\d+)", sh_text)
    if max_instances is None:
        findings.append(Finding(
            "MEDIUM", "SH031",
            "No --max-instances cap found.",
            "Set --max-instances to limit cost/blast radius."
        ))

    # Concurrency
    conc = _sh_capture(r"--concurrency\s+\"?(\d+)", sh_text)
    if conc is not None and int(conc) >= 50:
        findings.append(Finding(
            "MEDIUM", "SH040",
            f"--concurrency is {conc} (may hurt LLM/CPU workloads).",
            "Consider 5–20 for CPU/LLM workloads; benchmark and tune."
        ))

    # Timeout
    timeout = _sh_capture(r"--timeout\s+\"?(\d+)", sh_text)
    if timeout is not None and int(timeout) > 900:
        findings.append(Finding(
            "LOW", "SH050",
            f"--timeout is {timeout}s (quite high).",
            "Lower timeout unless needed; long timeouts can increase cost."
        ))

    # CPU boost
    if _sh_has(r"--cpu-boost\b", sh_text):
        findings.append(Finding(
            "INFO", "SH060",
            "--cpu-boost is enabled.",
            "Good for latency; verify you need it to avoid extra cost."
        ))

    return _format_report("sh", "(from deploy script)", findings)


def _format_report(kind: str, service: str, findings: List[Finding]) -> Dict[str, Any]:
    by = {"HIGH": [], "MEDIUM": [], "LOW": [], "INFO": []}
    for f in findings:
        by[f.severity].append(f.as_dict())

    score = (
        len(by["HIGH"]) * 10
        + len(by["MEDIUM"]) * 5
        + len(by["LOW"]) * 2
        + len(by["INFO"]) * 1
    )

    return {
        "kind": kind,
        "service": service,
        "score": score,
        "summary": {k: len(v) for k, v in by.items()},
        "findings": by,
    }


def review_cloudrun_config(text: str, kind: str = "auto") -> Dict[str, Any]:
    """
    kind: auto | yaml | sh
    """
    k = (kind or "auto").lower().strip()

    if k == "yaml":
        return review_service_yaml(text)
    if k == "sh":
        return review_deploy_sh(text)

    # auto-detect
    looks_yaml = ("apiVersion:" in text and "kind:" in text) or re.search(r"^\s*apiVersion\s*:", text, re.M)
    looks_sh = ("gcloud run deploy" in text) or re.search(r"^\s*#!/usr/bin/env\s+bash", text, re.M)

    if looks_yaml and not looks_sh:
        return review_service_yaml(text)
    if looks_sh and not looks_yaml:
        return review_deploy_sh(text)

    # ambiguous: return both
    return {
        "kind": "auto",
        "reports": [
            review_service_yaml(text) if looks_yaml else {"kind": "yaml", "skipped": True},
            review_deploy_sh(text) if looks_sh else {"kind": "sh", "skipped": True},
        ],
    }
class CloudRunReviewPathInput(BaseModel):
    path: str = Field(..., description="Path to service.yaml or deploy.sh inside workspace")
    kind: Literal["auto", "yaml", "sh"] = Field("auto", description="Input format: auto/yaml/sh")
    save_summary: bool = Field(True, description="Save markdown report to summary/")
    summary_dir: str = Field("summary", description="Folder name under repo root to save report")

# ----------------------------
# Dual-mode guard on file read
# ----------------------------
def _safe_read_file(path: str) -> str:
    """
    Read a file safely depending on the environment.
    Cloud Run: allow only /tmp or workspace paths.
    """
    p = Path(path).expanduser().resolve()
    root = pick_workspace_root()
    try:
        p.relative_to(root)
    except ValueError:
        if RUN_ENV == RunEnv.CLOUDRUN:
            raise ValueError(f"Cloud Run can only read files under {root}, attempted: {p}")
    if not p.exists() or not p.is_file():
        raise ValueError(f"File not found: {p}")
    return p.read_text(encoding="utf-8", errors="ignore")

def save_md(md: str, summary_dir: str = "summary", prefix: str = "cloudrun_review") -> str:
    root = pick_workspace_root()
    out = (root / summary_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    p = out / f"{prefix}_{ts}.md"
    p.write_text(md, encoding="utf-8")
    return str(p)

@tool("cloudrun_review_report", args_schema=CloudRunReviewPathInput)
def cloudrun_review_report(path: str, kind: str = "auto", save_summary: bool = True, summary_dir: str = "summary") -> Dict[str, Any]:
    """
    Read a local file (service.yaml or deploy script) and return a formatted Cloud Run review report.
    Also saves the markdown report into summary/ by default.
    """
    text = _safe_read_file(path)
    raw = review_cloudrun_config(text=text, kind=kind)
    report = format_cloudrun_review(raw)

    md = report.get("markdown") or report.get("md") or ""
    if RUN_ENV == RunEnv.CLOUDRUN and save_summary and md:
        report["saved_path"] = save_md(md, summary_dir= Path("/tmp") / "summary")
    if save_summary and md:
        report["saved_path"] = save_md(md, summary_dir=summary_dir)

    report["source_path"] = path
    report["env"] = RUN_ENV.value
    return report
