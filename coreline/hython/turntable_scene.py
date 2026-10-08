"""Runs inside Houdini's command-line Python (hython), started by CORELINE for turntables.

    hython turntable_scene.py <spec.json>

Builds a small USD scene around an exported asset: a grey studio backdrop (cyclorama), the
team's HDRI, a camera framed from the asset's size, the asset spinning once, and (if it's
a sensible size) a 1.8 m figure for scale. Karma (husk) then renders that scene.

spec.json keys: asset_usd, scene_usd, hdri, frames, fps, resolution [w, h], focal_mm,
elevation_deg, start_angle, margin, clay (true for the grey model look), scale_figure,
clay_color, backdrop_color, figure_color. Lines starting with CORELINE:: are messages.
"""
import json
import math
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdRender, UsdShade

MARK = "CORELINE::"
FIGURE_HEIGHT = 1.8
FIGURE_GAP = 0.35       # metres between the asset's spin circle and the figure
FIGURE_MIN, FIGURE_MAX = 0.3, 8.0  # only show the figure for assets this size (largest side, m)
APERTURE_MM = 36.0


def emit(kind, data=None):
    print(f"{MARK}{kind} {json.dumps(data or {})}", flush=True)


def asset_bounds(path):
    stage = Usd.Stage.Open(path)
    if UsdGeom.GetStageUpAxis(stage) != UsdGeom.Tokens.y:
        emit("WARNING", {"message": "The asset is Z-up; the turntable expects Y-up."})
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    box = cache.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
    if box.IsEmpty():
        raise RuntimeError("The exported asset has no geometry.")
    return box.GetMin(), box.GetMax()


