# app/tools/cloudrun_config_generator.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import re
import textwrap
from langchain_core.tools import tool
from app._paths import pick_workspace_root

try:
    import yaml  # PyYAML
except Exception as e:  # pragma: no cover
    yaml = None


# -----------------------
# Security / Validation
# -----------------------

_SUSPECT_SECRET_PATTERNS = [
    r"\bsk-[A-Za-z0-9]{20,}\b",          # OpenAI-like
    r"\bya29\.[A-Za-z0-9\-_]+\b",        # Google OAuth access token
    r"\bAIza[0-9A-Za-z\-_]{20,}\b",      # Google API key
    r"\bghp_[A-Za-z0-9]{20,}\b",         # GitHub token
    r"-----BEGIN (?:RSA |EC |)PRIVATE KEY-----",  # private key
]

_SUSPECT_SECRET_RE = re.compile("|".join(_SUSPECT_SECRET_PATTERNS))


def _assert_no_secret_value(value: str, *, field_name: str) -> None:
    """Fail fast if a value looks like a secret."""
    if value is None:
        return
    if not isinstance(value, str):
        return
    if _SUSPECT_SECRET_RE.search(value):
        raise ValueError(
            f"{field_name} contains a value that looks like a secret. "
            "Do NOT store secrets in config templates. Use Secret Manager refs instead."
        )


def _require_pyyaml() -> None:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is not installed but required to generate service.yaml. "
            "Add `pyyaml` to requirements.txt."
        )


# -----------------------
# Models
# -----------------------

@dataclass
class SecretEnvRef:
    """Represents an env var sourced from Secret Manager (no secret value stored here)."""
    secret: str
    version: str = "latest"

    def to_env_item(self, env_name: str) -> Dict[str, Any]:
        # Cloud Run (Knative) style:
        # env:
        # - name: OPENAI_API_KEY
        #   valueFrom:
        #     secretKeyRef:
        #       name: openai-api-key
        #       key: latest
        return {
            "name": env_name,
            "valueFrom": {
                "secretKeyRef": {
                    "name": self.secret,
                    "key": self.version,
                }
            },
        }


@dataclass
class CloudRunConfig:
    # Identity
    service_name: str = "${SERVICE_NAME}"
    project_id: str = "${PROJECT_ID}"
    region: str = "${REGION}"

    # Runtime
    image: str = "${IMAGE}"  # Intentionally placeholder in Day 6
    port: int = 8080

    # Resources / scaling
    cpu: str = "1"
    memory: str = "512Mi"
    concurrency: int = 80
    timeout_seconds: int = 300
    min_instances: int = 0
    max_instances: int = 3

    # Network / auth
    ingress: str = "all"  # all | internal | internal-and-cloud-load-balancing
    allow_unauthenticated: bool = False

    # IAM
    service_account: str = "${SERVICE_ACCOUNT}"  # optional placeholder

    # Env
    env: Dict[str, str] = field(default_factory=lambda: {"WORKSPACE_ROOT": "/app"})
    secret_env: Dict[str, SecretEnvRef] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.service_name:
            raise ValueError("service_name must not be empty")
        if self.ingress not in {"all", "internal", "internal-and-cloud-load-balancing"}:
            raise ValueError("ingress must be one of: all|internal|internal-and-cloud-load-balancing")
        if self.concurrency <= 0:
            raise ValueError("concurrency must be > 0")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.min_instances < 0 or self.max_instances < 0:
            raise ValueError("min_instances/max_instances must be >= 0")
        if self.max_instances and self.min_instances > self.max_instances:
            raise ValueError("min_instances cannot be > max_instances")

        # env must NOT contain secret-like values
        for k, v in (self.env or {}).items():
            _assert_no_secret_value(v, field_name=f"env[{k}]")

        # secret_env must not contain secret values by design (only refs)
        for k, ref in (self.secret_env or {}).items():
            if not ref.secret:
                raise ValueError(f"secret_env[{k}].secret is empty")
            if not ref.version:
                raise ValueError(f"secret_env[{k}].version is empty")

        # prevent obvious misuse: putting secret-ish strings in service fields
        _assert_no_secret_value(self.image, field_name="image")
        _assert_no_secret_value(self.service_account, field_name="service_account")

    def to_service_yaml_dict(self) -> Dict[str, Any]:
        self.validate()

        env_items = []
        for k, v in (self.env or {}).items():
            env_items.append({"name": k, "value": v})

        # Secret refs appended after non-secret env
        for env_name, ref in (self.secret_env or {}).items():
            env_items.append(ref.to_env_item(env_name))

        # If service_account placeholder is left as empty string by caller, omit it
        spec: Dict[str, Any] = {
            "containerConcurrency": self.concurrency,
            "timeoutSeconds": self.timeout_seconds,
            "containers": [
                {
                    "image": self.image,
                    "ports": [{"name": "http1", "containerPort": int(self.port)}],
                    "env": env_items,
                }
            ],
        }
        if self.service_account and self.service_account != "${SERVICE_ACCOUNT}":
            spec["serviceAccountName"] = self.service_account
        elif self.service_account == "${SERVICE_ACCOUNT}":
            # keep placeholder so users know where to put it
            spec["serviceAccountName"] = self.service_account

        service = {
            "apiVersion": "serving.knative.dev/v1",
            "kind": "Service",
            "metadata": {
                "name": self.service_name,
                "annotations": {
                    "run.googleapis.com/ingress": self.ingress,
                },
            },
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "autoscaling.knative.dev/minScale": str(self.min_instances),
                            "autoscaling.knative.dev/maxScale": str(self.max_instances),
                            "run.googleapis.com/cpu": str(self.cpu),
                            "run.googleapis.com/memory": str(self.memory),
                        }
                    },
                    "spec": spec,
                }
            },
        }
        return service


