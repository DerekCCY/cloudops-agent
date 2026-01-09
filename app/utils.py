from __future__ import annotations
import os
from pathlib import Path


'''Root Path Determination'''
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


'''Capabilities Gate'''
from dataclasses import dataclass
from app.runtime import RunEnv, get_run_env

@dataclass(frozen=True)
class Capabilities:
    can_read_repo: bool
    can_write_files: bool
    can_execute_commands: bool

def get_capabilities() -> Capabilities:
    env = get_run_env()

    if env == RunEnv.CLOUDRUN:
        return Capabilities(
            can_read_repo=False,
            can_write_files=False,
            can_execute_commands=False,
        )

    # LOCAL / Docker
    return Capabilities(
        can_read_repo=True,
        can_write_files=True,
        can_execute_commands=True,
    )


def require_capability(name: str):
    caps = get_capabilities()
    if not getattr(caps, name):
        raise PermissionError(
            f"Capability '{name}' is not allowed in this runtime environment."
        )

