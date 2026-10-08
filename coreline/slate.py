"""The title card at the start of a turntable, and the strip of text over every frame.

Drawn by Windows (System.Drawing, through PowerShell), because Houdini's ffmpeg can't draw
text and the lab PCs have no Python image library.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from . import powershell

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$spec = Get-Content -Raw -Encoding UTF8 -LiteralPath __SPEC__ | ConvertFrom-Json
$w = [int]$spec.width; $h = [int]$spec.height
$k = $h / 720.0   # everything below is laid out for 720 lines and scaled to fit
function Color($hex) { [Drawing.ColorTranslator]::FromHtml($hex) }
function Brush($hex) { New-Object Drawing.SolidBrush (Color $hex) }
function Font($name, $size, $style) { New-Object Drawing.Font($name, [float]($size * $k), [Drawing.FontStyle]$style, [Drawing.GraphicsUnit]::Pixel) }
function Setup($g) {
    $g.SmoothingMode = 'AntiAlias'; $g.InterpolationMode = 'HighQualityBicubic'
    $g.TextRenderingHint = 'AntiAliasGridFit'
}

# --- Slate -------------------------------------------------------------------
$bmp = New-Object Drawing.Bitmap($w, $h)
$g = [Drawing.Graphics]::FromImage($bmp); Setup $g
$g.Clear((Color '#0e0e10'))
$g.FillRectangle((Brush '#d0141d'), 0, 0, [int](10 * $k), $h)
$x = 80 * $k; $y = 64 * $k
if ($spec.logo -and (Test-Path -LiteralPath $spec.logo)) {
    $logo = [Drawing.Image]::FromFile($spec.logo)
    $lh = 64 * $k; $lw = $logo.Width * $lh / $logo.Height
    $g.DrawImage($logo, [float]$x, [float]$y, [float]$lw, [float]$lh); $logo.Dispose()
}
$y += 120 * $k
$g.DrawString($spec.title, (Font 'Segoe UI' 64 'Bold, Italic'), (Brush '#ececee'), [float]($x - 6 * $k), [float]$y)
$y += 92 * $k
$g.DrawString($spec.subtitle, (Font 'Segoe UI Semibold' 24 'Regular'), (Brush '#e8262f'), [float]$x, [float]$y)
$y += 64 * $k
$keyFont = Font 'Consolas' 17 'Regular'; $valueFont = Font 'Segoe UI' 21 'Regular'
$valueBox = New-Object Drawing.RectangleF([float]($x + 190 * $k), 0, [float]($w - $x - 270 * $k), [float](60 * $k))
foreach ($row in $spec.rows) {
    $g.DrawString($row[0].ToUpper(), $keyFont, (Brush '#8c8c94'), [float]$x, [float]($y + 6 * $k))
    $valueBox.Y = [float]$y
    $g.DrawString($row[1], $valueFont, (Brush '#ececee'), $valueBox)
    $y += [Math]::Max(38 * $k, $g.MeasureString($row[1], $valueFont, [int]$valueBox.Width).Height + 10 * $k)
}
if ($spec.warning) {
    $box = New-Object Drawing.RectangleF([float]$x, [float]($y + 10 * $k), [float]($w - $x - 80 * $k), [float](80 * $k))
    $g.DrawString($spec.warning, (Font 'Segoe UI Semibold' 19 'Regular'), (Brush '#eaa33c'), $box)
}
$foot = Font 'Consolas' 15 'Regular'
$g.DrawString($spec.footer, $foot, (Brush '#5d5d64'), [float]$x, [float]($h - 54 * $k))
$g.Dispose(); $bmp.Save($spec.slate, [Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()

# --- Burn-in strip (transparent apart from the strip) --------------------------
$bmp = New-Object Drawing.Bitmap($w, $h, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
$g = [Drawing.Graphics]::FromImage($bmp); Setup $g
$g.Clear([Drawing.Color]::Transparent)
$bar = 40 * $k
$g.FillRectangle((New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(170, 8, 8, 10))), 0, [float]($h - $bar), $w, [float]$bar)
$text = Font 'Consolas' 15 'Regular'
$ty = $h - $bar + 11 * $k
$g.DrawString($spec.corner_left, $text, (Brush '#ececee'), [float](16 * $k), [float]$ty)
# The CORE logo in the right-hand corner, with the date to its left.
$right = $w - 16 * $k
if ($spec.logo -and (Test-Path -LiteralPath $spec.logo)) {
    $logo = [Drawing.Image]::FromFile($spec.logo)
    $lh = $bar - 12 * $k; $lw = $logo.Width * $lh / $logo.Height
    $g.DrawImage($logo, [float]($right - $lw), [float]($h - $bar + 6 * $k), [float]$lw, [float]$lh); $logo.Dispose()
    $right -= $lw + 14 * $k
}
$size = $g.MeasureString($spec.corner_right, $text)
$g.DrawString($spec.corner_right, $text, (Brush '#8c8c94'), [float]($right - $size.Width), [float]$ty)
$g.Dispose(); $bmp.Save($spec.overlay, [Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
Write-Output 'ok'
"""


def draw(slate: Path, overlay: Path, size: Tuple[int, int], title: str, subtitle: str,
         rows: List[Tuple[str, str]], corner_left: str, corner_right: str, footer: str = "",
         warning: str = "", logo: Optional[Path] = None) -> None:
    """Writes the slate (a full frame) and the overlay (the bottom strip, on transparent)."""
    spec = {"width": size[0], "height": size[1], "title": title, "subtitle": subtitle,
            "rows": [list(r) for r in rows], "corner_left": corner_left, "corner_right": corner_right,
            "footer": footer, "warning": warning, "logo": str(logo) if logo else "",
            "slate": str(slate), "overlay": str(overlay)}
    with tempfile.TemporaryDirectory(prefix="coreline_slate_") as tmp:
        spec_file = Path(tmp) / "slate.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        result = powershell.run(_SCRIPT.replace("__SPEC__", powershell.quote(spec_file)))
    if "ok" not in result.stdout.split():
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError("Couldn't draw the slate: " + (detail[-1] if detail else "no output from PowerShell"))
