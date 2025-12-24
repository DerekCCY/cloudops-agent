import json
import os
import sys
import glob
import time
from typing import Any, Dict, List
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
    # normalize to string for assertion
    if isinstance(resp_json, str):
        return resp_json
    return json.dumps(resp_json, ensure_ascii=False)

def check_assertions(text: str, must_contain: List[str], must_not_contain: List[str]) -> List[str]:
    errors = []
    for s in must_contain:
        if s not in text:
            errors.append(f"missing: {s}")
    for s in must_not_contain:
        if s in text:
            errors.append(f"forbidden present: {s}")
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
        must_contain = case.get("assert", {}).get("must_contain", [])
        must_not_contain = case.get("assert", {}).get("must_not_contain", [])

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
                    "response": body,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        text = as_text(body)
        errs = []
        if status != 200:
            errs.append(f"http_status={status}")
        errs += check_assertions(text, must_contain, must_not_contain)

        if errs:
            failed += 1
            print(f"[FAIL] {case_id}: " + "; ".join(errs))
        else:
            print(f"[PASS] {case_id} ({round(time.time()-t0, 2)}s) -> {out_path}")

    print(f"\nDone. total={total}, failed={failed}")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
