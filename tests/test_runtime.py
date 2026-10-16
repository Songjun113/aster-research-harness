import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "aster_harness.py"
EXAMPLES = ROOT / "examples"


class HarnessTests(unittest.TestCase):
    def cli(self, *args, expect=0):
        p = subprocess.run([sys.executable, "-B", str(CLI), *map(str, args)], capture_output=True, text=True)
        self.assertEqual(p.returncode, expect, p.stderr)
        return p

    def freeze(self, temp, command=None, timeout=None, expected_outputs=None):
        task = json.loads((EXAMPLES / "task.json").read_text())
        if command is not None:
            task["command"] = command
        if timeout is not None:
            task["timeout_seconds"] = timeout
        if expected_outputs is not None:
            task["expected_outputs"] = expected_outputs
        path = temp / "task.json"
        task["project_root"] = str(ROOT)
        path.write_text(json.dumps(task), encoding="utf-8")
        run_dir = temp / "run"
        self.cli("freeze", path, run_dir)
        return run_dir

    def test_freeze_run_review_and_completed_rerun_guard(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            run_dir = self.freeze(Path(d))
            self.cli("run", run_dir)
            state = json.loads((run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "completed")
            self.assertTrue(json.loads((run_dir / "exit_evidence.json").read_text())["success"])
            self.cli("review", run_dir)
            metrics = json.loads((run_dir / "review.json").read_text())
            self.assertEqual(metrics["auc"], 1.0)
            self.cli("run", run_dir, expect=2)

    def test_frozen_file_tampering_fails_before_command(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            run_dir = self.freeze(Path(d))
            source = run_dir / "project" / "examples" / "synthetic_predictions.csv"
            source.write_text("tampered", encoding="utf-8")
            self.cli("run", run_dir, expect=2)
            self.assertFalse((run_dir / "state.json").exists())

    def test_failed_command_keeps_exit_evidence(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            run_dir = self.freeze(Path(d), ["{python}", "-c", "raise SystemExit(7)"])
            self.cli("run", run_dir, expect=2)
            evidence = json.loads((run_dir / "exit_evidence.json").read_text())
            self.assertEqual(evidence["returncode"], 7)
            self.assertFalse(evidence["success"])

    def test_launch_error_writes_failure_evidence(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            run_dir = self.freeze(Path(d), ["aster_missing_executable_for_test"])
            self.cli("run", run_dir, expect=2)
            evidence = json.loads((run_dir / "exit_evidence.json").read_text())
            self.assertIsNone(evidence["returncode"])
            self.assertFalse(evidence["success"])
            self.assertTrue((run_dir / "stderr.log").read_bytes())

    def test_timeout_keeps_failure_evidence_and_blocks_retry(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            run_dir = self.freeze(Path(d), ["{python}", "-c", "import time; time.sleep(2)"], timeout=.1)
            self.cli("run", run_dir, expect=2)
            receipt = run_dir / "exit_evidence.json"
            original = receipt.read_bytes()
            self.assertTrue(json.loads(original)["timed_out"])
            self.cli("run", run_dir, expect=2)
            self.assertEqual(receipt.read_bytes(), original)

    def test_review_detects_manifest_tampering(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            run_dir = self.freeze(Path(d))
            self.cli("run", run_dir)
            frozen_path = run_dir / "frozen_task.json"
            frozen = json.loads(frozen_path.read_text())
            frozen["review_csv"] = "other.csv"
            frozen_path.write_text(json.dumps(frozen), encoding="utf-8")
            self.cli("review", run_dir, expect=2)

    def test_run_detects_manifest_tampering(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            run_dir = self.freeze(Path(d))
            frozen_path = run_dir / "frozen_task.json"
            frozen = json.loads(frozen_path.read_text())
            frozen["timeout_seconds"] += 1
            frozen_path.write_text(json.dumps(frozen), encoding="utf-8")
            self.cli("run", run_dir, expect=2)
            self.assertFalse((run_dir / "state.json").exists())

    def test_absolute_and_traversal_outputs_are_rejected(self):
        for output in ("/tmp/out.csv", "../out.csv"):
            with self.subTest(output=output), tempfile.TemporaryDirectory(dir=ROOT) as d:
                run_dir = Path(d) / "run"
                task = json.loads((EXAMPLES / "task.json").read_text())
                task["project_root"] = str(ROOT)
                task["expected_outputs"] = [output]
                path = Path(d) / "task.json"
                path.write_text(json.dumps(task), encoding="utf-8")
                self.cli("freeze", path, run_dir, expect=2)
                self.assertFalse(run_dir.exists())

    def test_duplicate_participant_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            run_dir = self.freeze(Path(d))
            self.cli("run", run_dir)
            path = run_dir / "project" / "predictions.csv"
            path.write_text("participant_id,y_true,probability\na,0,0.1\na,1,0.9\nb,0,0.2\n", encoding="utf-8")
            self.cli("review", run_dir, expect=2)


if __name__ == "__main__":
    unittest.main()
