import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coreline import apps, drives, settings
from coreline.render import RenderJob, RenderQueue, parse_message

HOUDINI_CFG = {
    "label": "Houdini",
    "exe": "bin/houdini.exe",
    "extensions": [".hip", ".hipnc"],
    "version_regex": r"Houdini (\d+\.\d+\.\d+)",
    "file_version_regex": r"_HIP_SAVEVERSION = '([\d.]+)'",
    "tools": {"hython": "bin/hython.exe"},
}


class AppTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        for version in ("20.5.613", "21.0.729"):
            bin_dir = self.base / f"Houdini {version}" / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "houdini.exe").write_text("")
            (bin_dir / "hython.exe").write_text("")

    def tearDown(self):
        self._tmp.cleanup()

    def _cfg(self):
        return dict(HOUDINI_CFG, paths=[str(self.base / "Houdini [0-9]*" / "bin" / "houdini.exe")])

    def test_detects_all_versions_newest_first(self):
        installs = apps.detect_installations("houdini", self._cfg())
        self.assertEqual([i.version for i in installs], ["21.0.729", "20.5.613"])
        self.assertEqual(installs[0].install_dir, self.base / "Houdini 21.0.729")
        self.assertEqual(installs[0].tool(self._cfg(), "hython"),
                         self.base / "Houdini 21.0.729" / "bin" / "hython.exe")

    def test_extra_location_and_no_duplicates(self):
        exe = self.base / "Houdini 21.0.729" / "bin" / "houdini.exe"
        installs = apps.detect_installations("houdini", self._cfg(), extra_exes=[str(exe)])
        self.assertEqual(len(installs), 2)

    def test_saved_version_and_picking(self):
        hip = self.base / "scene.hip"
        hip.write_bytes(b"junk\nset -g _HIP_SAVEVERSION = '20.5.613'\nmore")
        self.assertEqual(apps.saved_version(HOUDINI_CFG, hip), "20.5.613")
        installs = apps.detect_installations("houdini", self._cfg())
        self.assertEqual(apps.pick_installation(installs, "20.5.613").version, "20.5.613")
        self.assertEqual(apps.pick_installation(installs, "19.0.1").version, "21.0.729")
        self.assertEqual(apps.pick_installation(installs, None, "20.5.613").version, "20.5.613")
        self.assertIsNone(apps.pick_installation([], "20.5.613"))

    def test_app_for_extension(self):
        self.assertEqual(apps.app_for_extension({"houdini": HOUDINI_CFG}, ".HIPNC"), "houdini")
        self.assertIsNone(apps.app_for_extension({"houdini": HOUDINI_CFG}, ".ma"))


class SettingsTests(unittest.TestCase):
    def test_portable_path_uses_onedrive_variable(self):
        env = {"OneDriveCommercial": r"C:\Users\sam\OneDrive - Uni", "OneDrive": r"C:\Users\sam\OneDrive - Uni"}
        with mock.patch.dict(os.environ, env):
            text = settings.portable_path(r"C:\Users\sam\OneDrive - Uni\FMP\Film")
            self.assertEqual(text, "%OneDriveCommercial%/FMP/Film")
            self.assertEqual(settings.expand_path(text), Path(r"C:\Users\sam\OneDrive - Uni\FMP\Film"))
            self.assertEqual(settings.portable_path(r"D:\Film"), "D:/Film")

    def test_bournemouth_onedrive_and_sharepoint_paths(self):
        env = {"OneDriveCommercial": r"C:\Users\s5707305\OneDrive - Bournemouth University",
               "OneDrive": r"C:\Users\s5707305\OneDrive - Bournemouth University",
               "USERPROFILE": r"C:\Users\s5707305"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(
                settings.portable_path(r"C:\Users\s5707305\OneDrive - Bournemouth University\FMP_FILM"),
                "%OneDriveCommercial%/FMP_FILM")
            # A Teams/SharePoint library synced outside OneDrive still works for other users.
            self.assertEqual(
                settings.portable_path(r"C:\Users\s5707305\Bournemouth University\FMP Team - Documents"),
                "%USERPROFILE%/Bournemouth University/FMP Team - Documents")
        # The same saved text opens the right folder for a teammate.
        other = {"OneDriveCommercial": r"C:\Users\s5711111\OneDrive - Bournemouth University"}
        with mock.patch.dict(os.environ, other):
            self.assertEqual(settings.expand_path("%OneDriveCommercial%/FMP_FILM"),
                             Path(r"C:\Users\s5711111\OneDrive - Bournemouth University\FMP_FILM"))

    def test_sanitize_user(self):
        self.assertEqual(settings.sanitize_user("Ritvik Vachher"), "ritvikvachher")
        self.assertEqual(settings.sanitize_user("!!"), "user")


class DriveTests(unittest.TestCase):
    def test_parse_subst_output(self):
        text = "\r\n".join([r"X:\: => C:\Users\sam\OneDrive - Uni\Film", r"P:\: => D:\Stuff", ""])
        self.assertEqual(drives.parse_subst_output(text),
                         {"X": r"C:\Users\sam\OneDrive - Uni\Film", "P": r"D:\Stuff"})


class RenderMessageTests(unittest.TestCase):
    def test_parse_message(self):
        self.assertEqual(parse_message('CORELINE::FRAME {"frame": 3}'), ("FRAME", {"frame": 3}))
        self.assertEqual(parse_message("CORELINE::FINISHED {}"), ("FINISHED", {}))
        self.assertEqual(parse_message("Karma: 50% done"), (None, None))


class RenderJobTests(unittest.TestCase):
    def _job(self, **kw):
        return RenderJob(hip=Path("X:/shots/a.hip"), node="/out/karma1", start=1001, end=1010, step=1,
                         hython=Path("hython.exe"), houdini_label="21.0", env={}, log_path=Path("x.log"), **kw)

    def test_command_includes_karma_options(self):
        cmd = self._job(res_percent=50, skip_existing=True).command()
        self.assertEqual(cmd[-3:], ["--res", "50", "--skip-existing"])
        self.assertNotIn("--res", self._job().command())

    def test_command_sends_output_to_shot_folder(self):
        out = "X:/shots/SH010/render/beauty/a/a.$F4.exr"
        self.assertEqual(self._job(output_override=out).command()[-2:], ["--output", out])
        self.assertNotIn("--output", self._job().command())

    def test_progress_from_karma_output(self):
        queue, job = RenderQueue(), self._job(res_percent=25)
        job.status = "Rendering"
        for line in ['CORELINE::START {"total": 10}',
                     'CORELINE::FRAME_SKIPPED {"frame": 1001, "index": 1, "total": 10}',
                     'CORELINE::FRAME {"frame": 1002, "index": 2, "total": 10}',
                     "ALF_PROGRESS 42%"]:
            queue._handle_line(job, line)
        self.assertEqual(job.progress_label, "1/10, frame 1002 42% (1 skipped)")
        self.assertEqual(job.frames_label, "1001-1010 @25%")
        self.assertFalse(any("ALF_PROGRESS" in l for l in job.lines))  # kept out of the log


if __name__ == "__main__":
    unittest.main()