def preview_material(stage, path, color, roughness):
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, f"{path}/surface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.04, 0.04, 0.04))
    shader.CreateInput("useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(1)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def bind(prim, material, stronger=False):
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(
        material, UsdShade.Tokens.strongerThanDescendants if stronger else UsdShade.Tokens.weakerThanDescendants)


def cyclorama(stage, path, width, depth_front, wall_z, height, radius):
    """A studio backdrop: a floor that curves up into a wall behind the asset (wall at -Z)."""
    profile = [(depth_front, 0.0)]           # (z, y) points from the front of the floor...
    steps = 16
    curve_start = wall_z + radius
    for i in range(steps + 1):                # ...round the curve...
        a = (math.pi / 2) * i / steps
        profile.append((curve_start - math.sin(a) * radius, radius - math.cos(a) * radius))
    profile.append((wall_z, height))          # ...and up the wall.
    points, counts, indices = [], [], []
    for x in (-width / 2, width / 2):
        points += [Gf.Vec3f(x, y, z) for z, y in profile]
    n = len(profile)
    for i in range(n - 1):
        counts.append(4)
        indices += [i, i + 1, n + i + 1, n + i]
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(indices)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDoubleSidedAttr(True)
    return mesh


def _capsule(stage, path, radius, bottom, top, x=0.0):
    """A Y-up capsule from height `bottom` to `top` (rounded ends included)."""
    capsule = UsdGeom.Capsule.Define(stage, path)
    capsule.CreateAxisAttr(UsdGeom.Tokens.y)
    capsule.CreateRadiusAttr(radius)
    half = (top - bottom) / 2
    capsule.CreateHeightAttr(max(0.0, 2 * (half - radius)))
    capsule.CreateExtentAttr([Gf.Vec3f(-radius, -half, -radius), Gf.Vec3f(radius, half, radius)])
    UsdGeom.XformCommonAPI(capsule).SetTranslate(Gf.Vec3d(x, bottom + half, 0))


def figure(stage, path, x):
    """A simple 1.8 m standing mannequin for scale: two legs, two arms, a body and a head."""
    root = UsdGeom.Xform.Define(stage, path)
    UsdGeom.XformCommonAPI(root).SetTranslate(Gf.Vec3d(x, 0, 0))
    for side, offset in (("l", -0.09), ("r", 0.09)):
        _capsule(stage, f"{path}/leg_{side}", 0.075, 0.0, 0.9, offset)
        _capsule(stage, f"{path}/arm_{side}", 0.055, 0.78, 1.47, offset * 2.55)
    _capsule(stage, f"{path}/body", 0.17, 0.82, 1.5)
    _capsule(stage, f"{path}/neck", 0.055, 1.42, 1.6)
    head = UsdGeom.Sphere.Define(stage, f"{path}/head")
    head.CreateRadiusAttr(0.11)
    head.CreateExtentAttr([Gf.Vec3f(-0.11, -0.11, -0.11), Gf.Vec3f(0.11, 0.11, 0.11)])
    UsdGeom.XformCommonAPI(head).SetTranslate(Gf.Vec3d(0, FIGURE_HEIGHT - 0.11, 0))
    return root


def build(spec):
    low, high = asset_bounds(spec["asset_usd"])
    size = high - low
    centre = (low + high) / 2
    # How far the asset reaches from the spin axis (through its centre), as it turns.
    reach = math.hypot(size[0] / 2, size[2] / 2)
    height = size[1]
    largest = max(size[0], size[1], size[2])
    show_figure = spec.get("scale_figure", True) and FIGURE_MIN <= largest <= FIGURE_MAX

    # Framing: what must stay in shot is the spin cylinder (and the figure next to it).
    left, right = -reach, reach
    top = height
    figure_x = None
    if show_figure:
        figure_x = reach + FIGURE_GAP + 0.2
        right = figure_x + 0.3
        top = max(top, FIGURE_HEIGHT)
    width_px, height_px = spec["resolution"]
    focal = spec["focal_mm"]
    half_h = math.atan((APERTURE_MM / 2) / focal)
    half_v = math.atan((APERTURE_MM * height_px / width_px / 2) / focal)
    margin = spec.get("margin", 1.15)
    distance = max(((right - left) / 2 * margin) / math.tan(half_h),
                   (top / 2 * margin) / math.tan(half_v)) + reach
    look = Gf.Vec3d((left + right) / 2, top / 2, 0)
    elevation = math.radians(spec["elevation_deg"])
    eye = look + Gf.Vec3d(0, math.sin(elevation) * distance, math.cos(elevation) * distance)

    stage = Usd.Stage.CreateNew(spec["scene_usd"])
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    frames, fps = spec["frames"], spec["fps"]
    stage.SetStartTimeCode(1)
    stage.SetEndTimeCode(frames)
    stage.SetTimeCodesPerSecond(fps)
    stage.SetFramesPerSecond(fps)
    world = UsdGeom.Xform.Define(stage, "/turntable")
    stage.SetDefaultPrim(world.GetPrim())

    # The asset: spun about its own centre, standing on the floor.
    spin = UsdGeom.Xform.Define(stage, "/turntable/spin")
    rotate = spin.AddRotateYOp()
    for frame in range(1, frames + 1):
        rotate.Set(spec.get("start_angle", 0) + 360.0 * (frame - 1) / frames, frame)
    place = UsdGeom.Xform.Define(stage, "/turntable/spin/asset")
    place.AddTranslateOp().Set(Gf.Vec3d(-centre[0], -low[1], -centre[2]))
    # The reference goes on its own prim: on `place` it would lose the asset's own root
    # transform (e.g. the unit scale Maya writes), because `place` sets its own transform.
    geo = UsdGeom.Xform.Define(stage, "/turntable/spin/asset/geo")
    geo.GetPrim().GetReferences().AddReference(spec["asset_usd"])

    looks = "/turntable/looks"
    UsdGeom.Scope.Define(stage, looks)
    if spec.get("clay", True):
        bind(spin.GetPrim(), preview_material(stage, f"{looks}/clay", spec["clay_color"], 0.55), stronger=True)

    # Studio: backdrop, HDRI, scale figure.
    span = distance + reach
    backdrop = cyclorama(stage, "/turntable/backdrop", width=span * 8, depth_front=distance * 2,
                         wall_z=-(reach + max(1.0, reach) * 2.5), height=span * 4,
                         radius=max(1.0, reach) * 1.5)
    bind(backdrop.GetPrim(), preview_material(stage, f"{looks}/backdrop", spec["backdrop_color"], 0.9))
    dome = UsdLux.DomeLight.Define(stage, "/turntable/hdri")
    dome.CreateTextureFileAttr(Sdf.AssetPath(spec["hdri"]))
    dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
    dome.CreateIntensityAttr(spec.get("hdri_intensity", 1.0))
    if spec.get("hdri_angle"):
        UsdGeom.XformCommonAPI(dome).SetRotate(Gf.Vec3f(0, spec["hdri_angle"], 0))
    if show_figure:
        person = figure(stage, "/turntable/scale_figure", figure_x)
        bind(person.GetPrim(), preview_material(stage, f"{looks}/figure", spec["figure_color"], 0.8),
             stronger=True)

    # Camera, aimed at the middle of what's in shot.
    camera = UsdGeom.Camera.Define(stage, "/turntable/camera")
    camera.CreateFocalLengthAttr(focal)
    camera.CreateHorizontalApertureAttr(APERTURE_MM)
    camera.CreateVerticalApertureAttr(APERTURE_MM * height_px / width_px)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.01, span * 20))
    view = Gf.Matrix4d().SetLookAt(eye, look, Gf.Vec3d(0, 1, 0)).GetInverse()
    camera.AddTransformOp().Set(view)

    settings = UsdRender.Settings.Define(stage, "/Render/settings")
    settings.CreateCameraRel().SetTargets([camera.GetPath()])
    settings.CreateResolutionAttr(Gf.Vec2i(width_px, height_px))
    stage.SetMetadata("renderSettingsPrimPath", "/Render/settings")
    stage.GetRootLayer().Save()

    emit("SCENE", {
        "size_m": [round(v, 3) for v in size],
        "figure": show_figure,
        "camera_distance": round(distance, 2),
    })


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        spec = json.load(f)
    try:
        build(spec)
    except Exception as e:  # report it to CORELINE rather than as a bare traceback
        emit("ERROR", {"message": f"Couldn't build the turntable scene: {e}"})
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
