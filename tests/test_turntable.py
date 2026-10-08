import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coreline import turntable
from coreline.project import PipelineError, Project
from coreline.render import RenderJob, RenderQueue

TEMPLATE = json.loads((Path(__file__).resolve().parents[1] / "config" / "project_template.json").read_text())


class TurntableTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "project"
        for task in ("geo", "lookdev"):
            for sub in ("wip", "published"):
                (self.root / "assets/characters/cheetah" / task / sub).mkdir(parents=True)
        (self.root / "_ADMIN").mkdir()
        self.project = Project.create(self.root, TEMPLATE, name="CORE")
        self.cheetah = next(e for e in self.project.list_entities("asset") if e.name == "cheetah")
        wip = self.project.work_dir(self.cheetah, "geo")
        (wip / "cheetah_geo_v001_ritvik.ma").write_text('//Maya ASCII 2025 scene\nrequires maya "2025";\n')
        self.workfile = self.project.list_workfiles(self.cheetah, "geo")[-1]
        self.publish = self.project.publish(self.workfile, self.cheetah, "geo", "ritvik", "Retopo done.")
        # Pretend software: the job only checks these files exist.
        self.houdini_bin = self.base / "houdini" / "bin"
        self.houdini_bin.mkdir(parents=True)
        for tool in ("hython", "husk", "hffmpeg", "hoiiotool"):
            (self.houdini_bin / f"{tool}.exe").write_text("")
        self.mayapy = self.base / "maya" / "bin" / "mayapy.exe"
        self.mayapy.parent.mkdir(parents=True)
        self.mayapy.write_text("")
        self.hdri = self.root / "_ADMIN/pipeline/turntable/studio_small_08_4k.exr"
        self.hdri.parent.mkdir(parents=True)
        self.hdri.write_bytes(b"exr")
        self.template = self.hdri.parent / "turntable_template.hipnc"
        self.template.write_bytes(b"hip")
        patcher = mock.patch.object(turntable, "WORK_ROOT", self.base / "work")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for folder, _dirs, files in os.walk(self.base):
            for name in files:
                os.chmod(os.path.join(folder, name), stat.S_IWRITE | stat.S_IREAD)
        self._tmp.cleanup()

    def job(self, publish="default", **changes):
        args = dict(project=self.project, entity=self.cheetah, task="geo",
                    source=turntable.publish_source(self.publish),
                    publish=self.publish if publish == "default" else publish, user="sam",
                    mayapy=self.mayapy, maya_version="2025", houdini_bin=self.houdini_bin,
                    houdini_version="21.0.729")
        args.update(changes)
        return turntable.create_job(**args)

    def test_which_steps_get_a_turntable(self):
        self.assertEqual(turntable.kind_for("geo", Path("a.ma")), "geo")
        self.assertEqual(turntable.kind_for("Model", Path("a.MB")), "geo")
        self.assertIsNone(turntable.kind_for("geo", Path("a.hip")))
        self.assertIsNone(turntable.kind_for("rig", Path("a.ma")))
        self.assertEqual(turntable.kind_for("lookdev", Path("a.hipnc")), "lookdev")
        self.assertEqual(turntable.kind_for("Texturing", Path("a.HIP")), "lookdev")
        self.assertIsNone(turntable.kind_for("lookdev", Path("a.ma")))
        self.assertTrue(turntable.has_turntables("lookdev"))
        self.assertFalse(turntable.has_turntables("rig"))

    def test_settings_file_only_needs_changes(self):
        settings_file = self.base / "turntable.json"
        settings_file.write_text('{"frames": 48}')
        with mock.patch.object(turntable, "SETTINGS_FILE", settings_file):
            config = turntable.load_settings()
        self.assertEqual(config["frames"], 48)
        self.assertEqual(config["fps"], turntable.DEFAULTS["fps"])

    def test_output_next_to_publish_or_in_wip(self):
        source = turntable.publish_source(self.publish)
        self.assertEqual(source.name, "cheetah_geo_v001.ma")
        published = turntable.output_path(self.project, self.cheetah, "geo", source, self.publish)
        self.assertEqual(published, self.publish.path / "turntable" / "cheetah_geo_v001_turntable.mp4")
        work = turntable.output_path(self.project, self.cheetah, "geo", self.workfile.path)
        self.assertEqual(work, self.workfile.path.parent / "turntables" / "cheetah_geo_v001_ritvik_turntable.mp4")
        self.assertIsNone(turntable.find_turntable(self.project, self.cheetah, "geo", self.publish))
        published.parent.mkdir()
        published.write_bytes(b"mp4")
        self.assertEqual(turntable.find_turntable(self.project, self.cheetah, "geo", self.publish), published)

    def test_job_spec(self):
        job = self.job()
        spec = json.loads(job.spec_file.read_text(encoding="utf-8"))
        self.assertEqual(spec["kind"], "geo")
        self.assertEqual(spec["context"]["artist"], "ritvik")  # who published, not who clicked
        self.assertEqual(spec["context"]["made_by"], "sam")
        self.assertEqual(spec["context"]["notes"], "Retopo done.")
        self.assertEqual(spec["context"]["version_text"], "v001")
        self.assertEqual(Path(spec["template"]), self.template)
        self.assertEqual(job.command()[1:4], ["-m", "coreline.turntable", str(job.spec_file)])
        self.assertEqual(job.frames_label, "Turntable · Waiting to start")
        self.assertIn(str(turntable.PACKAGE_DIR.parent), job.env["PYTHONPATH"])

    def test_work_file_job(self):
        job = self.job(publish=None, source=self.workfile.path)
        spec = json.loads(job.spec_file.read_text(encoding="utf-8"))
        self.assertEqual(spec["context"]["version_text"], "work file cheetah_geo_v001_ritvik")
        self.assertEqual(spec["context"]["artist"], "sam")

    def test_missing_pieces_are_explained(self):
        with self.assertRaisesRegex(PipelineError, "Maya isn't installed"):
            self.job(mayapy=None)
        (self.houdini_bin / "husk.exe").unlink()
        with self.assertRaisesRegex(PipelineError, "husk"):
            self.job()
        (self.houdini_bin / "husk.exe").write_text("")
        self.template.unlink()
        with self.assertRaisesRegex(PipelineError, "template is missing"):
            self.job()
        self.hdri.unlink()
        with self.assertRaisesRegex(PipelineError, "HDRI is missing"):
            self.job(overrides={"template": ""})
        with self.assertRaisesRegex(PipelineError, "geo step"):
            self.job(task="rig")

    def lookdev_job(self, **changes):
        wip = self.project.work_dir(self.cheetah, "lookdev")
        hip = wip / "cheetah_skele_test.hipnc"
        hip.write_bytes(b"hip")
        args = dict(task="lookdev", source=hip, publish=None, mayapy=None, maya_version="")
        args.update(changes)
        return self.job(**args)

    def test_lookdev_job(self):
        job = self.lookdev_job()  # no Maya needed
        spec = json.loads(job.spec_file.read_text(encoding="utf-8"))
        self.assertEqual(spec["kind"], "lookdev")
        self.assertEqual(spec["context"]["version_text"], "work file cheetah_skele_test")
        self.assertEqual(job.end, 240)  # the asset's spin, then the HDRI's
        self.assertEqual(turntable.output_path(self.project, self.cheetah, "lookdev", job.hip).name,
                         "cheetah_skele_test_turntable.mp4")

    def test_hdri_spin_only_for_lookdev_in_the_template(self):
        config = turntable.load_settings()
        self.assertEqual(turntable.hdri_spin_frames("lookdev", config), config["frames"])
        self.assertEqual(turntable.hdri_spin_frames("geo", config), 0)
        self.assertEqual(turntable.hdri_spin_frames("lookdev", dict(config, template="")), 0)
        self.assertEqual(turntable.hdri_spin_frames("lookdev", dict(config, lookdev_hdri_spin=False)), 0)
        self.template.unlink()
        self.assertEqual(self.lookdev_job(overrides={"template": "", "frames": 48}).end, 48)

    def test_farm_spec_travels_between_pcs(self):
        job = self.lookdev_job()
        spec = json.loads(job.spec_file.read_text(encoding="utf-8"))
        portable = turntable.portable_spec(spec, self.root)
        self.assertEqual(portable["source"], "assets/characters/cheetah/lookdev/wip/cheetah_skele_test.hipnc")
        self.assertEqual(portable["template"], "_ADMIN/pipeline/turntable/turntable_template.hipnc")
        self.assertNotIn("tools", portable)
        self.assertTrue(portable["context"]["requested"])
        # Another PC, with the project somewhere else.
        other = self.base / "other_pc" / "project"
        import shutil
        shutil.copytree(self.root, other)
        project = Project(other)
        piece = turntable.farm_job(portable, project, "sam", houdini_bin=self.houdini_bin,
                                   houdini_version="21.0.729", mayapy=None, maya_version="",
                                   log_path=self.base / "piece.log", work_name="farm_x_31_60",
                                   frame_range=(31, 60), part_out=self.base / "parts" / "part_0031_0060.mp4")
        local = json.loads(piece.spec_file.read_text(encoding="utf-8"))
        self.assertEqual(Path(local["source"]), other / portable["source"])
        self.assertEqual(local["frame_range"], [31, 60])
        self.assertEqual((piece.start, piece.end), (31, 60))
        self.assertEqual(Path(local["tools"]["husk"]), self.houdini_bin / "husk.exe")
        finish = turntable.farm_job(portable, project, "sam", houdini_bin=self.houdini_bin,
                                    houdini_version="21.0.729", mayapy=None, maya_version="",
                                    log_path=self.base / "finish.log", work_name="farm_x_0_0",
                                    finish_parts=[self.base / "parts" / "part_0001_0030.mp4"])
        self.assertEqual(len(json.loads(finish.spec_file.read_text(encoding="utf-8"))["finish_parts"]), 1)
        geo = dict(portable, kind="geo")
        with self.assertRaisesRegex(PipelineError, "Maya"):
            turntable.farm_job(geo, project, "sam", houdini_bin=self.houdini_bin, houdini_version="21.0.729",
                               mayapy=None, maya_version="", log_path=self.base / "g.log", work_name="g")

    def test_lab_colour_config_or_houdinis_own(self):
        lab = "S:/no_such_drive/OCIO/BU_config.ocio"
        houdini_config = self.base / "houdini" / "packages" / "ocio" / "houdini-config-v2.ocio"
        houdini_config.parent.mkdir(parents=True)
        houdini_config.write_text("ocio_profile_version: 2")
        with mock.patch.dict(os.environ, {"OCIO": ""}):
            spec = json.loads(self.job(overrides={"ocio_config": lab}).spec_file.read_text(encoding="utf-8"))
            self.assertEqual(Path(spec["ocio_config"]), houdini_config)  # off the lab network
            self.assertIn("isn't reachable", spec["ocio_note"])
            reachable = self.base / "BU_config.ocio"
            reachable.write_text("ocio_profile_version: 2")
            spec = json.loads(self.job(overrides={"ocio_config": str(reachable)}).spec_file.read_text(encoding="utf-8"))
            self.assertEqual((Path(spec["ocio_config"]), spec["ocio_note"]), (reachable, ""))
            # On the farm, the team's config travels as it's set, and each PC checks it again.
            portable = turntable.portable_spec(
                json.loads(self.job(overrides={"ocio_config": lab}).spec_file.read_text(encoding="utf-8")), self.root)
            self.assertEqual(portable["ocio_config"], lab)
            self.assertNotIn("ocio_note", portable)
            piece = turntable.farm_job(portable, self.project, "sam", houdini_bin=self.houdini_bin,
                                       houdini_version="21.0.729", mayapy=self.mayapy, maya_version="2025",
                                       log_path=self.base / "p.log", work_name="ocio_check")
            local = json.loads(piece.spec_file.read_text(encoding="utf-8"))
            self.assertEqual(Path(local["ocio_config"]), houdini_config)
            self.assertIn("isn't reachable", local["ocio_note"])

    def test_plain_studio_without_template(self):
        self.template.unlink()
        spec = json.loads(self.job(overrides={"template": ""}).spec_file.read_text(encoding="utf-8"))
        self.assertEqual(spec["template"], "")
        self.assertEqual(Path(spec["hdri"]), self.hdri)

    def test_dialog_overrides(self):
        self.assertEqual(turntable.parse_overrides(" 96 ", "32"), {"frames": 96, "samples": 32})
        for frames, samples in (("abc", "32"), ("0", "32"), ("96", "0"), ("96", "1.5")):
            with self.assertRaises(PipelineError):
                turntable.parse_overrides(frames, samples)
        job = self.job(overrides={"frames": 48, "samples": 16})
        spec = json.loads(job.spec_file.read_text(encoding="utf-8"))
        self.assertEqual((spec["settings"]["frames"], spec["settings"]["samples"]), (48, 16))
        self.assertEqual(job.end, 48)

    def test_slate_text(self):
        self.assertEqual(turntable._size_text([0.229, 0.792, 1.421]),
                         "0.23 × 0.79 × 1.42 m  (width × height × length)")
        stats = {"triangles": 3823200, "meshes": 165, "meshes_with_uvs": 0}
        self.assertEqual(turntable._model_text(stats), "3,823,200 triangles · 165 meshes · no UVs yet")
        self.assertIn("UVs on every mesh", turntable._model_text(dict(stats, meshes_with_uvs=165)))
        self.assertIn("UVs on 5 of 165 meshes", turntable._model_text(dict(stats, meshes_with_uvs=5)))

    def test_queue_shows_turntable_stages(self):
        job = self.job()
        queue = RenderQueue()
        queue._handle_line(job, 'CORELINE::STAGE {"name": "Rendering 120 frames"}')
        self.assertEqual(job.frames_label, "Turntable · Rendering 120 frames")
        queue._handle_line(job, 'CORELINE::DONE {"message": "Saved cheetah_geo_v001_turntable.mp4"}')
        self.assertEqual(job.message, "Saved cheetah_geo_v001_turntable.mp4")
        self.assertIsInstance(job, RenderJob)


if __name__ == "__main__":
    unittest.main()
