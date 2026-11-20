import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = """\
import sys

assert sys.argv == [__file__], sys.argv  # gcphase must not leak its own flags

data = [{"i": i, "tags": ["a", "b"]} for i in range(5000)]
print("loaded", len(data))
"""


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.script = self.dir / "workload.py"
        self.script.write_text(SCRIPT)
        self.report = self.dir / "report.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "gcphase", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

    def test_run_writes_a_report_and_preserves_argv(self):
        result = self.run_cli("run", str(self.script), "--json", str(self.report))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("loaded 5000", result.stdout)
        report = json.loads(self.report.read_text())
        self.assertIn("script", report["phases"])
        self.assertGreater(report["phases"]["script"]["wall_s"], 0.0)

    def test_gate_pass_and_fail(self):
        self.run_cli("run", str(self.script), "--json", str(self.report), "--quiet")
        passing = self.run_cli("gate", str(self.report), "--max-share", "1.0")
        self.assertEqual(passing.returncode, 0, passing.stdout)
        self.assertIn("PASS", passing.stdout)
        failing = self.run_cli("gate", str(self.report), "--max-share", "0.0")
        self.assertEqual(failing.returncode, 1, failing.stdout)
        self.assertIn("FAIL", failing.stdout)

    def test_gate_reports_missing_phase(self):
        self.run_cli("run", str(self.script), "--json", str(self.report), "--quiet")
        result = self.run_cli("gate", str(self.report), "--phase", "nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL", result.stdout)

    def test_missing_script_is_an_error(self):
        result = self.run_cli("run", str(self.dir / "absent.py"))
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()