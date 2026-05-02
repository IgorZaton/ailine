"""Tests for `ailine doctor` checks."""

import os
import subprocess
import tempfile
import unittest

from ailine.cli.doctor import (
    DoctorContext,
    FAIL,
    PASS,
    WARN,
    _check_config,
    _check_dvc,
    _check_environment,
    _check_git,
    _check_mlflow,
    run_checks,
)
from ailine.config.validate import validate_config


def _make_repo(tmp: str) -> str:
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", "t@t.t"], check=True
    )
    subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("hi\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(
        ["git", "-C", repo, "commit", "-q", "-m", "init"], check=True
    )
    return repo


class DoctorChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _make_repo(self.tmp.name)
        self.cfg_path = os.path.join(self.repo, ".ailine.yml")
        with open(self.cfg_path, "w") as f:
            f.write("project:\n  version: 1\n  mode: track\n")
        self.cfg = validate_config(self.cfg_path)
        self.ctx = DoctorContext(cwd=self.repo, config_path=self.cfg_path, config=self.cfg)

    def test_config_pass(self):
        result = _check_config(self.ctx)
        self.assertEqual(result.status, PASS)

    def test_config_fail_when_missing(self):
        ctx = DoctorContext(
            cwd=self.repo,
            config_path=self.cfg_path,
            config=validate_config(os.path.join(self.tmp.name, "absent.yml")),
        )
        self.assertEqual(_check_config(ctx).status, FAIL)

    def test_git_pass_inside_repo(self):
        self.assertEqual(_check_git(self.ctx).status, PASS)

    def test_git_fail_outside_repo(self):
        outside = tempfile.mkdtemp(prefix="no-git-")
        self.addCleanup(lambda: os.rmdir(outside))
        ctx = DoctorContext(cwd=outside, config_path=self.cfg_path, config=self.cfg)
        self.assertEqual(_check_git(ctx).status, FAIL)

    def test_mlflow_pass_when_mode_none_and_no_set_env(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"] = {"mode": "none", "set_env": False}
        ctx = DoctorContext(cwd=self.repo, config_path=self.cfg_path, config=cfg)
        self.assertEqual(_check_mlflow(ctx).status, PASS)

    def test_dvc_pass_when_no_verify_commands(self):
        self.assertEqual(_check_dvc(self.ctx).status, PASS)

    def test_dvc_fail_when_verify_commands_set_but_dvc_missing(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["dvc"]["verify_commands"] = [["nonexistent-binary-xyz"]]
        ctx = DoctorContext(cwd=self.repo, config_path=self.cfg_path, config=cfg)
        # Force shutil.which("dvc") to return None by overriding PATH.
        original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ""
        try:
            self.assertEqual(_check_dvc(ctx).status, FAIL)
        finally:
            os.environ["PATH"] = original_path

    def test_environment_warn_or_pass(self):
        result = _check_environment(self.ctx)
        # On the dev box environment fingerprint completes; on a stripped CI
        # it can be partial. Either way it must not be FAIL.
        self.assertIn(result.status, {PASS, WARN})

    def test_run_checks_returns_one_per_default_check(self):
        results = run_checks(self.ctx)
        names = [r.name for r in results]
        self.assertEqual(
            names, ["config", "git", "mlflow", "dvc", "storage", "environment"]
        )


if __name__ == "__main__":
    unittest.main()
