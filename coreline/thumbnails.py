"""Thumbnail pictures for assets and shots.

A picture called thumbnail.png/.jpg/... in an asset's or shot's folder is shown in place of
the striped placeholder. It lives in the shared project folder, so everyone sees it.

The Python on lab PCs has no image library and Tk can't resize photos smoothly, so the
resizing is done by Windows itself (System.Drawing, through PowerShell). Resized copies are
cached on this computer, keyed by the picture's path, size and date, so a changed picture
is picked up everywhere.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import powershell
from .project import PipelineError

THUMBNAIL_NAMES = ("thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg", "thumbnail.gif", "thumbnail.bmp")
IMAGE_FILETYPES = [("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.tif *.tiff"), ("All files", "*.*")]
# The copy kept in the project is shrunk to this many pixels on its longest side; plenty for
# the biggest place it's shown, and small enough that OneDrive syncs it quickly.
STORED_SIZE = 1280
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CORELINE" / "thumbs"

# A job is (source, destination, width, height, mode). "cover" fills width x height exactly,
# cropping the edges like a CSS background; "fit" shrinks (never enlarges) to fit inside it.
Job = Tuple[Path, Path, int, int, str]

_RESIZE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
# (Not wrapped in @(): Windows PowerShell hands back the whole JSON list as one item, so
# @() would make a list containing a list.)
$jobs = Get-Content -Raw -Encoding UTF8 -LiteralPath __JOBS__ | ConvertFrom-Json
$i = 0
foreach ($job in $jobs) {
    try {
        # Read through memory so the picture isn't locked while OneDrive wants it.
        $stream = New-Object IO.MemoryStream(, [IO.File]::ReadAllBytes($job.src))
        $img = [Drawing.Image]::FromStream($stream)
        # Phone photos are stored sideways with an EXIF note saying which way is up.
        if ($img.PropertyIdList -contains 0x0112) {
            switch ([int]$img.GetPropertyItem(0x0112).Value[0]) {
                3 { $img.RotateFlip('Rotate180FlipNone') }
                6 { $img.RotateFlip('Rotate90FlipNone') }
                8 { $img.RotateFlip('Rotate270FlipNone') }
            }
        }
        $iw = $img.Width; $ih = $img.Height
        if ($job.mode -eq 'cover') {
            $w = [int]$job.w; $h = [int]$job.h
            $scale = [Math]::Max($w / $iw, $h / $ih)
            $sw = $w / $scale; $sh = $h / $scale
            $src = New-Object Drawing.RectangleF((($iw - $sw) / 2), (($ih - $sh) / 2), $sw, $sh)
        } else {
            $scale = [Math]::Min(1.0, [Math]::Min($job.w / $iw, $job.h / $ih))
            $w = [Math]::Max(1, [int]($iw * $scale)); $h = [Math]::Max(1, [int]($ih * $scale))
            $src = New-Object Drawing.RectangleF(0, 0, $iw, $ih)
        }
        $bmp = New-Object Drawing.Bitmap($w, $h)
        $g = [Drawing.Graphics]::FromImage($bmp)
        $g.Clear([Drawing.Color]::FromArgb(25, 25, 28))
        $g.InterpolationMode = 'HighQualityBicubic'
        $g.PixelOffsetMode = 'HighQuality'
        $g.SmoothingMode = 'HighQuality'
        $attrs = New-Object Drawing.Imaging.ImageAttributes
        $attrs.SetWrapMode('TileFlipXY')  # stops a faint dark line along the edges
        $g.DrawImage($img, (New-Object Drawing.Rectangle(0, 0, $w, $h)), $src.X, $src.Y, $src.Width,
                     $src.Height, 'Pixel', $attrs)
        $g.Dispose()
        if ($job.dst.ToLower().EndsWith('.jpg')) {
            $codec = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
            $params = New-Object Drawing.Imaging.EncoderParameters(1)
            $params.Param[0] = New-Object Drawing.Imaging.EncoderParameter([Drawing.Imaging.Encoder]::Quality, [long]90)
            $bmp.Save($job.dst, $codec, $params)
        } else {
            $bmp.Save($job.dst, [Drawing.Imaging.ImageFormat]::Png)
        }
        $bmp.Dispose(); $img.Dispose(); $stream.Dispose()
        Write-Output "ok $i"
    } catch {
        Write-Output ("error $i " + $_.Exception.Message)
    }
    $i++
}
"""


