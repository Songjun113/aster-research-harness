#!/usr/bin/env python3
"""Small stdlib-only runner for frozen, local research tasks.

Commands run with shell=False; this is process invocation control, not a security sandbox.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def relative_file(root, value):
    p = Path(value)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"file must stay within project root: {value}")
    root = Path(os.path.abspath(root))
    resolved = Path(os.path.abspath(root / p))
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise ValueError(f"not a project file: {value}")
    return p, resolved


def relative_output(value):
    if not isinstance(value, str):
        raise ValueError("output path must be a string")
    p = Path(value)
    if not value or p.is_absolute() or p.root or p.drive or p.as_posix() == "." or ".." in p.parts or "." in p.parts:
        raise ValueError(f"output must be a project-relative path: {value}")
    return p.as_posix()


def verify_task_key(frozen):
    recorded = frozen.get("task_key")
    body = {k: v for k, v in frozen.items() if k != "task_key"}
    actual = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not recorded or recorded != actual:
        raise ValueError("frozen task manifest hash mismatch")


def freeze(task_path, run_dir):
    task_path = Path(os.path.abspath(task_path))
    if not task_path.is_file():
        raise ValueError(f"task file does not exist: {task_path}")
    run_dir = Path(os.path.abspath(run_dir))
    if run_dir.exists() and any(run_dir.iterdir()):
        raise ValueError("run directory must be empty")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    root = Path(os.path.abspath(task_path.parent / task.get("project_root", ".")))
    if not root.is_dir():
        raise ValueError("project_root must name an existing directory")
    command = task["command"]
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ValueError("command must be a nonempty argv list of strings")
    if any(x in ("sh", "bash", "cmd", "powershell", "pwsh") for x in command[:1]):
        raise ValueError("shell commands are not allowed")
    if not isinstance(task.get("timeout_seconds"), (int, float)) or task["timeout_seconds"] <= 0:
        raise ValueError("timeout_seconds must be positive")
    raw_outputs = task.get("expected_outputs", [])
    if not isinstance(raw_outputs, list):
        raise ValueError("expected_outputs must be a list")
    expected_outputs = [relative_output(p) for p in raw_outputs]
    review_csv = relative_output(task["review_csv"]) if task.get("review_csv") else None
    files = {}
    for kind in ("source_files", "inputs"):
        files[kind] = {}
        for name in task.get(kind, []):
            rel, src = relative_file(root, name)
            target = run_dir / "project" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
            files[kind][rel.as_posix()] = digest(target)
    frozen = {
        "task_id": task["task_id"], "command": command,
        "timeout_seconds": task["timeout_seconds"],
        "expected_outputs": expected_outputs, "review_csv": review_csv, "files": files,
    }
    frozen["task_key"] = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(run_dir / "frozen_task.json", frozen)
    print(frozen["task_key"])


def run(run_dir):
    run_dir = Path(os.path.abspath(run_dir))
    if not run_dir.is_dir():
        raise ValueError("run directory does not exist")
    frozen = json.loads((run_dir / "frozen_task.json").read_text(encoding="utf-8"))
    state_path = run_dir / "state.json"
    verify_task_key(frozen)
    if state_path.exists():
        raise ValueError("run directory already has state; use a fresh directory to retry")
    for group in frozen["files"].values():
        for name, expected in group.items():
            if digest(run_dir / "project" / name) != expected:
                raise ValueError(f"frozen file hash mismatch: {name}")
    atomic_json(state_path, {"status": "running", "task_key": frozen["task_key"], "started_at": now()})
    argv = [sys.executable if part == "{python}" else part for part in frozen["command"]]
    timed_out = False
    try:
        result = subprocess.run(argv, cwd=run_dir / "project", capture_output=True,
                                timeout=frozen["timeout_seconds"], shell=False)
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", None
    except OSError as exc:
        stdout, stderr, code = b"", str(exc).encode("utf-8", errors="replace"), None
    atomic_bytes(run_dir / "stdout.log", stdout if isinstance(stdout, bytes) else stdout.encode())
    atomic_bytes(run_dir / "stderr.log", stderr if isinstance(stderr, bytes) else stderr.encode())
    missing = [p for p in frozen["expected_outputs"] if not (run_dir / "project" / p).is_file()]
    success = not timed_out and code == 0 and not missing
    evidence = {"task_key": frozen["task_key"], "finished_at": now(), "returncode": code,
                "timed_out": timed_out, "missing_outputs": missing, "success": success}
    atomic_json(run_dir / "exit_evidence.json", evidence)
    atomic_json(state_path, {"status": "completed" if success else "failed",
                             "task_key": frozen["task_key"], "finished_at": now(),
                             "execution_success": success})
    if not success:
        raise RuntimeError(f"task failed; see {run_dir / 'exit_evidence.json'}")
    print("completed (execution only)")


def review(run_dir):
    run_dir = Path(os.path.abspath(run_dir))
    if not run_dir.is_dir():
        raise ValueError("run directory does not exist")
    frozen = json.loads((run_dir / "frozen_task.json").read_text(encoding="utf-8"))
    verify_task_key(frozen)
    state_path = run_dir / "state.json"
    evidence_path = run_dir / "exit_evidence.json"
    if not state_path.is_file() or not evidence_path.is_file():
        raise ValueError("review requires a completed run")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if (state.get("status") != "completed" or not evidence.get("success")
            or state.get("task_key") != frozen["task_key"]
            or evidence.get("task_key") != frozen["task_key"]):
        raise ValueError("review requires successful execution evidence")
    if not frozen.get("review_csv"):
        raise ValueError("task has no review_csv")
    path = run_dir / "project" / frozen["review_csv"]
    rows, seen = [], set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["participant_id", "y_true", "probability"]:
            raise ValueError("CSV columns must be participant_id,y_true,probability")
        for row in reader:
            subject = (row["participant_id"] or "").strip()
            if not subject or subject in seen:
                raise ValueError("participant_id must be present and unique")
            seen.add(subject)
            try:
                y = int(row["y_true"])
                p = float(row["probability"])
            except (TypeError, ValueError):
                raise ValueError("y_true and probability must be numeric") from None
            if str(y) != row["y_true"] or y not in (0, 1) or not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError("invalid binary label or probability")
            rows.append((y, p))
    if not rows or {y for y, _ in rows} != {0, 1}:
        raise ValueError("review requires rows from both classes")
    tp = sum(y == 1 and p >= .5 for y, p in rows)
    tn = sum(y == 0 and p < .5 for y, p in rows)
    fp, fn = sum(y == 0 and p >= .5 for y, p in rows), sum(y == 1 and p < .5 for y, p in rows)
    pos, neg = tp + fn, tn + fp
    # Mann-Whitney rank AUC, with average ranks for ties.
    ordered = sorted((p, y) for y, p in rows)
    rank_sum, i = 0.0, 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j][0] == ordered[i][0]:
            j += 1
        avg_rank = ((i + 1) + j) / 2
        rank_sum += avg_rank * sum(y for _, y in ordered[i:j])
        i = j
    sensitivity, specificity = tp / pos, tn / neg
    result = {"task_key": frozen["task_key"], "reviewed_csv_sha256": digest(path),
              "n_participants": len(rows),
              "threshold": 0.5, "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
              "sensitivity": sensitivity, "specificity": specificity,
              "balanced_accuracy": (sensitivity + specificity) / 2,
              "auc": (rank_sum - pos * (pos + 1) / 2) / (pos * neg),
              "interpretation": "toy binary review only; not scientific validation"}
    atomic_json(run_dir / "review.json", result)
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("freeze"); p.add_argument("task_json"); p.add_argument("run_dir")
    p = sub.add_parser("run"); p.add_argument("run_dir")
    p = sub.add_parser("review"); p.add_argument("run_dir")
    args = parser.parse_args()
    try:
        {"freeze": lambda: freeze(args.task_json, args.run_dir),
         "run": lambda: run(args.run_dir), "review": lambda: review(args.run_dir)}[args.action]()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
