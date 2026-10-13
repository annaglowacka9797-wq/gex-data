import os, re, shutil, subprocess, sys, unittest
try:
    import yaml
except ImportError:          # pip install -r requirements-dev.txt
    yaml = None

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WF = os.path.join(ROOT, ".github", "workflows")


def load(name):
    with open(os.path.join(WF, name), encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    d["on"] = d.pop(True, d.get("on"))            # PyYAML reads the key `on` as boolean True
    return d


@unittest.skipIf(yaml is None, "PyYAML missing: pip install -r requirements-dev.txt")
class TestWorkflows(unittest.TestCase):
    def test_collect_schedule(self):
        w = load("collect.yml")
        crons = [s["cron"] for s in w["on"]["schedule"]]
        self.assertEqual(crons, ["45 22 * * 1-5", "15 23 * * 1-5"])
        for c in crons:
            self.assertEqual(len(c.split()), 5)
        self.assertIn("workflow_dispatch", w["on"])

    def test_permissions_are_minimal_and_sufficient(self):
        w = load("collect.yml")
        self.assertEqual(w["permissions"], {"contents": "write", "issues": "write"})
        self.assertEqual(load("tests.yml")["permissions"], {"contents": "read"})

    def test_no_untrusted_expression_inside_run_scripts(self):
        """Workflow inputs must reach the shell through env vars, never be pasted into the script (injection)."""
        w = load("collect.yml")
        for step in w["jobs"]["collect"]["steps"]:
            run = step.get("run", "")
            self.assertNotIn("inputs.", run, step.get("name"))
            self.assertNotIn("github.event", run, step.get("name"))

    def test_actions_are_pinned_to_major_versions(self):
        for name in ("collect.yml", "tests.yml"):
            text = open(os.path.join(WF, name), encoding="utf-8").read()
            for m in re.finditer(r"uses:\s*([\w./-]+)@(\S+)", text):
                self.assertRegex(m.group(2), r"^v\d+", f"{m.group(1)} must be pinned")

    def test_safety_settings(self):
        j = load("collect.yml")["jobs"]["collect"]
        self.assertLessEqual(j["timeout-minutes"], 30)
        self.assertEqual(load("collect.yml")["concurrency"]["cancel-in-progress"], False)
        collect = [s for s in j["steps"] if s.get("id") == "collect"][0]
        self.assertTrue(collect["continue-on-error"], "failure must not skip the commit of the symbols that worked")
        self.assertEqual(collect["shell"], "bash", "needed for pipefail around tee")
        names = [s.get("name") for s in j["steps"]]
        self.assertLess(names.index("Commit data"), names.index("Report failure"))

    @unittest.skipUnless(shutil.which("actionlint"), "actionlint not installed")
    def test_actionlint_clean(self):
        r = subprocess.run(["actionlint", os.path.join(WF, "collect.yml"), os.path.join(WF, "tests.yml")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_data_files_never_include_logs(self):
        with open(os.path.join(ROOT, ".gitignore")) as fh:
            self.assertIn("collect.log", fh.read())


if __name__ == "__main__":
    unittest.main()
