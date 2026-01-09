import os
from enum import Enum

class RunEnv(str, Enum):
    LOCAL = "local"
    CLOUDRUN = "cloudrun"

def get_run_env() -> RunEnv:
    if os.getenv("K_SERVICE"):
        return RunEnv.CLOUDRUN
    return RunEnv.LOCAL
