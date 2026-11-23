# tests/test_install_skill.py — the `proofhouse install-skill` command
"""Installing the agent skill from the command line.

The skill ships inside the package so a user who installed with pip, and
has no clone, can still put it where their agent looks. What these tests
pin:

  * the packaged copy is byte-identical to the repository's own skill, so
    the two cannot drift apart unnoticed;
  * the default run writes under the current directory, --user under the
    home directory, --dest under the directory it is given;
  * an identical file is left alone, a different one is refused with
    nothing written, and --force replaces it.
"""

import os
import shutil
import tempfile
import unittest

from click.testing import CliRunner

from proofhouse.cli.main import _packaged_skill_path, cli


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_SKILL = os.path.join(REPO, ".claude", "skills", "proofhouse", "SKILL.md")
RELATIVE_TARGET = os.path.join("proofhouse", "SKILL.md")


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


class TestPackagedSkill(unittest.TestCase):

    def test_packaged_copy_matches_the_repository_skill(self):
        """Edit one copy without the other and this fails."""
        self.assertTrue(os.path.isfile(_packaged_skill_path()))
        self.assertEqual(
            _read(_packaged_skill_path()), _read(REPO_SKILL),
            "proofhouse/data/skill/SKILL.md and "
            ".claude/skills/proofhouse/SKILL.md differ. Copy the edited "
            "one over the other.")


class TestInstallSkill(unittest.TestCase):

    def setUp(self):
        # realpath: on macOS the temp directory sits behind a symlink, and
        # the command reports the path the working directory resolves to.
        self.work = os.path.realpath(tempfile.mkdtemp())
        self.home = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.old_cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, self.old_cwd)
        # HOME for POSIX, USERPROFILE for Windows: the real home directory
        # is never written to.
        self.env = {"HOME": self.home, "USERPROFILE": self.home}
        self.runner = CliRunner()

    def _run(self, *args):
        return self.runner.invoke(cli, ["install-skill"] + list(args),
                                  env=self.env)

    def _project_target(self):
        return os.path.join(self.work, ".claude", "skills", RELATIVE_TARGET)

    def _home_target(self):
        return os.path.join(self.home, ".claude", "skills", RELATIVE_TARGET)

    def test_default_writes_under_the_current_directory(self):
        result = self._run()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(_read(self._project_target()), _read(REPO_SKILL))
        self.assertIn(self._project_target(), result.output)
        self.assertFalse(os.path.exists(self._home_target()))

    def test_user_writes_under_the_home_directory(self):
        result = self._run("--user")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(_read(self._home_target()), _read(REPO_SKILL))
        self.assertFalse(os.path.exists(self._project_target()))

    def test_dest_writes_under_the_given_skills_directory(self):
        dest = os.path.join(self.work, "elsewhere", "skills")
        result = self._run("--dest", dest)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            _read(os.path.join(dest, RELATIVE_TARGET)), _read(REPO_SKILL))
        self.assertFalse(os.path.exists(self._project_target()))

    def test_user_and_dest_together_are_refused(self):
        result = self._run("--user", "--dest", os.path.join(self.work, "x"))
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(os.path.exists(self._home_target()))
        self.assertFalse(os.path.exists(os.path.join(self.work, "x")))

    def test_identical_file_is_left_alone(self):
        self.assertEqual(self._run().exit_code, 0)
        before = os.stat(self._project_target()).st_mtime_ns
        result = self._run()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Already up to date", result.output)
        self.assertEqual(os.stat(self._project_target()).st_mtime_ns, before)

    def test_different_file_is_refused_and_untouched(self):
        self.assertEqual(self._run().exit_code, 0)
        with open(self._project_target(), "wb") as fh:
            fh.write(b"my own notes\n")
        result = self._run()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("--force", result.output)
        self.assertEqual(_read(self._project_target()), b"my own notes\n")

    def test_force_replaces_a_different_file(self):
        self.assertEqual(self._run().exit_code, 0)
        with open(self._project_target(), "wb") as fh:
            fh.write(b"my own notes\n")
        result = self._run("--force")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Replaced", result.output)
        self.assertEqual(_read(self._project_target()), _read(REPO_SKILL))

    def test_missing_packaged_file_is_reported_not_crashed(self):
        import proofhouse.cli.main as main

        original = main._packaged_skill_path
        main._packaged_skill_path = lambda: os.path.join(self.work, "absent")
        self.addCleanup(setattr, main, "_packaged_skill_path", original)
        result = self._run()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("missing from this install", result.output)
        self.assertFalse(os.path.exists(self._project_target()))


if __name__ == "__main__":
    unittest.main()
