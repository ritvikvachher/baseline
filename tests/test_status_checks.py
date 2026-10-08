import os
import stat
import tempfile
import unittest
from pathlib import Path

from coreline import checks
from coreline.project import PipelineError, Project

CONFIG = {
    "name": "TEST",
    "asset_types": ["char", "prop"],
    "tasks": {"asset": ["model", "rig"], "shot": ["anim", "lighting"]},
}
APP_CONFIG = {"houdini": {"label": "Houdini", "extensions": [".hip"],
                          "file_version_regex": r"_HIP_SAVEVERSION = '([\d.]+)'"}}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = Project(self.root, CONFIG)
        self.project.ensure_structure()
        self.hero = self.project.create_asset("char", "hero")

    def tearDown(self):
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                os.chmod(os.path.join(folder, name), stat.S_IWRITE | stat.S_IREAD)
        self._tmp.cleanup()

    def scene(self, text="set -g _HIP_SAVEVERSION = '21.0.729'\n"):
        path = self.root / "scene.hip"
        path.write_text(text)
        return path


class TaskStatusTests(Base):
    def test_defaults_and_updates(self):
        self.assertEqual(self.project.task_info(self.hero, "rig")["status"], "Not started")
        info = self.project.update_task(self.hero, "rig", "ritvik", status="In progress", assignee="sam")
        self.assertEqual((info["status"], info["assignee"], info["updated_by"]), ("In progress", "sam", "ritvik"))
        self.assertEqual(self.project.task_info(self.hero, "rig")["assignee"], "sam")
        with self.assertRaises(PipelineError):
            self.project.update_task(self.hero, "rig", "ritvik", status="Nearly done")

    def test_current_step_and_progress(self):
        self.assertEqual(self.project.current_step(self.hero), "model")
        self.project.update_task(self.hero, "model", "r", status="Approved")
        self.project.update_task(self.hero, "rig", "r", status="In review")
        self.assertEqual(self.project.current_step(self.hero), "rig")
        film, per_task = self.project.progress()
        self.assertAlmostEqual(per_task["model"], 1.0)
        self.assertAlmostEqual(per_task["rig"], 0.8)
        self.assertAlmostEqual(film, 0.9)

    def test_all_publishes_newest_first(self):
        wf = self.project.add_existing_file(self.scene(), self.hero, "model", "ritvik")
        self.project.publish(wf, self.hero, "model", "ritvik", "one")
        prop = self.project.create_asset("prop", "tyre")
        wf2 = self.project.add_existing_file(self.scene(), prop, "rig", "sam")
        pub = self.project.publish(wf2, prop, "rig", "sam", "two")
        pub.path.joinpath("publish.json").write_text(
            pub.path.joinpath("publish.json").read_text().replace(pub.info["time"], "2999-01-01T00:00:00"))
        latest = self.project.all_publishes(limit=5)
        self.assertEqual([(e.name, t) for e, t, _ in latest], [("tyre", "rig"), ("hero", "model")])

    def test_save_config(self):
        root = self.root / "film"
        Project.create(root, CONFIG).save_config(delivery_date="2027-05-14")
        self.assertEqual(Project(root).config["delivery_date"], "2027-05-14")


class PublishCheckTests(Base):
    def run_checks(self, workfile, installed=("21.0.729",)):
        from coreline.apps import Installation
        installs = {"houdini": [Installation("houdini", "Houdini", v, Path("h.exe"), Path(".")) for v in installed]}
        return {c.name: c for c in checks.run_checks(self.project, APP_CONFIG, installs, self.hero, "model", workfile)}

    def test_clean_file_passes(self):
        wf = self.project.add_existing_file(self.scene(), self.hero, "model", "ritvik")
        result = self.run_checks(wf)
        self.assertTrue(all(c.state == checks.PASS for c in result.values()), result)

    def test_warnings(self):
        wf1 = self.project.add_existing_file(self.scene(), self.hero, "model", "ritvik")
        self.project.add_existing_file(self.scene(), self.hero, "model", "sam")
        self.project.publish(wf1, self.hero, "model", "ritvik")
        result = self.run_checks(wf1, installed=("20.5.613",))
        self.assertEqual(result["Latest version"].state, checks.WARN)
        self.assertEqual(result["Changed since last publish"].state, checks.WARN)
        self.assertEqual(result["Software version"].state, checks.WARN)
        self.assertIn("warning", checks.summary(list(result.values())))

    def test_empty_and_missing_files_fail(self):
        wf = self.project.add_existing_file(self.scene(""), self.hero, "model", "ritvik")
        self.assertEqual(self.run_checks(wf)["Scene file saved"].state, checks.FAIL)
        wf.path.unlink()
        self.assertEqual(self.run_checks(wf)["Scene file saved"].state, checks.FAIL)


if __name__ == "__main__":
    unittest.main()
