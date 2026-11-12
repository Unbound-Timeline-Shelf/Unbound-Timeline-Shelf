"""The shared vocabulary file (CONTRACTS-v1 C7.4): generated from the host registry, and in
agreement with the file Atlas publishes."""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("vocab_check", ROOT / "tools" / "vocab_check.py")
vocab_check = importlib.util.module_from_spec(SPEC)
sys.modules["vocab_check"] = vocab_check
SPEC.loader.exec_module(vocab_check)

HOST_FILE = ROOT / "contracts" / "vocab-v1.json"
ATLAS_FILE = Path(__file__).parent / "fixtures" / "atlas" / "vocab-v1.json"


def run(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        status = vocab_check.main(list(argv))
    return status, out.getvalue()


class HostFileTests(unittest.TestCase):
    def test_committed_file_and_digest_match_the_registry(self):
        status, out = run("check", "--root", str(ROOT))
        self.assertEqual(status, 0, out)

    def test_committed_file_is_well_formed_and_its_digest_holds(self):
        for path in (HOST_FILE, ATLAS_FILE):
            status, out = run("verify", str(path))
            self.assertEqual(status, 0, out)

    def test_host_and_atlas_agree_on_every_shared_vocabulary(self):
        status, out = run("compare", str(HOST_FILE), str(ATLAS_FILE))
        self.assertEqual(status, 0, out)
        host = json.loads(HOST_FILE.read_text(encoding="utf-8"))
        self.assertIn("platinum", host["achievement_tier"])
        self.assertEqual(host["crossplay"], ["yes", "partial", "no"])
        # Atlas-only keys are not the host's to publish
        self.assertNotIn("feed_kinds", host)

    def test_generate_writes_the_file_and_digest_the_check_accepts(self):
        with tempfile.TemporaryDirectory() as scratch:
            status, out = run("generate", "--root", scratch)
            self.assertEqual(status, 0, out)
            written = Path(scratch) / "contracts" / "vocab-v1.json"
            self.assertEqual(written.read_bytes(), HOST_FILE.read_bytes())
            self.assertEqual(run("check", "--root", scratch)[0], 0)
            written.write_text(written.read_text(encoding="utf-8").replace('"gold",', ""), "utf-8")
            status, out = run("check", "--root", scratch)
            self.assertEqual(status, 1)
            self.assertIn("does not match", out)


class DisagreementTests(unittest.TestCase):
    def _write(self, folder, name, data):
        path = Path(folder) / name
        path.write_text(vocab_check.render(data), encoding="utf-8")
        return path

    def test_a_missing_value_is_reported_and_order_is_not(self):
        atlas = json.loads(ATLAS_FILE.read_text(encoding="utf-8"))
        older = {**atlas, "achievement_tier": ["gold", "silver", "bronze"]}
        reordered = {**atlas, "crossplay": list(reversed(atlas["crossplay"]))}
        with tempfile.TemporaryDirectory() as scratch:
            first = self._write(scratch, "atlas.json", atlas)
            second = self._write(scratch, "older.json", older)
            third = self._write(scratch, "reordered.json", reordered)
            status, out = run("compare", str(first), str(second))
            self.assertEqual(status, 1)
            self.assertIn("achievement_tier", out)
            self.assertIn("platinum", out)
            self.assertEqual(run("compare", str(first), str(third))[0], 0)

    def test_a_digest_that_does_not_match_is_reported(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = self._write(scratch, "vocab-v1.json", json.loads(HOST_FILE.read_text("utf-8")))
            Path(str(path) + ".sha256").write_text("0" * 64 + "  vocab-v1.json\n", encoding="utf-8")
            status, out = run("verify", str(path))
            self.assertEqual(status, 1)
            self.assertIn("hashes to", out)


if __name__ == "__main__":
    unittest.main()
