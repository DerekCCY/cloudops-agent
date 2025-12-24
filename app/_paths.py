from __future__ import annotations
import os
from pathlib import Path

def pick_workspace_root() -> Path:
    """
    Return a safe root for reading/writing artifacts.
    Priority:
      1) WORKSPACE_ROOT env (if exists)
      2) /workspace (if exists)  # docker mount convention
      3) current repo root inferred from code location (/app in container)
    """
    ws = os.getenv("WORKSPACE_ROOT")
    if ws:
        p = Path(ws).expanduser().resolve()
        if p.exists():
            return p

    if Path("/workspace").exists():
        return Path("/workspace").resolve()

    # fallback: repo root based on this file location:
    # app/tools/_paths.py -> parents[2] => /app (container) or repo root (local)
    return Path(__file__).resolve().parents[2]
