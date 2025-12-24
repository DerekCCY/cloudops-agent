from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any

from langchain_core.tools import tool


def _safe_repo_path(repo_path: str) -> Path:
    repo = Path(repo_path).expanduser().resolve() # Turn to clean absolute path
    workspace_root = os.getenv("WORKSPACE_ROOT")
    if workspace_root:
        root = Path(workspace_root).expanduser().resolve()
        repo.relative_to(root)  # raises ValueError if outside
    if not repo.exists() or not repo.is_dir():
        raise ValueError(f"Invalid repo_path: {repo}")
    return repo


def generate_dockerfile_from_analysis(analysis: Dict[str, Any]) -> str:
    """
    Create a Cloud Run friendly Dockerfile for Python FastAPI.
    Uses analysis.runtime_hints.port and analysis.dependencies_file.
    """
    language = analysis.get("language")
    framework = analysis.get("framework")
    deps = analysis.get("dependencies_file")
    port = (analysis.get("runtime_hints") or {}).get("port", 8000)
    start_cmd = (analysis.get("runtime_hints") or {}).get("start_command")

    if language != "python":
        raise ValueError(f"Only python is supported in Day5 MVP. language={language}")
    if framework != "fastapi":
        raise ValueError(f"Only fastapi is supported in Day5 MVP. framework={framework}")
    if deps not in ("requirements.txt", "pyproject.toml"):
        raise ValueError(f"Unsupported dependencies_file: {deps}")

    # Cloud Run expects listening on $PORT; we’ll use env PORT and bind host 0.0.0.0
    # We'll prefer the analyzer's command, but ensure it uses $PORT.
    if start_cmd and "--port" in start_cmd:
        # Replace explicit port number with $PORT
        # e.g. --port 8000 -> --port $PORT
        import re
        start_cmd = re.sub(r"--port\s+\d{2,5}", "--port $PORT", start_cmd)

    if not start_cmd:
        start_cmd = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"

    dockerfile = f"""\
# syntax=docker/dockerfile:1

FROM python:3.11-slim

# Faster, cleaner Python in containers
ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PORT={port}

WORKDIR /app

# System deps (kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ca-certificates \\
 && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching
COPY {deps} /app/{deps}
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r /app/{deps}

# Copy the rest of the source code
COPY . /app

# Cloud Run listens on $PORT
EXPOSE {port}

# Use shell form so $PORT env var is expanded
CMD ["sh", "-c", "{start_cmd}"]
"""
    return dockerfile


@tool
def dockerfile_generator(repo_path: str, analysis: Dict[str, Any], overwrite: bool = False) -> Dict[str, Any]:
    """
    Generate a Dockerfile from analyzer output and write it into the repo.

    Inputs:
      repo_path: repo directory (must be under WORKSPACE_ROOT if set)
      analysis: analyzer JSON dict
      overwrite: whether to overwrite existing Dockerfile

    Output:
      dict with dockerfile_path and dockerfile_preview
    """
    repo = _safe_repo_path(repo_path)
    dockerfile_path = repo / "Dockerfile"

    if dockerfile_path.exists() and not overwrite:
        return {
            "status": "skipped",
            "reason": "Dockerfile already exists (set overwrite=true to replace).",
            "dockerfile_path": str(dockerfile_path),
        }

    dockerfile = generate_dockerfile_from_analysis(analysis)
    dockerfile_path.write_text(dockerfile, encoding="utf-8")

    return {
        "status": "written",
        "dockerfile_path": str(dockerfile_path),
        "dockerfile_preview": dockerfile[:1200],
    }
