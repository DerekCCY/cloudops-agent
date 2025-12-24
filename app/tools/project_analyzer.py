from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from langchain_core.tools import tool


MAX_TEXT_BYTES = 300_000


def _read_text_if_exists(path: Path, max_bytes: int = MAX_TEXT_BYTES) -> str:
    if not path.exists() or not path.is_file():
        return ""
    data = path.read_bytes()
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="ignore")


def _safe_resolve_repo(repo_path: str, workspace_root: Optional[str]) -> Path:
    repo = Path(repo_path).expanduser().resolve()

    if workspace_root:
        root = Path(workspace_root).expanduser().resolve()
        try:
            repo.relative_to(root)
        except ValueError as e:
            raise ValueError(
                f"repo_path must be inside workspace_root. repo_path={repo} workspace_root={root}"
            ) from e

    if not repo.exists() or not repo.is_dir():
        raise ValueError(f"repo_path does not exist or is not a directory: {repo}")

    return repo


def _guess_port_from_text(text: str) -> Optional[int]:
    """
    Try to infer port from typical patterns.
    """
    # uvicorn --port 8000
    m = re.search(r"--port\s+(\d{2,5})", text)
    if m:
        return int(m.group(1))

    # os.environ.get("PORT", "8000") or PORT = int(os.getenv("PORT", 8000))
    m = re.search(r'PORT"\s*,\s*"?(\d{2,5})"?', text)
    if m:
        return int(m.group(1))

    # app.run(port=xxxx) or listen(xxxx)
    m = re.search(r"\bport\s*=\s*(\d{2,5})\b", text)
    if m:
        return int(m.group(1))

    return None


def _detect_python_deps(repo: Path) -> Tuple[Optional[str], Optional[str], str]:
    """
    Return (dependencies_file, packaging, combined_text_for_heuristics)
    packaging: "requirements" | "pyproject" | None
    """
    req = repo / "requirements.txt"
    pyproject = repo / "pyproject.toml"

    combined = ""
    if req.exists():
        combined += _read_text_if_exists(req)
        return "requirements.txt", "requirements", combined

    if pyproject.exists():
        combined += _read_text_if_exists(pyproject)
        return "pyproject.toml", "pyproject", combined

    return None, None, combined


def _find_fastapi_entrypoint(repo: Path) -> Tuple[Optional[str], bool]:
    """
    Look for FastAPI app creation. MVP: scan a few common files.
    Returns (entrypoint_relative_path, fastapi_marker_found)
    """
    candidates = [
        repo / "app" / "main.py",
        repo / "main.py",
        repo / "src" / "main.py",
        repo / "app.py",
    ]

    for p in candidates:
        if not p.exists():
            continue
        txt = _read_text_if_exists(p).lower()
        # very common patterns:
        # app = FastAPI(...)
        # application = FastAPI(...)
        if "fastapi(" in txt and ("= fastapi(" in txt or "fastapi(" in txt):
            return str(p.relative_to(repo)), True

    # fallback: just pick first existing candidate as entrypoint
    for p in candidates:
        if p.exists():
            return str(p.relative_to(repo)), False

    return None, False


def _infer_uvicorn_app_target(entrypoint: Optional[str]) -> str:
    """
    Convert file path to uvicorn module target.
    app/main.py -> app.main:app
    main.py -> main:app
    """
    if not entrypoint:
        return "app.main:app"
    ep = entrypoint.replace("\\", "/")
    if ep.endswith(".py"):
        ep = ep[:-3]
    ep = ep.replace("/", ".")
    # assume variable name is `app` (FastAPI standard)
    return f"{ep}:app"


def analyze_project(repo_path: str, workspace_root: Optional[str]) -> Dict[str, Any]:
    repo = _safe_resolve_repo(repo_path, workspace_root=workspace_root)

    has_dockerfile = (repo / "Dockerfile").exists()
    has_compose = (repo / "docker-compose.yml").exists() or (repo / "compose.yml").exists()

    language: Optional[str] = None
    dependencies_file: Optional[str] = None
    framework: Optional[str] = None
    entrypoint: Optional[str] = None
    runtime_hints: Dict[str, Any] = {}
    signals: Dict[str, Any] = {}

    # ---- language & deps
    deps_file, packaging, deps_text = _detect_python_deps(repo)
    if deps_file:
        language = "python"
        dependencies_file = deps_file

    if language is None and (repo / "package.json").exists():
        language = "node"
        dependencies_file = "package.json"

    # ---- python detection
    if language == "python":
        entrypoint, fastapi_marker = _find_fastapi_entrypoint(repo)
        signals["fastapi_marker"] = fastapi_marker

        deps_lower = (deps_text or "").lower()
        is_fastapi = ("fastapi" in deps_lower) or fastapi_marker
        is_uvicorn = ("uvicorn" in deps_lower)

        if is_fastapi:
            framework = "fastapi"

            # infer port: scan entrypoint file if present
            port = 8000
            if entrypoint:
                txt = _read_text_if_exists(repo / entrypoint)
                inferred = _guess_port_from_text(txt)
                if inferred:
                    port = inferred

            target = _infer_uvicorn_app_target(entrypoint)
            # If uvicorn not in deps, still give command (later Day5 will ensure install)
            start_command = f"uvicorn {target} --host 0.0.0.0 --port {port}"

            runtime_hints = {"port": port, "start_command": start_command}

        else:
            # generic python
            framework = None
            runtime_hints = {}

    # ---- node (MVP)
    if language == "node":
        pkg = _read_text_if_exists(repo / "package.json").lower()
        if "next" in pkg:
            framework = "nextjs"
            runtime_hints = {"port": 3000, "start_command": "npm run start"}
        elif "express" in pkg:
            framework = "express"
            runtime_hints = {"port": 3000, "start_command": "npm run start"}
        else:
            framework = None
            runtime_hints = {"start_command": "npm run start"}

    return {
        "repo_path": str(repo),
        "language": language,
        "framework": framework,
        "entrypoint": entrypoint,
        "dependencies_file": dependencies_file,
        "has_dockerfile": has_dockerfile,
        "has_compose": has_compose,
        "signals": signals,
        "runtime_hints": runtime_hints,
    }


@tool
def project_analyzer(repo_path: str) -> Dict[str, Any]:
    """
    Analyze a repository folder and return structured info useful for containerization & deployment.

    Security:
      If WORKSPACE_ROOT is set, repo_path must be inside it.
      In containers, prefer /workspace (if exists) or /app as workspace root.
    """
    ws = os.getenv("WORKSPACE_ROOT")

    # Auto-detect container-friendly workspace if env is missing or invalid
    if ws:
        ws_path = Path(ws).expanduser()
        if not ws_path.exists():
            ws = None

    if not ws:
        if Path("/workspace").exists():
            ws = "/workspace"
        elif Path("/app").exists():
            ws = "/app"
        else:
            ws = str(Path.cwd())

    return analyze_project(repo_path, workspace_root=ws)

