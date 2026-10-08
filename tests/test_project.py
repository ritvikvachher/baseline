import os
import stat
import tempfile
import unittest
from pathlib import Path

from coreline.project import PipelineError, Project

CONFIG = {
    "name": "TEST",
    "asset_types": ["char", "prop", "set"],
    "tasks": {"asset": ["model", "rig"], "shot": ["anim", "lighting"]},
}


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = Project(self.root, CONFIG)
        self.project.ensure_structure()

    def tearDown(self):
        # Publishes are read-only; make them writable so cleanup can delete them.
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                os.chmod(os.path.join(folder, name), stat.S_IWRITE | stat.S_IREAD)
        self._tmp.cleanup()

    def _scene(self, name="scene.hip", text="scene"):
        path = self.root / name
        path.write_text(text)
        return path

    def test_create_and_list_entities(self):
        self.project.create_asset("char", "hero")
        self.project.create_shot("sq010", "sh010", 1001, 1050)
        self.assertEqual([e.label for e in self.project.list_entities("asset")], ["char/hero"])
        self.assertEqual([e.label for e in self.project.list_entities("shot")], ["sq010/sh010"])
        self.assertTrue((self.root / "assets/char/hero/rig/work").is_dir())
        shot = self.project.list_entities("shot")[0]
        self.assertEqual(self.project.entity_info(shot)["frame_end"], 1050)

    def test_rejects_bad_names_and_duplicates(self):
        with self.assertRaises(PipelineError):
            self.project.create_asset("char", "Hero Guy")
        with self.assertRaises(PipelineError):
            self.project.create_asset("vehicle", "car")
        with self.assertRaises(PipelineError):
            self.project.create_shot("sq010", "sh010", 1100, 1001)
        self.project.create_asset("char", "hero")
        with self.assertRaises(PipelineError):
            self.project.create_asset("char", "hero")

    def test_workfile_versions_are_shared_across_users(self):
        hero = self.project.create_asset("char", "hero")
        v1 = self.project.add_existing_file(self._scene(), hero, "model", "ritvik")
        self.assertEqual(v1.path.name, "hero_model_v001_ritvik.hip")
        v2 = self.project.version_up(v1, hero, "model", "sam")
        self.assertEqual((v2.version, v2.user), (2, "sam"))
        # Autosaves and stray files are ignored.
        (v1.path.parent / "hero_model_v001_ritvik_autosave.hip").write_text("x")
        (v1.path.parent / "notes.txt").write_text("x")
        self.assertEqual([w.version for w in self.project.list_workfiles(hero, "model")], [1, 2])
        self.assertEqual(self.project.next_version(hero, "model"), 3)

    def test_publish_creates_read_only_numbered_copies(self):
        hero = self.project.create_asset("char", "hero")
        wf = self.project.add_existing_file(self._scene(text="v1"), hero, "model", "ritvik")
        pub1 = self.project.publish(wf, hero, "model", "ritvik", "first pass")
        pub2 = self.project.publish(wf, hero, "model", "ritvik", "")
        self.assertEqual([p.version for p in self.project.list_publishes(hero, "model")], [1, 2])
        published = pub1.path / "hero_model_v001.hip"
        self.assertEqual(published.read_text(), "v1")
        self.assertFalse(os.access(published, os.W_OK))
        self.assertEqual(pub1.info["comment"], "first pass")
        self.assertEqual(pub1.info["source"], "assets/char/hero/model/work/hero_model_v001_ritvik.hip")
        self.assertEqual(pub2.path.name, "v002")

    def test_create_project_and_reload_from_marker(self):
        root = self.root / "film"
        Project.create(root, CONFIG, name="My Film")
        loaded = Project(root)
        self.assertEqual(loaded.name, "My Film")
        self.assertEqual(loaded.tasks_for("shot"), ["anim", "lighting"])
        with self.assertRaises(PipelineError):
            Project(self.root / "not_a_project")

    def test_context_for_path(self):
        shot = self.project.create_shot("sq010", "sh010", 1001, 1010)
        wf = self.project.add_existing_file(self._scene(), shot, "lighting", "ritvik")
        entity, task = self.project.context_for_path(wf.path)
        self.assertEqual((entity.label, task), ("sq010/sh010", "lighting"))
        self.assertEqual(self.project.context_for_path(self.root / "elsewhere.hip"), (None, None))
        self.assertEqual(self.project.context_for_path(Path("C:/other/file.hip")), (None, None))

    def test_context_env_without_task(self):
        env = self.project.context_env(None, None, "ritvik")
        self.assertNotIn("CORELINE_TASK", env)
        self.assertEqual(env["CORELINE_USER"], "ritvik")

    def test_context_env_for_shot(self):
        shot = self.project.create_shot("sq010", "sh020", 1001, 1024)
        env = self.project.context_env(shot, "anim", "ritvik")
        self.assertEqual(env["CORELINE_FRAME_START"], "1001")
        self.assertEqual(env["CORELINE_TASK"], "anim")
        self.assertEqual(env["JOB"], self.root.as_posix())


if __name__ == "__main__":
    unittest.main()
