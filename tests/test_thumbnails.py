import os
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from baseline import thumbnails
from baseline.project import PipelineError


def write_png(path: Path, width: int, height: int) -> None:
    """A small gradient PNG, made by hand because there's no image library to make one."""
    rows = b"".join(b"\x00" + b"".join(bytes([x * 255 // width, y * 255 // height, 90]) for x in range(width))
                    for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def png_size(path: Path):
    return struct.unpack(">II", path.read_bytes()[16:24])


class ThumbnailFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "cheetah"
        (self.folder / "geo").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_thumbnail(self):
        self.assertIsNone(thumbnails.find(self.folder))
        self.assertIsNone(thumbnails.find(self.folder / "missing"))

    def test_finds_any_case_and_prefers_png(self):
        (self.folder / "Thumbnail.JPG").write_bytes(b"x")
        self.assertEqual(thumbnails.find(self.folder).name, "Thumbnail.JPG")
        (self.folder / "thumbnail.png").write_bytes(b"x")
        self.assertEqual(thumbnails.find(self.folder).name, "thumbnail.png")

    def test_cache_changes_when_the_picture_does(self):
        picture = self.folder / "thumbnail.png"
        picture.write_bytes(b"one")
        first = thumbnails.cache_path(picture, 206, 104)
        self.assertEqual(first, thumbnails.cache_path(picture, 206, 104))
        self.assertNotEqual(first, thumbnails.cache_path(picture, 56, 34))
        picture.write_bytes(b"changed")
        self.assertNotEqual(first, thumbnails.cache_path(picture, 206, 104))

    def test_remove_keeps_other_files(self):
        (self.folder / "thumbnail.png").write_bytes(b"x")
        (self.folder / "thumbnail.jpg").write_bytes(b"x")
        (self.folder / "notes.txt").write_bytes(b"x")
        thumbnails.remove(self.folder)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["geo", "notes.txt"])

    def test_png_is_copied_if_windows_cant_resize(self):
        picture = Path(self._tmp.name) / "cheetah.png"
        picture.write_bytes(b"png data")
        (self.folder / "thumbnail.jpg").write_bytes(b"old")
        with mock.patch.object(thumbnails, "resize", lambda jobs: {jobs[0][1]: "no"}):
            result = thumbnails.set_thumbnail(self.folder, picture)
        self.assertEqual(result.name, "thumbnail.png")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["geo", "thumbnail.png"])

    def test_unreadable_jpg_is_an_error(self):
        picture = Path(self._tmp.name) / "cheetah.jpg"
        picture.write_bytes(b"not really a jpg")
        with mock.patch.object(thumbnails, "resize", lambda jobs: {jobs[0][1]: "bad file"}):
            with self.assertRaises(PipelineError):
                thumbnails.set_thumbnail(self.folder, picture)
        self.assertIsNone(thumbnails.find(self.folder))


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "resizing uses Windows PowerShell")
class WindowsResizeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.folder = self.base / "it's a cheetah"  # an apostrophe must not break the script
        self.folder.mkdir()
        patcher = mock.patch.object(thumbnails, "CACHE_DIR", self.base / "cache")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def test_set_then_cache_several_sizes(self):
        picture = self.base / "photo.png"
        write_png(picture, 1600, 900)
        stored = thumbnails.set_thumbnail(self.folder, picture)
        self.assertEqual(stored.name, "thumbnail.jpg")
        self.assertEqual(thumbnails.find(self.folder), stored)
        made = thumbnails.make_cached([(stored, 206, 104), (stored, 56, 34)])
        self.assertEqual(png_size(made[(stored, 206, 104)]), (206, 104))
        self.assertEqual(png_size(made[(stored, 56, 34)]), (56, 34))
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["thumbnail.jpg"])

    def test_small_pictures_are_not_enlarged(self):
        picture = self.base / "small.png"
        write_png(picture, 40, 20)
        target = self.base / "out.png"
        self.assertEqual(thumbnails.resize([(picture, target, 1280, 1280, "fit")]), {})
        self.assertEqual(png_size(target), (40, 20))

    def test_broken_picture_reports_an_error(self):
        picture = self.base / "broken.png"
        picture.write_bytes(b"nope")
        target = self.base / "out.png"
        self.assertIn(target, thumbnails.resize([(picture, target, 50, 50, "cover")]))
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
