import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from coreline import checks, publish_usd, turntable
from coreline.project import PipelineError, Project

TEMPLATE = json.loads((Path(__file__).resolve().parents[1] / "config" / "project_template.json").read_text())


class PartTests(unittest.TestCase):
    """A step split into parts (geo: skeleton, muscle, skin), each versioned and published on its own."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "project"
        for task in ("geo", "lookdev"):
            for sub in ("wip", "published"):
                (self.root / "assets/characters/cheetah" / task / sub).mkdir(parents=True)
        (self.root / "_ADMIN").mkdir()
        self.project = Project.create(self.root, TEMPLATE, name="CORE")
        self.cheetah = next(e for e in self.project.list_entities("asset") if e.name == "cheetah")
        self.wip = self.project.work_dir(self.cheetah, "geo")

    def tearDown(self):
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                os.chmod(os.path.join(folder, name), stat.S_IWRITE | stat.S_IREAD)
        self._tmp.cleanup()

    def _file(self, name):
        path = self.wip / name
        path.write_text('//Maya ASCII 2025 scene\nrequires maya "2025";\n')
        return path

    def test_files_carry_their_part_and_version_per_part(self):
        self._file("cheetah_geo_v003_ritvik.ma")
        self._file("cheetah_geo_muscle_v001_liangyi.ma")
        self._file("cheetah_geo_muscle_v002_liangyi.ma")
        self._file("BUFMP_Core_Cheetah_Skeleton_Reto.mb")
        everything = self.project.list_workfiles(self.cheetah, "geo")
        self.assertEqual(len(everything), 4)
        muscle = self.project.list_workfiles(self.cheetah, "geo", "muscle")
        self.assertEqual([(w.version, w.user, w.part) for w in muscle], [(1, "liangyi", "muscle"), (2, "liangyi", "muscle")])
        self.assertEqual(len(self.project.list_workfiles(self.cheetah, "geo", "")), 2)  # not in a part
        self.assertEqual(self.project.next_version(self.cheetah, "geo", "muscle"), 3)
        self.assertEqual(self.project.next_version(self.cheetah, "geo", "skin"), 1)
        self.assertEqual(self.project.next_version(self.cheetah, "geo"), 4)  # the step as before
        self.assertEqual(self.project.new_workfile_path(self.cheetah, "geo", "ma", "sam", "skin").name,
                         "cheetah_geo_skin_v001_sam.ma")
        new = self.project.version_up(muscle[-1], self.cheetah, "geo", "sam")
        self.assertEqual((new.path.name, new.part), ("cheetah_geo_muscle_v003_sam.ma", "muscle"))
        brought = self.project.add_existing_file(self.wip / "BUFMP_Core_Cheetah_Skeleton_Reto.mb", self.cheetah,
                                                 "geo", "ritvik", "skeleton")
        self.assertEqual(brought.path.name, "cheetah_geo_skeleton_v001_ritvik.mb")

    def test_adding_parts_and_their_status(self):
        self.assertEqual(self.project.list_parts(self.cheetah, "geo"), [])
        for name in ("skeleton", "Muscle", "skin"):
            self.project.add_part(self.cheetah, "geo", name, "ritvik")
        self.assertEqual(self.project.list_parts(self.cheetah, "geo"), ["skeleton", "muscle", "skin"])
        for bad in ("muscle", "fat_layer", "2muscle", "v002", ""):
            with self.assertRaises(PipelineError):
                self.project.add_part(self.cheetah, "geo", bad, "ritvik")
        self.project.update_part(self.cheetah, "geo", "muscle", "ritvik", status="In progress", assignee="liangyi")
        info = self.project.part_info(self.cheetah, "geo", "muscle")
        self.assertEqual((info["status"], info["assignee"]), ("In progress", "liangyi"))
        self.assertEqual(self.project.task_info(self.cheetah, "geo")["status"], "Not started")  # the step's own
        self.assertEqual(self.project.list_parts(self.cheetah, "lookdev"), [])
        # A part also shows up from a file named for it.
        self._file("cheetah_geo_fascia_v001_sam.ma")
        self.assertIn("fascia", self.project.list_parts(self.cheetah, "geo"))

    def test_publishing_a_part(self):
        self._file("cheetah_geo_v001_ritvik.ma")
        whole_file = self.project.list_workfiles(self.cheetah, "geo")[0]
        part_file = self.project.add_existing_file(self._file("muscles_retopo.ma"), self.cheetah, "geo", "liangyi",
                                                   "muscle")
        pub = self.project.publish(part_file, self.cheetah, "geo", "liangyi", "First pass of the muscles")
        self.assertEqual(pub.path, self.project.publish_dir(self.cheetah, "geo") / "muscle" / "v001")
        self.assertEqual(pub.info["files"], ["cheetah_geo_muscle_v001.ma"])
        self.assertEqual(pub.info["part"], "muscle")
        again = self.project.publish(part_file, self.cheetah, "geo", "liangyi")
        self.assertEqual(again.version, 2)
        whole = self.project.publish(whole_file, self.cheetah, "geo", "ritvik")
        self.assertEqual(whole.version, 1)  # the step's own publishes count separately
        self.assertEqual([p.version for p in self.project.list_publishes(self.cheetah, "geo", "muscle")], [1, 2])
        self.assertEqual([p.version for p in self.project.list_publishes(self.cheetah, "geo")], [1])
        self.assertEqual(self.project.list_parts(self.cheetah, "geo"), ["muscle"])
        recent = self.project.all_publishes()
        self.assertEqual(len(recent), 3)
        # Turntables of a part publish are named for it.
        out = turntable.output_path(self.project, self.cheetah, "geo", Path(""), again)
        self.assertEqual(out, again.path / "turntable" / "cheetah_geo_muscle_v002_turntable.mp4")
        # Publish checks compare with the same part only.
        found = checks.run_checks(self.project, {}, {}, self.cheetah, "geo", part_file)
        latest = next(c for c in found if c.name == "Latest version")
        self.assertEqual(latest.state, checks.PASS)

    def test_usd_job_for_maya_geo_publishes(self):
        part_file = self.project.add_existing_file(self._file("muscles_retopo.ma"), self.cheetah, "geo", "liangyi",
                                                   "muscle")
        pub = self.project.publish(part_file, self.cheetah, "geo", "liangyi")
        scene = turntable.publish_source(pub)
        self.assertTrue(publish_usd.wants_usd("geo", scene))
        self.assertFalse(publish_usd.wants_usd("lookdev", scene))
        self.assertFalse(publish_usd.wants_usd("geo", scene.with_suffix(".hip")))
        mayapy = Path(self._tmp.name) / "mayapy.exe"
        mayapy.write_text("")
        maya = SimpleNamespace(version="2025", tool=lambda cfg, name: mayapy)
        app = SimpleNamespace(installation_for_file=lambda path: (maya, "2025"), app_config={"maya": {}},
                              user="liangyi")
        job = publish_usd.job_for(self.project, self.cheetah, "geo", pub, app)
        spec = json.loads(job.spec_file.read_text(encoding="utf-8"))
        self.assertEqual(Path(spec["out_usd"]), pub.path / "cheetah_geo_muscle_v001.usd")
        self.assertEqual(Path(spec["info_file"]),
                         self.project._publish_info_file(self.cheetah, "geo", pub.path))
        self.assertEqual(job.command()[1:3], ["-m", "coreline.publish_usd"])
        self.assertEqual(job.frames_label, "USD for Houdini · Waiting to start")
        no_maya = SimpleNamespace(installation_for_file=lambda path: (None, None), app_config={"maya": {}}, user="x")
        self.assertIsNone(publish_usd.job_for(self.project, self.cheetah, "geo", pub, no_maya))


if __name__ == "__main__":
    unittest.main()
