import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from coreline import apps, farm, render
from coreline.project import Project
from tests.test_farm import FAKE_RENDER, TEMPLATE


class SyncedFarmTests(unittest.TestCase):
    """The team farm in OneDrive: each PC claims in its own file, and the earliest claim wins."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.farm = farm.Farm(Path(self._tmp.name) / "team", farm.TEAM, synced=True)
        patcher = mock.patch.object(farm, "SETTLE_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.job_id = self.farm.submit(project_name="CORE", scene="shots/SH010/sh010_light_v001.hip",
                                       node="/stage/karma", start=1, end=20, step=1, chunk_size=10,
                                       houdini_version="21.0.729", user="ritvik")
        self.chunks = self.farm.jobs_dir / self.job_id / "chunks"

    def tearDown(self):
        self._tmp.cleanup()

    def _claim_file(self, pc, chunk=(1, 10), at=None, beat=None):
        at = time.time() if at is None else at
        (self.chunks / f"{farm.chunk_name(chunk)}.claim.{pc}").write_text(json.dumps(
            {"pc": pc, "at": at, "beat": at if beat is None else beat, "frame": None}))

    def test_one_pc_per_chunk(self):
        self.assertEqual(self.farm.claim_next("PC-A", "CORE")[1], (1, 10))
        self.assertEqual(self.farm.claim_next("PC-B", "CORE")[1], (11, 20))  # PC-A's claim is live
        self.assertIsNone(self.farm.claim_next("PC-C", "CORE"))
        self.assertEqual(self.farm.status(self.job_id).chunks[0].pc, "PC-A")
        self.assertTrue((self.chunks / "1_10.claim.PC-A").is_file())

    def test_earliest_claim_wins_when_two_arrive_together(self):
        def other_pc_claims_meanwhile(seconds):
            self._claim_file("PC-B", at=time.time() - 5)  # PC-B claimed first; OneDrive brings it in now
            return False

        picked = self.farm.claim_next("PC-A", "CORE", wait=other_pc_claims_meanwhile)
        self.assertEqual(picked[1], (11, 20))  # PC-A gave up 1-10 and took the next chunk
        self.assertFalse((self.chunks / "1_10.claim.PC-A").exists())
        self.assertTrue(self.farm.owns(self.job_id, (1, 10), "PC-B"))

    def test_a_late_earlier_claim_takes_the_chunk_over(self):
        self.farm.claim_next("PC-A", "CORE")
        self.assertTrue(self.farm.heartbeat(self.job_id, (1, 10), "PC-A", frame=3))
        self._claim_file("PC-B", at=time.time() - 120)  # OneDrive was slow: PC-B had it first
        self.assertFalse(self.farm.heartbeat(self.job_id, (1, 10), "PC-A", frame=4))

    def test_a_pc_that_went_away_loses_its_chunk(self):
        old = time.time() - farm.SYNCED_STALE_SECONDS - 60
        self._claim_file("PC-A", at=old, beat=old)
        self.assertEqual(self.farm.claim_next("PC-B", "CORE")[1], (1, 10))
        self.assertFalse((self.chunks / "1_10.claim.PC-A").exists())  # moved aside: it can't win again
        self.assertTrue(any(".stale_PC-B_" in p.name for p in self.chunks.iterdir()))

    def test_stopping_while_waiting_gives_the_chunk_back(self):
        self.assertIsNone(self.farm.claim_next("PC-A", "CORE", wait=lambda s: True))
        self.assertFalse(list(self.chunks.glob("*.claim*")))

    def test_done_and_fail_files_are_one_per_pc(self):
        self.farm.claim_next("PC-A", "CORE")
        self.farm.fail(self.job_id, (1, 10), "PC-A", "Karma crashed")
        self.farm.claim_next("PC-B", "CORE")  # takes 1-10 (PC-A failed it once)
        self.farm.fail(self.job_id, (1, 10), "PC-B", "Karma crashed")
        self.assertTrue((self.chunks / "1_10.fail.PC-A").is_file() and (self.chunks / "1_10.fail.PC-B").is_file())
        self.assertEqual(self.farm.status(self.job_id).chunks[0].attempts, 2)
        self.assertEqual(self.farm.retry_failed(self.job_id), 1)
        self.assertFalse(list(self.chunks.glob("1_10.fail*")))
        self.farm.claim_next("PC-C", "CORE")
        self.farm.finish(self.job_id, (1, 10), "PC-C", 30)
        self.assertTrue((self.chunks / "1_10.done.PC-C").is_file())
        self.assertEqual(self.farm.status(self.job_id).chunks[0].state, "done")

    def test_workers_use_their_own_clock(self):
        self.farm.worker_checkin("PC-A", {"state": "Idle"})
        self.assertTrue(self.farm.workers()[0]["online"])
        info = json.loads((self.farm.workers_dir / "PC-A.json").read_text())
        info["beat"] = time.time() - farm.SYNCED_OFFLINE_SECONDS - 5  # file time is new, but the PC went quiet
        (self.farm.workers_dir / "PC-A.json").write_text(json.dumps(info))
        self.assertFalse(self.farm.workers()[0]["online"])


class OldStyleFilesTests(unittest.TestCase):
    def test_jobs_from_before_still_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = farm.Farm(Path(tmp) / "farm")
            job_id = f.submit(project_name="CORE", scene="a.hip", node="/out/x", start=1, end=20, step=1,
                              chunk_size=10, houdini_version="", user="ritvik")
            chunks = f.jobs_dir / job_id / "chunks"
            (chunks / "1_10.done").write_text(json.dumps({"pc": "PC-A", "seconds": 5}))
            (chunks / "11_20.fail").write_text(json.dumps({"pc": "PC-A", "message": "x"}) + "\n")
            states = [(c.state, c.attempts) for c in f.status(job_id).chunks]
            self.assertEqual(states, [("done", 0), ("retry", 1)])


class FarmChoiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "project"
        (root / "_ADMIN").mkdir(parents=True)
        self.project = Project.create(root, TEMPLATE, name="CORE")

    def tearDown(self):
        self._tmp.cleanup()

    def test_mine_first_then_team(self):
        mine = Path(self._tmp.name) / "H" / "CORELINE_farm"
        with mock.patch.object(farm.settings, "get_user_setting", lambda key, default=None: str(mine)
                               if key == "farm_folder" else default):
            farms = farm.farms_for(self.project)
        self.assertEqual([(f.kind, f.label) for f in farms], [(farm.MINE, "My PCs (H:)"), (farm.TEAM, "Team (OneDrive)")])
        self.assertEqual(farms[1].folder, self.project.tracking_root / "farm")

    def test_only_the_team_farm_without_an_h_drive(self):
        with mock.patch.object(farm.settings, "get_user_setting", lambda key, default=None: default), \
                mock.patch.object(farm, "DEFAULT_FOLDER", "Q:/CORELINE_farm"):
            farms = farm.farms_for(self.project)
        self.assertEqual([f.kind for f in farms], [farm.TEAM])


class TwoFarmWorkerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        root = base / "project"
        (root / "shots/SH010/lighting/wip").mkdir(parents=True)
        self.project = Project.create(root, TEMPLATE, name="CORE")
        (root / "shots/SH010/lighting/wip/sh010_light_v001.hip").write_text("")
        self.mine = farm.Farm(base / "H", farm.MINE)
        self.team = farm.Farm(base / "team", farm.TEAM, synced=True)
        fake = base / "fake_render.py"
        fake.write_text(FAKE_RENDER)
        python = Path(sys.executable)
        self.app_cfg = {"tools": {"hython": python.name}}
        self.installs = [apps.Installation("houdini", "Houdini", "21.0.729", python, python.parent)]
        for p in (mock.patch.object(render, "SCRIPT", fake), mock.patch.object(farm, "POLL_SECONDS", 0.2),
                  mock.patch.object(farm, "SETTLE_SECONDS", 0), mock.patch.object(farm, "keep_awake", lambda on: None)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _submit(self, f):
        return f.submit(project_name="CORE", scene="shots/SH010/lighting/wip/sh010_light_v001.hip",
                        node="/stage/karma", start=1, end=4, step=1, chunk_size=2, houdini_version="21.0.729",
                        user="ritvik")

    def test_worker_renders_both_farms_jobs(self):
        team_job, my_job = self._submit(self.team), self._submit(self.mine)
        events = []
        worker = farm.FarmWorker([self.mine, self.team], self.project, "ritvik", self.installs, self.app_cfg,
                                 on_event=events.append)
        worker.start()
        deadline = time.time() + 60
        while time.time() < deadline and not (self.mine.status(my_job).status == "Done"
                                              and self.team.status(team_job).status == "Done"):
            if self.team.status(team_job).count("done") and not self.mine.status(my_job).status == "Done":
                self.fail("The team's job went before this PC's own")
            time.sleep(0.1)
        checked_in = [f.workers()[0].get("farms") for f in (self.mine, self.team)]
        worker.stop()
        while worker.running:
            time.sleep(0.05)
        self.assertEqual((self.mine.status(my_job).status, self.team.status(team_job).status), ("Done", "Done"))
        self.assertEqual(checked_in, [[farm.MINE, farm.TEAM]] * 2)  # it shows on both farms' PC lists
        self.assertTrue(list((self.team.jobs_dir / team_job / "chunks").glob(f"*.done.{farm.pc_name()}")))


if __name__ == "__main__":
    unittest.main()
