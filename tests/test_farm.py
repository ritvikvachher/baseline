import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from baseline import apps, farm, render
from baseline.project import Project

TEMPLATE = json.loads((Path(__file__).resolve().parents[1] / "config" / "project_template.json").read_text())

# Stands in for hython + render_hip.py: reports the frames like the real thing, and fails for
# scenes with "bad" in the name.
FAKE_RENDER = '''
import json, sys
hip, node, start, end, step = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
if "bad" in hip:
    print('BASELINE::ERROR {"message": "Karma crashed"}', flush=True)
    sys.exit(1)
frames = list(range(start, end + 1, step))
print("BASELINE::START " + json.dumps({"total": len(frames)}), flush=True)
for i, f in enumerate(frames, 1):
    print("BASELINE::FRAME " + json.dumps({"frame": f, "index": i, "total": len(frames)}), flush=True)
    print("BASELINE::FRAME_DONE " + json.dumps({"frame": f, "index": i, "total": len(frames), "seconds": 0}),
          flush=True)
print("ARGS " + json.dumps(sys.argv[1:]), flush=True)
print("BASELINE::FINISHED {}", flush=True)
'''


class ChunkTests(unittest.TestCase):
    def test_make_chunks(self):
        self.assertEqual(farm.make_chunks(1001, 1025, 1, 10), [(1001, 1010), (1011, 1020), (1021, 1025)])
        self.assertEqual(farm.make_chunks(1, 9, 2, 2), [(1, 3), (5, 7), (9, 9)])
        self.assertEqual(farm.make_chunks(5, 5, 1, 10), [(5, 5)])

    def test_project_relative_paths(self):
        self.assertEqual(farm.project_relative("X:/", "X:/shots/SH010/a.hip"), "shots/SH010/a.hip")
        self.assertEqual(farm.project_relative("X:\\", "x:\\shots\\a.hip"), "shots/a.hip")
        self.assertEqual(farm.project_relative("X:/", "D:/elsewhere/a.hip"), "D:/elsewhere/a.hip")
        self.assertEqual(farm.resolve("Y:/", "shots/SH010/a.$F4.exr"), "Y:/shots/SH010/a.$F4.exr")
        self.assertEqual(farm.resolve("Y:/", "D:/elsewhere/a.hip"), "D:/elsewhere/a.hip")
        self.assertEqual(farm.resolve("Y:/", "$HIP/render/a.exr"), "$HIP/render/a.exr")

    def test_onedrive_folder_is_flagged(self):
        self.assertTrue(farm.is_on_onedrive(r"C:\Users\s1\OneDrive - Bournemouth University\CORE\_ADMIN\farm"))
        self.assertFalse(farm.is_on_onedrive("H:/BASELINE_farm"))


class FarmFolderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.farm = farm.Farm(Path(self._tmp.name) / "farm")
        self.job_id = self.farm.submit(project_name="CORE", scene="shots/SH010/sh010_light_v001.hip",
                                       node="/stage/karma", start=1001, end=1025, step=1, chunk_size=10,
                                       houdini_version="21.0.729", user="ritvik")

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_chunk_goes_to_one_pc(self):
        a = self.farm.claim_next("PC-A", "CORE")
        b = self.farm.claim_next("PC-B", "CORE")
        self.assertEqual(a[1], (1001, 1010))
        self.assertEqual(b[1], (1011, 1020))
        status = self.farm.status(self.job_id)
        self.assertEqual([c.state for c in status.chunks], ["rendering", "rendering", "waiting"])
        self.assertEqual(status.pcs, ["PC-A", "PC-B"])
        self.assertEqual(status.status, "Rendering")

    def test_other_projects_jobs_are_left_alone(self):
        self.assertIsNone(self.farm.claim_next("PC-A", "SOME_OTHER_FILM"))

    def test_finishing_every_chunk_finishes_the_job(self):
        while True:
            picked = self.farm.claim_next("PC-A", "CORE")
            if not picked:
                break
            self.farm.finish(self.job_id, picked[1], "PC-A", 12)
        status = self.farm.status(self.job_id)
        self.assertEqual(status.status, "Done")
        self.assertEqual((status.frames_done, status.frames_total), (25, 25))
        self.assertFalse(list((self.farm.jobs_dir / self.job_id / "chunks").glob("*.claim")))

    def test_a_pc_that_went_away_loses_its_chunk(self):
        _, chunk = self.farm.claim_next("PC-A", "CORE")
        claim = self.farm._chunk_file(self.job_id, chunk, "claim")
        old = time.time() - farm.STALE_SECONDS - 5
        os.utime(claim, (old, old))  # PC-A restarted for Windows updates and stopped touching it
        self.assertEqual(self.farm.status(self.job_id).chunks[0].state, "waiting")
        self.assertEqual(self.farm.claim_next("PC-B", "CORE")[1], chunk)
        self.assertTrue(self.farm.owns(self.job_id, chunk, "PC-B"))
        # PC-A comes back: its heartbeat tells it to let go, and it can't release PC-B's claim.
        self.assertFalse(self.farm.heartbeat(self.job_id, chunk, "PC-A"))
        self.farm.release(self.job_id, chunk, "PC-A")
        self.assertTrue(self.farm.owns(self.job_id, chunk, "PC-B"))

    def test_heartbeat_keeps_the_claim_and_reports_the_frame(self):
        _, chunk = self.farm.claim_next("PC-A", "CORE")
        self.assertTrue(self.farm.heartbeat(self.job_id, chunk, "PC-A", frame=1004))
        self.assertEqual(self.farm.status(self.job_id).chunks[0].frame, 1004)

    def test_failed_chunks_are_retried_then_given_up(self):
        chunk = (1001, 1010)
        self.assertEqual(self.farm.claim_next("PC-A", "CORE")[1], chunk)
        self.farm.fail(self.job_id, chunk, "PC-A", "Karma crashed")
        self.assertEqual(self.farm.status(self.job_id).chunks[0].state, "retry")
        self.assertEqual(self.farm.claim_next("PC-A", "CORE")[1], chunk)   # PC-A's second go
        self.farm.fail(self.job_id, chunk, "PC-A", "Karma crashed")
        self.assertEqual(self.farm.claim_next("PC-A", "CORE")[1], (1011, 1020))  # PC-A gave up on it
        self.assertEqual(self.farm.claim_next("PC-B", "CORE")[1], chunk)  # PC-B tries it
        self.farm.fail(self.job_id, chunk, "PC-B", "Karma crashed")
        first = self.farm.status(self.job_id).chunks[0]
        self.assertEqual((first.state, first.attempts, first.message), ("failed", 3, "Karma crashed"))
        self.assertEqual(self.farm.retry_failed(self.job_id), 1)
        self.assertEqual(self.farm.status(self.job_id).chunks[0].state, "waiting")

    def test_split_turntable_joins_the_pieces_last(self):
        job_id = self.farm.submit_turntable(project_name="CORE", turntable={"source": "assets/characters/cheetah/lookdev/wip/cheetah_skele_test.hipnc",
                     "out_mp4": "assets/characters/cheetah/lookdev/wip/turntables/x_turntable.mp4",
                     "kind": "lookdev", "context": {"task": "lookdev"}}, frames=70, split=True,
                                            chunk_size=30, houdini_version="21.0.729", user="ritvik")
        status = self.farm.status(job_id)
        self.assertEqual([c.chunk for c in status.chunks], [(1, 30), (31, 60), (61, 70), farm.FINISH])
        self.assertEqual(status.frames_total, 70)
        self.assertEqual(status.chunks[-1].label, "MP4")
        self.assertTrue(self.farm.parts_dir(job_id).is_dir())
        self.farm.cancel(self.job_id)  # leave only the turntable to claim
        picked = [self.farm.claim_next(pc, "CORE")[1] for pc in ("PC-A", "PC-B", "PC-C")]
        self.assertEqual(picked, [(1, 30), (31, 60), (61, 70)])
        self.assertIsNone(self.farm.claim_next("PC-D", "CORE"))  # the MP4 waits for every piece
        for pc, chunk in zip(("PC-A", "PC-B"), picked):
            self.farm.finish(job_id, chunk, pc, 60)
        self.assertIsNone(self.farm.claim_next("PC-D", "CORE"))
        self.farm.finish(job_id, picked[2], "PC-C", 60)
        self.assertEqual(self.farm.claim_next("PC-D", "CORE")[1], farm.FINISH)
        self.farm.finish(job_id, farm.FINISH, "PC-D", 5)
        self.assertEqual(self.farm.status(job_id).status, "Done")

    def test_whole_turntable_is_one_chunk(self):
        job_id = self.farm.submit_turntable(project_name="CORE", turntable={"source": "assets/characters/cheetah/lookdev/wip/cheetah_skele_test.hipnc",
                     "out_mp4": "assets/characters/cheetah/lookdev/wip/turntables/x_turntable.mp4",
                     "kind": "lookdev", "context": {"task": "lookdev"}}, frames=240, split=False,
                                            chunk_size=30, houdini_version="21.0.729", user="ritvik")
        job = self.farm.read_job(job_id)
        self.assertEqual((job["type"], job["mode"]), ("turntable", "whole"))
        self.assertEqual([c.chunk for c in self.farm.status(job_id).chunks], [(1, 240)])

    def test_cancel_and_remove(self):
        self.farm.cancel(self.job_id)
        self.assertEqual(self.farm.status(self.job_id).status, "Cancelled")
        self.assertIsNone(self.farm.claim_next("PC-A", "CORE"))
        self.farm.remove(self.job_id)
        self.assertEqual(self.farm.job_ids(), [])

    def test_workers_check_in_and_can_be_stopped(self):
        self.farm.worker_checkin("PC-A", {"state": "Idle"})
        self.assertEqual([(w["pc"], w["online"]) for w in self.farm.workers()], [("PC-A", True)])
        self.farm.ask_to_stop("PC-A")
        self.assertTrue(self.farm.stop_requested("PC-A"))
        self.assertFalse(self.farm.stop_requested("PC-A"))  # only once
        self.farm.worker_gone("PC-A")
        self.assertFalse(self.farm.workers()[0]["online"])


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        root = base / "project"
        (root / "shots/SH010/lighting/wip").mkdir(parents=True)
        self.project = Project.create(root, TEMPLATE, name="CORE")
        for name in ("sh010_light_v001.hip", "sh010_bad_v001.hip"):
            (root / "shots/SH010/lighting/wip" / name).write_text("")
        self.farm = farm.Farm(base / "farm")
        fake = base / "fake_render.py"
        fake.write_text(FAKE_RENDER)
        python = Path(sys.executable)
        self.app_cfg = {"tools": {"hython": python.name}}
        self.installs = [apps.Installation("houdini", "Houdini", "21.0.729", python, python.parent)]
        self.events = []
        patches = [mock.patch.object(render, "SCRIPT", fake), mock.patch.object(farm, "POLL_SECONDS", 0.2),
                   mock.patch.object(farm, "keep_awake", lambda on: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _submit(self, scene, **kw):
        return self.farm.submit(project_name="CORE", scene=f"shots/SH010/lighting/wip/{scene}",
                                node="/stage/karma", start=1001, end=1005, step=1, chunk_size=2,
                                houdini_version="21.0.729", user="ritvik",
                                output_override="shots/SH010/render/beauty/x/x.$F4.exr", **kw)

    def _run_until(self, worker, done, timeout=60):
        worker.start()
        deadline = time.time() + timeout
        while not done() and time.time() < deadline:
            time.sleep(0.1)
        worker.stop()
        while worker.running:
            time.sleep(0.05)

    def _worker(self):
        return farm.FarmWorker(self.farm, self.project, "ritvik", self.installs, self.app_cfg,
                               on_event=self.events.append)

    def test_worker_renders_every_chunk(self):
        job_id = self._submit("sh010_light_v001.hip")
        self._run_until(self._worker(), lambda: self.farm.status(job_id).status == "Done")
        status = self.farm.status(job_id)
        self.assertEqual(status.status, "Done")
        self.assertEqual([c.chunk for c in status.chunks], [(1001, 1002), (1003, 1004), (1005, 1005)])
        # Each chunk rendered with skip-existing, into the project on this PC.
        log = self.farm.log_file(job_id, (1003, 1004), farm.pc_name()).read_text()
        args = json.loads(log.split("ARGS ", 1)[1].splitlines()[0])
        self.assertEqual(args[3:6], ["1003", "1004", "1"])
        self.assertIn("--skip-existing", args)
        self.assertEqual(args[args.index("--output") + 1],
                         (self.project.root / "shots/SH010/render/beauty/x/x.$F4.exr").as_posix())
        self.assertFalse(self.farm.workers()[0]["online"])  # checked out when stopped

    def test_worker_gives_up_on_a_job_that_keeps_failing(self):
        bad = self._submit("sh010_bad_v001.hip")
        good = self._submit("sh010_light_v001.hip")
        self._run_until(self._worker(), lambda: self.farm.status(good).status == "Done")
        self.assertEqual(self.farm.status(good).status, "Done")
        states = [c.state for c in self.farm.status(bad).chunks]
        self.assertNotIn("done", states)
        self.assertTrue(any("FAILED" in e and "Karma crashed" in e for e in self.events))

    def test_worker_makes_turntable_pieces_then_the_mp4(self):
        hip = "shots/SH010/lighting/wip/sh010_light_v001.hip"
        job_id = self.farm.submit_turntable(
            project_name="CORE", turntable={"source": hip, "out_mp4": "x.mp4", "kind": "lookdev",
                                            "context": {"task": "lighting"}},
            frames=5, split=True, chunk_size=2, houdini_version="21.0.729", user="ritvik")
        calls = []

        def fake_farm_job(portable, project, user, **kw):
            calls.append(kw)
            return render.RenderJob(hip=project.root / hip, node="/stage/karma", start=1, end=1, step=1,
                                    hython=kw["houdini_bin"] / Path(sys.executable).name,
                                    houdini_label="21.0.729", env=dict(os.environ), log_path=kw["log_path"])

        from baseline import turntable
        with mock.patch.object(turntable, "farm_job", fake_farm_job):
            self._run_until(self._worker(), lambda: self.farm.status(job_id).status == "Done")
        self.assertEqual(self.farm.status(job_id).status, "Done")
        parts = self.farm.parts_dir(job_id)
        pieces = [c for c in calls if c["frame_range"]]
        self.assertEqual([c["frame_range"] for c in pieces], [(1, 2), (3, 4), (5, 5)])
        self.assertEqual(pieces[1]["part_out"], parts / "part_0003_0004.mp4")
        finish = calls[-1]
        self.assertIsNone(finish["frame_range"])
        self.assertEqual(finish["finish_parts"], [parts / "part_0001_0002.mp4", parts / "part_0003_0004.mp4",
                                                  parts / "part_0005_0005.mp4"])
        self.assertFalse(parts.exists())  # cleared once they're in the MP4

    def test_missing_scene_fails_the_chunk(self):
        job_id = self._submit("not_synced_v001.hip")
        self._run_until(self._worker(), lambda: self.farm.status(job_id).count("retry") >= 1, timeout=10)
        self.assertIn("isn't on this PC", self.farm.status(job_id).chunks[0].message)


if __name__ == "__main__":
    unittest.main()