def find(folder) -> Optional[Path]:
    """The thumbnail picture in an asset or shot folder, or None. One folder listing, not one
    check per name, because the folder may be on OneDrive."""
    try:
        names = {p.name.lower(): p for p in Path(folder).iterdir() if p.is_file()}
    except OSError:
        return None
    return next((names[n] for n in THUMBNAIL_NAMES if n in names), None)


def cache_path(source: Path, width: int, height: int) -> Path:
    """Where this computer keeps `source` resized to width x height."""
    try:
        stat = source.stat()
        stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        stamp = "missing"
    key = hashlib.sha1(f"{str(source).lower()}|{stamp}|{width}x{height}".encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{key}.png"


def resize(jobs: List[Job]) -> Dict[Path, str]:
    """Run the jobs in one PowerShell call. Returns {destination: error} for the ones that
    failed; each one that worked is written whole (never half a file)."""
    if not jobs:
        return {}
    errors: Dict[Path, str] = {}
    with tempfile.TemporaryDirectory(prefix="coreline_thumbs_") as tmp:
        tmp = Path(tmp)
        temps = [tmp / f"{i}{Path(dst).suffix.lower()}" for i, (_s, dst, _w, _h, _m) in enumerate(jobs)]
        spec = [{"src": str(src), "dst": str(t), "w": int(w), "h": int(h), "mode": mode}
                for (src, _d, w, h, mode), t in zip(jobs, temps)]
        jobs_file = tmp / "jobs.json"
        jobs_file.write_text(json.dumps(spec), encoding="utf-8")
        try:
            output = powershell.run(_RESIZE_SCRIPT.replace("__JOBS__", powershell.quote(jobs_file))).stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            return {Path(dst): f"Windows couldn't resize the picture ({e})." for _s, dst, *_ in jobs}
        messages = {}
        for line in output.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) >= 2 and parts[0] in ("ok", "error") and parts[1].isdigit():
                messages[int(parts[1])] = parts[2] if parts[0] == "error" and len(parts) > 2 else ""
        for i, (_src, dst, *_rest) in enumerate(jobs):
            dst = Path(dst)
            if messages.get(i) == "" and temps[i].is_file():
                # Copy next to the target first, then swap it in, so nobody sees half a picture.
                dst.parent.mkdir(parents=True, exist_ok=True)
                partial = dst.with_name(dst.name + ".partial")
                shutil.copyfile(temps[i], partial)
                os.replace(partial, dst)
            else:
                errors[dst] = messages.get(i) or "Windows couldn't read the picture."
    return errors


def make_cached(requests: List[Tuple[Path, int, int]]) -> Dict[Tuple[Path, int, int], Optional[Path]]:
    """Make the cached copies that don't exist yet. Returns {request: cached file or None}."""
    jobs: List[Job] = []
    for source, w, h in requests:
        target = cache_path(source, w, h)
        if not target.is_file():
            jobs.append((source, target, w, h, "cover"))
    errors = resize(jobs)
    result = {}
    for source, w, h in requests:
        target = cache_path(source, w, h)
        result[(source, w, h)] = target if target not in errors and target.is_file() else None
    return result


def set_thumbnail(folder, picture) -> Path:
    """Use `picture` as the thumbnail for this asset or shot folder. It's shrunk and saved as
    thumbnail.jpg (or copied as-is if Windows can't read it but Tk can), replacing any old one."""
    folder, picture = Path(folder), Path(picture)
    if not picture.is_file():
        raise PipelineError(f"Can't find the picture {picture}.")
    target = folder / "thumbnail.jpg"
    error = resize([(picture, target, STORED_SIZE, STORED_SIZE, "fit")]).get(target)
    if error:
        if picture.suffix.lower() not in (".png", ".gif"):
            raise PipelineError(f"Couldn't use {picture.name} as a thumbnail.\n\n{error}")
        target = folder / f"thumbnail{picture.suffix.lower()}"
        shutil.copyfile(picture, target)
    remove(folder, keep=target)
    return target


def remove(folder, keep: Optional[Path] = None) -> None:
    """Delete this folder's thumbnail pictures (all of them, apart from `keep`)."""
    try:
        files = [p for p in Path(folder).iterdir() if p.is_file() and p.name.lower() in THUMBNAIL_NAMES]
    except OSError:
        return
    for path in files:
        if keep is None or path.name.lower() != keep.name.lower():
            path.unlink()