# -----------------------
# Generator
# -----------------------

def generate_service_yaml(config: CloudRunConfig) -> str:
    _require_pyyaml()
    data = config.to_service_yaml_dict()
    # stable & readable output
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def generate_deploy_sh(config: CloudRunConfig) -> str:
    # Day 6: template only, default dry-run. No secrets.
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        # =====================================
        # Cloud Run deploy template (Day 6)
        # - No secrets stored here
        # - Default: dry-run
        # =====================================

        PROJECT_ID="${{PROJECT_ID:-}}"
        REGION="${{REGION:-{config.region if config.region else "us-central1"}}}"
        SERVICE_NAME="${{SERVICE_NAME:-{config.service_name if config.service_name else "cloudops-agent"}}}"
        IMAGE="${{IMAGE:-}}"  # Day 10 will set this after build+push

        SERVICE_YAML="${{SERVICE_YAML:-service.yaml}}"
        DRY_RUN="${{DRY_RUN:-true}}"  # true|false

        if [[ -z "$PROJECT_ID" ]]; then
          echo "ERROR: PROJECT_ID is empty. Example: export PROJECT_ID='my-gcp-project'"
          exit 1
        fi

        if [[ ! -f "$SERVICE_YAML" ]]; then
          echo "ERROR: $SERVICE_YAML not found."
          exit 1
        fi

        echo "Project: $PROJECT_ID"
        echo "Region:  $REGION"
        echo "Service: $SERVICE_NAME"
        echo "YAML:    $SERVICE_YAML"
        echo "Image:   ${{IMAGE:-<empty>}}"
        echo

        echo "Render placeholders -> /tmp/service.rendered.yaml"
        export PROJECT_ID REGION SERVICE_NAME IMAGE
        envsubst < "$SERVICE_YAML" > /tmp/service.rendered.yaml

        echo
        if [[ "$DRY_RUN" == "true" ]]; then
          echo "[DRY RUN] Validating Cloud Run config (no deployment)"
          gcloud run services replace /tmp/service.rendered.yaml \\
            --project "$PROJECT_ID" --region "$REGION" --dry-run
          echo
          echo "OK. Day 10: set IMAGE and run with DRY_RUN=false (remove --dry-run)."
        else
          echo "[DEPLOY] Applying Cloud Run config"
          gcloud run services replace /tmp/service.rendered.yaml \\
            --project "$PROJECT_ID" --region "$REGION"
        fi
        """
    )


def generate_readme_md(config: CloudRunConfig) -> str:
    # Provide guidance; no secrets.
    return textwrap.dedent(
        f"""\
        # Cloud Run Config (Generated on Day 6)

        This folder contains Cloud Run deployment configuration templates.

        ## Files
        - `service.yaml`: declarative Cloud Run service config (**no secret values**)
        - `cloudrun_deploy.sh`: helper script template (defaults to **dry-run**)

        ## Fill placeholders
        You must provide:
        - `PROJECT_ID`
        - `REGION`
        - `SERVICE_NAME`
        - `IMAGE` (**Day 10** after build+push)

        Example:
        ```bash
        export PROJECT_ID="YOUR_GCP_PROJECT"
        export REGION="{config.region if config.region else "us-central1"}"
        export SERVICE_NAME="{config.service_name if config.service_name else "cloudops-agent"}"
        export IMAGE="us-docker.pkg.dev/YOUR_GCP_PROJECT/REPO/IMAGE:TAG"
        envsubst < service.yaml > /tmp/service.rendered.yaml
        ```

        ## Secrets (do NOT put secret values in repo)
        Create secrets in Secret Manager (example):
        ```bash
        gcloud secrets create openai-api-key --replication-policy="automatic"
        # then add secret versions via CLI or console (DO NOT commit values)
        ```

        In `service.yaml`, secrets are referenced like:
        ```yaml
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: openai-api-key
              key: latest
        ```

        ## Day 6: Dry run (no deployment)
        ```bash
        chmod +x ./cloudrun_deploy.sh
        export PROJECT_ID="YOUR_GCP_PROJECT"
        export REGION="{config.region if config.region else "us-central1"}"
        export SERVICE_NAME="{config.service_name if config.service_name else "cloudops-agent"}"
        export DRY_RUN="true"
        ./cloudrun_deploy.sh
        ```

        ## Day 10: Real deployment
        Build + push image first, then:
        ```bash
        export IMAGE="us-docker.pkg.dev/YOUR_GCP_PROJECT/REPO/IMAGE:TAG"
        export DRY_RUN="false"
        ./cloudrun_deploy.sh
        ```
        """
    )


def generate_cloudrun_templates(config_dict: Dict[str, Any]) -> Dict[str, str]:
    """
    Main entry:
    Input: config_dict (from agent/tool call)
    Output: file contents (service.yaml, cloudrun_deploy.sh, README_cloudrun.md)
    """
    # Convert dict -> CloudRunConfig
    secret_env_dict = {}
    for env_name, ref in (config_dict.get("secret_env") or {}).items():
        secret_env_dict[env_name] = SecretEnvRef(
            secret=ref.get("secret", ""),
            version=ref.get("version", "latest"),
        )

    cfg = CloudRunConfig(
        service_name=config_dict.get("service_name", "${SERVICE_NAME}"),
        project_id=config_dict.get("project_id", "${PROJECT_ID}"),
        region=config_dict.get("region", "${REGION}"),
        image=config_dict.get("image", "${IMAGE}"),
        port=int(config_dict.get("port", 8080)),
        cpu=str(config_dict.get("cpu", "1")),
        memory=str(config_dict.get("memory", "512Mi")),
        concurrency=int(config_dict.get("concurrency", 80)),
        timeout_seconds=int(config_dict.get("timeout_seconds", 300)),
        min_instances=int(config_dict.get("min_instances", 0)),
        max_instances=int(config_dict.get("max_instances", 3)),
        ingress=str(config_dict.get("ingress", "all")),
        allow_unauthenticated=bool(config_dict.get("allow_unauthenticated", False)),
        service_account=config_dict.get("service_account", "${SERVICE_ACCOUNT}"),
        env=dict(config_dict.get("env") or {"WORKSPACE_ROOT": "/app"}),
        secret_env=secret_env_dict,
    )

    service_yaml = generate_service_yaml(cfg)
    deploy_sh = generate_deploy_sh(cfg)
    readme_md = generate_readme_md(cfg)

    return {
        "service.yaml": service_yaml,
        "cloudrun_deploy.sh": deploy_sh,
        "README_cloudrun.md": readme_md,
    }



from pathlib import Path
from datetime import datetime

def _repo_root() -> Path:
    # 以 app/tools/.. 往上兩層回到 repo root
    return Path(__file__).resolve().parents[2]

def write_templates(files: dict[str, str], output_dir: str = "deploy") -> dict[str, str]:
    root = pick_workspace_root()
    out = (root / output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    written = {}
    for name, content in files.items():
        p = out / name
        p.write_text(content, encoding="utf-8")
        written[name] = str(p)
    return written


@tool
def cloudrun_config_generator_tool(input_json: Dict[str, Any]) -> Dict[str, Any]:
    """Generate Cloud Run config templates (service.yaml, cloudrun_deploy.sh, README) without secrets."""
    files = generate_cloudrun_templates(input_json)

    output_dir = (input_json.get("output_dir") or "deploy").strip().strip("/")
    write_files = bool(input_json.get("write_files", True))

    written = {}
    if write_files:
        written = write_templates(files, output_dir=output_dir)

    return {
        "files": files,
        "written_paths": written,
        "notes": (
            f"Day 6 generated Cloud Run config templates (no secrets). "
            f"Saved to ./{output_dir} (write_files={write_files}). "
            "Use DRY_RUN=true for validation. Day 10 will build+push IMAGE then deploy."
        ),
    }
