import json
import os
import glob
import time
from typing import Any, Dict, List, Optional
import requests

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000/generate")
CASES_DIR = os.environ.get("CASES_DIR", "tests/cases")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "tests/results")
REPO_ROOT = os.environ.get("REPO_ROOT", "").strip()


def replace_placeholders(obj):
    if isinstance(obj, dict):
        return {k: replace_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replace_placeholders(x) for x in obj]
    if isinstance(obj, str):
        if REPO_ROOT:
            obj = obj.replace("{{REPO_ROOT}}", REPO_ROOT)
        return obj
    return obj


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def as_text(resp_json: Any) -> str:
    if isinstance(resp_json, str):
        return resp_json
    return json.dumps(resp_json, ensure_ascii=False)


def check_assertions_text(text: str, must_contain: List[str], must_not_contain: List[str]) -> List[str]:
    errors = []
    for s in must_contain:
        if s not in text:
            errors.append(f"missing: {s}")
    for s in must_not_contain:
        if s in text:
            errors.append(f"forbidden present: {s}")
    return errors


def get_tool_used(resp: Any) -> List[Dict[str, Any]]:
    # resp is expected to be dict like {"output": "...", "tool_used": [...]}
    if not isinstance(resp, dict):
        return []
    tools = resp.get("tool_used")
    return tools if isinstance(tools, list) else []


def tool_names(resp: Any) -> List[str]:
    names: List[str] = []
    for t in get_tool_used(resp):
        if isinstance(t, dict) and isinstance(t.get("name"), str):
            names.append(t["name"])
    return names


def find_tool(resp: Any, name: str) -> Optional[Dict[str, Any]]:
    for t in get_tool_used(resp):
        if isinstance(t, dict) and t.get("name") == name:
            return t
    return None


def get_by_dotted_path(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def has_dotted_path(obj: Any, dotted: str) -> bool:
    return get_by_dotted_path(obj, dotted) is not None


def check_assertions_structured(resp: Any, assert_cfg: Dict[str, Any], status_code: int) -> List[str]:
    """
    Supported structured assertions:

    - status: int
    - tool_must_include: [tool_name, ...]
    - tool_must_not_include: [tool_name, ...]
    - tool_output_must_have: { tool_name: [key, ...] }  # keys at top-level in tool output dict
    - tool_output_path_must_exist: { tool_name: ["a.b.c", ...] }  # dotted path inside tool output dict
    """
    errors: List[str] = []

    # status
    expected_status = assert_cfg.get("status")
    if isinstance(expected_status, int) and status_code != expected_status:
        errors.append(f"status_expected={expected_status} got={status_code}")

    names = tool_names(resp)

    # tool presence
    must_tools = assert_cfg.get("tool_must_include", [])
    if isinstance(must_tools, list):
        for t in must_tools:
            if t not in names:
                errors.append(f"missing_tool: {t}")

    must_not_tools = assert_cfg.get("tool_must_not_include", [])
    if isinstance(must_not_tools, list):
        for t in must_not_tools:
            if t in names:
                errors.append(f"forbidden_tool_present: {t}")

    # tool output keys
    tool_output_must_have = assert_cfg.get("tool_output_must_have", {})
    if isinstance(tool_output_must_have, dict):
        for tool_name, keys in tool_output_must_have.items():
            tool = find_tool(resp, tool_name)
            if tool is None:
                errors.append(f"missing_tool_for_output_check: {tool_name}")
                continue
            out = tool.get("output")
            if not isinstance(out, dict):
                errors.append(f"tool_output_not_dict: {tool_name}")
                continue
            if isinstance(keys, list):
                for k in keys:
                    if k not in out:
                        errors.append(f"missing_tool_output_key: {tool_name}.{k}")

    # tool output dotted paths
    tool_output_path_must_exist = assert_cfg.get("tool_output_path_must_exist", {})
    if isinstance(tool_output_path_must_exist, dict):
        for tool_name, paths in tool_output_path_must_exist.items():
            tool = find_tool(resp, tool_name)
            if tool is None:
                errors.append(f"missing_tool_for_path_check: {tool_name}")
                continue
            out = tool.get("output")
            if not isinstance(out, dict):
                errors.append(f"tool_output_not_dict: {tool_name}")
                continue
            if isinstance(paths, list):
                for p in paths:
                    if not has_dotted_path(out, p):
                        errors.append(f"missing_tool_output_path: {tool_name}.{p}")

    return errors


def main() -> int:
    ensure_dir(RESULTS_DIR)

    case_files = sorted(glob.glob(os.path.join(CASES_DIR, "*.json")))
    if not case_files:
        print(f"No cases found in {CASES_DIR}")
        return 2

    total = 0
    failed = 0

    for fp in case_files:
        case = load_json(fp)
        case_id = case.get("id") or os.path.splitext(os.path.basename(fp))[0]
        req = replace_placeholders(case["request"])

        assert_cfg = case.get("assert", {}) if isinstance(case.get("assert", {}), dict) else {}

        # Keep backward compatibility with text-based assertions
        must_contain = assert_cfg.get("must_contain", []) if isinstance(assert_cfg.get("must_contain", []), list) else []
        must_not_contain = assert_cfg.get("must_not_contain", []) if isinstance(assert_cfg.get("must_not_contain", []), list) else []

        total += 1
        print(f"\n=== Running {case_id} ===")
        t0 = time.time()

        try:
            r = requests.post(API_URL, json=req, timeout=120)
            status = r.status_code
            try:
                body = r.json()
            except Exception:
                body = {"raw": r.text}
        except Exception as e:
            failed += 1
            print(f"[FAIL] request error: {e}")
            continue

        out_path = os.path.join(RESULTS_DIR, f"{case_id}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "case_id": case_id,
                    "status": status,
                    "elapsed_sec": round(time.time() - t0, 3),
                    "request": req,
                    "response": body,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        errs: List[str] = []

        # Default behavior: if user didn't specify status, require 200
        if "status" not in assert_cfg and status != 200:
            errs.append(f"http_status={status}")

        # text-based assertions (old)
        text = as_text(body)
        errs += check_assertions_text(text, must_contain, must_not_contain)

        # structured assertions (new)
        errs += check_assertions_structured(body, assert_cfg, status)

        if errs:
            failed += 1
            print(f"[FAIL] {case_id}: " + "; ".join(errs))
        else:
            print(f"[PASS] {case_id} ({round(time.time()-t0, 2)}s) -> {out_path}")

    print(f"\nDone. total={total}, failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
