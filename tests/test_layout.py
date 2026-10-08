"""A project laid out like the team's OneDrive folder: wip/published, no sequences,
tracking data in _ADMIN/coreline, and departments that differ per asset."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from coreline import checks
from coreline.project import Project

TEMPLATE = json.loads((Path(__file__).resolve().parents[1] / "config" / "project_template.json").read_text())
APP_CONFIG = {"maya": {"label": "Maya", "extensions": [".ma", ".mb"]}}


class TeamLayoutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        folders = {
            "assets/characters/cheetah": ["geo", "groom", "lookdev", "rig", "textures"],
            "assets/environments/racetrack": ["geo", "lookdev", "textures"],
            "shots/SH010": ["anim", "cfx", "comp", "fx", "lighting"],
            "shots/SH020": ["anim", "lighting"],
        }
        for entity, tasks in folders.items():
            for task in tasks:
                for sub in ("wip", "published"):
                    (self.root / entity / task / sub).mkdir(parents=True)
        (self.root / "shots/SH010/render/beauty").mkdir(parents=True)
        (self.root / "_ADMIN").mkdir()
        self.wip = self.root / "assets/characters/cheetah/geo/wip"
        (self.wip / "BUFMP_Core_Cheetah_Skeleton_Reto.mb").write_text("maya scene")
        (self.wip / "desktop.ini").write_text("x")
        self.project = Project.create(self.root, TEMPLATE, name="CORE")

    def tearDown(self):
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                os.chmod(os.path.join(folder, name), stat.S_IWRITE | stat.S_IREAD)
        self._tmp.cleanup()

    def entity(self, kind, name):
        return next(e for e in self.project.list_entities(kind) if e.name == name)

    def files_outside_admin(self):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                      if p.is_file() and "_ADMIN" not in p.parts)

    def test_finds_existing_assets_and_shots(self):
        self.assertEqual([e.label for e in self.project.list_entities("asset")],
                         ["characters/cheetah", "environments/racetrack"])
        shots = self.project.list_entities("shot")
        self.assertEqual([(e.group, e.label, e.display_name) for e in shots],
                         [("", "SH010", "SH010"), ("", "SH020", "SH020")])
        self.assertEqual(self.project.asset_types, ["characters", "environments", "vehicles"])

    def test_tasks_come_from_each_folder(self):
        cheetah, racetrack = self.entity("asset", "cheetah"), self.entity("asset", "racetrack")
        self.assertEqual(self.project.entity_tasks(cheetah), ["geo", "rig", "textures", "lookdev", "groom"])
        self.assertEqual(self.project.entity_tasks(racetrack), ["geo", "textures", "lookdev"])
        # The render folder has no wip/published, so it isn't a task.
        self.assertEqual(self.project.entity_tasks(self.entity("shot", "SH010")),
                         ["anim", "cfx", "fx", "lighting", "comp"])
        (self.root / "assets/characters/cheetah/mocap/wip").mkdir(parents=True)
        self.assertEqual(self.project.all_tasks("asset")[-1], "mocap")

    def test_existing_files_are_listed_and_versioned_up_with_our_name(self):
        cheetah = self.entity("asset", "cheetah")
        [existing] = self.project.list_workfiles(cheetah, "geo")
        self.assertEqual((existing.path.name, existing.version, existing.user),
                         ("BUFMP_Core_Cheetah_Skeleton_Reto.mb", 0, ""))
        new = self.project.version_up(existing, cheetah, "geo", "ritvik")
        self.assertEqual(new.path, self.wip / "cheetah_geo_v001_ritvik.mb")
        (self.wip / "cheetah_geo_v07_final.ma").write_text("x")
        self.assertEqual([w.version for w in self.project.list_workfiles(cheetah, "geo")], [0, 1, 7])
        self.assertEqual(self.project.next_version(cheetah, "geo"), 8)

    def test_publish_and_status_keep_tracking_in_admin(self):
        cheetah = self.entity("asset", "cheetah")
        before = self.files_outside_admin()
        [existing] = self.project.list_workfiles(cheetah, "geo")
        pub = self.project.publish(existing, cheetah, "geo", "ritvik", "skeleton")
        self.project.update_task(cheetah, "geo", "ritvik", status="In review", assignee="ritvik")

        self.assertEqual(pub.path, self.root / "assets/characters/cheetah/geo/published/v001")
        self.assertEqual(self.files_outside_admin(),
                         sorted(before + ["assets/characters/cheetah/geo/published/v001/cheetah_geo_v001.mb"]))
        admin = self.root / "_ADMIN/coreline/assets/characters/cheetah/geo"
        self.assertTrue((admin / "task.json").is_file())
        self.assertTrue((admin / "publishes/v001.json").is_file())
        self.assertEqual(self.project.list_publishes(cheetah, "geo")[0].info["comment"], "skeleton")
        self.assertEqual(self.project.task_info(cheetah, "geo")["status"], "In review")
        self.assertEqual(self.project.current_step(cheetah), "geo")
        self.assertEqual(self.project.logs_dir, self.root / "_ADMIN/coreline/logs")

    def test_context_for_path_without_sequences(self):
        entity, task = self.project.context_for_path(self.root / "shots/SH010/lighting/wip/a.hip")
        self.assertEqual((entity.name, entity.group, task), ("SH010", "", "lighting"))
        entity, task = self.project.context_for_path(self.root / "shots/SH010/render/beauty/a.exr")
        self.assertEqual((entity.name, task), ("SH010", None))
        entity, task = self.project.context_for_path(self.wip / "BUFMP_Core_Cheetah_Skeleton_Reto.mb")
        self.assertEqual((entity.label, task), ("characters/cheetah", "geo"))
        self.assertEqual(self.project.context_for_path(self.root / "reference/x.png"), (None, None))

    def test_new_shot_gets_render_folders_and_frames(self):
        shot = self.project.create_shot("", "SH080", 1001, 1048, user="ritvik")
        self.assertEqual(shot.path, self.root / "shots/SH080")
        self.assertTrue((shot.path / "lighting/wip").is_dir())
        self.assertTrue((shot.path / "render/cryptomatte").is_dir())
        self.assertEqual(self.project.frame_range(shot), (1001, 1048))
        self.assertFalse((shot.path / "entity.json").exists())

    def test_frame_range_for_existing_shot(self):
        shot = self.entity("shot", "SH010")
        self.assertIsNone(self.project.frame_range(shot))
        self.assertEqual(self.project.context_env(shot, "anim", "r")["CORELINE_FRAME_START"], "")
        self.project.update_entity_info(shot, "ritvik", frame_start=1001, frame_end=1096)
        self.assertEqual(self.project.frame_range(shot), (1001, 1096))

    def test_render_output_goes_to_shot_render_folder(self):
        shot = self.entity("shot", "SH010")
        scene = shot.path / "lighting/wip/SH010_lighting_v003_ritvik.hip"
        self.assertEqual(self.project.render_output(shot, scene),
                         (shot.path / "render/beauty/SH010_lighting_v003_ritvik/"
                                      "SH010_lighting_v003_ritvik.$F4.exr").as_posix())
        self.assertIsNone(self.project.render_output(self.entity("asset", "cheetah"), scene))

    def test_naming_check_warns_for_hand_named_files(self):
        cheetah = self.entity("asset", "cheetah")
        [existing] = self.project.list_workfiles(cheetah, "geo")
        result = {c.name: c for c in checks.run_checks(self.project, APP_CONFIG, {}, cheetah, "geo", existing)}
        self.assertEqual(result["Naming convention"].state, checks.WARN)
        self.assertEqual(result["Latest version"].state, checks.PASS)


if __name__ == "__main__":
    unittest.main()
