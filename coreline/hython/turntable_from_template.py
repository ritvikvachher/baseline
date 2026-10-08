"""Runs inside Houdini's command-line Python (hython), started by CORELINE for turntables.

    hython turntable_from_template.py <spec.json>

Puts an exported asset into the team's turntable template and writes out the finished scene
for Karma (husk) to render:

  1. writes a small USD "wrapper" around the asset: standing on the ground at the origin,
     spinning once over the turntable's frames;
  2. opens the template with $CORELINE_ASSET pointing at that wrapper;
  3. takes the stage arriving at OUT_CORELINE_turntable and, on a copy of it, moves the
     camera prim turntable_cam nearer or further (keeping its lens and angle) so the
     asset fits, and puts the clay material on the asset for geo turntables;
  4. for lookdev (hdri_spin_frames), holds the asset still afterwards and turns the template's
     dome light(s) once, so the look is seen under the light from every side;
  5. saves that as one USD file.

spec.json keys: template, asset_usd, wrapper_usd, scene_usd, frames, fps, start_angle,
margin, clay (true for geo), clay_material, hdri_spin_frames (0 or missing: no HDRI spin).
The template is never saved.
"""
import json
import math
import sys

import hou
from pxr import Gf, Usd, UsdGeom, UsdShade

from turntable_scene import asset_bounds, bind, emit, preview_material

OUT_NODE = "OUT_CORELINE_turntable"
CAMERA_NAME = "turntable_cam"
ASSET_PRIM = "turntable_asset"


def total_frames(spec):
    return spec["frames"] + int(spec.get("hdri_spin_frames") or 0)


def write_wrapper(spec, low, high):
    """The asset centred on the origin, on the ground, spinning once over the frames
    (then back at its starting angle for an HDRI spin)."""
    stage = Usd.Stage.CreateNew(spec["wrapper_usd"])
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    frames = spec["frames"]
    stage.SetStartTimeCode(1)
    stage.SetEndTimeCode(total_frames(spec))
    stage.SetTimeCodesPerSecond(spec["fps"])
    root = UsdGeom.Xform.Define(stage, "/spin")
    stage.SetDefaultPrim(root.GetPrim())
    rotate = root.AddRotateYOp()
    for frame in range(1, frames + 1):
        rotate.Set(spec.get("start_angle", 0) + 360.0 * (frame - 1) / frames, frame)
    if total_frames(spec) > frames:
        rotate.Set(spec.get("start_angle", 0) + 360.0, frames + 1)  # a full turn: still from here on
    centre = (low + high) / 2
    place = UsdGeom.Xform.Define(stage, "/spin/place")
    place.AddTranslateOp().Set(Gf.Vec3d(-centre[0], -low[1], -centre[2]))
    geo = UsdGeom.Xform.Define(stage, "/spin/place/geo")
    geo.GetPrim().GetReferences().AddReference(spec["asset_usd"])
    stage.GetRootLayer().Save()


def find_out_node():
    for node in hou.node("/").allSubChildren():
        if node.name() == OUT_NODE:
            return node
    return None


def find_prim(stage, name, kind=None):
    for prim in stage.Traverse():
        if prim.GetName() == name and (kind is None or prim.IsA(kind)):
            return prim
    return None


def frame_camera(stage, camera_prim, asset_prim, size, margin):
    """Move the camera along its own view line so the spinning asset fits the frame."""
    camera = UsdGeom.Camera(camera_prim)
    gf_camera = camera.GetCamera(Usd.TimeCode(1))
    aspect = gf_camera.horizontalAperture / gf_camera.verticalAperture if gf_camera.verticalAperture else 16 / 9
    focal = gf_camera.focalLength
    half_h = math.atan((gf_camera.horizontalAperture / 2) / focal)
    half_v = math.atan((gf_camera.verticalAperture / 2) / focal) if gf_camera.verticalAperture else \
        math.atan(math.tan(half_h) / aspect)
    # The asset spins, so what must fit is a cylinder around it; fit the sphere around that.
    reach = math.hypot(size[0] / 2, size[2] / 2)
    radius = math.hypot(reach, size[1] / 2)
    distance = radius * margin / math.sin(min(half_h, half_v))

    cache = UsdGeom.XformCache(Usd.TimeCode(1))
    centre = cache.GetLocalToWorldTransform(asset_prim).Transform(Gf.Vec3d(0, size[1] / 2, 0))
    world = cache.GetLocalToWorldTransform(camera_prim)
    forward = world.TransformDir(Gf.Vec3d(0, 0, -1)).GetNormalized()
    eye = centre - forward * distance
    new_world = Gf.Matrix4d(world)
    new_world.SetTranslateOnly(eye)
    parent = cache.GetParentToWorldTransform(camera_prim)
    xformable = UsdGeom.Xformable(camera_prim)
    xformable.ClearXformOpOrder()
    xformable.AddTransformOp(opSuffix="coreline").Set(new_world * parent.GetInverse())
    near, far = camera.GetClippingRangeAttr().Get() or (0.1, 1000.0)
    camera.CreateClippingRangeAttr(Gf.Vec2f(min(near, 0.01), max(far, distance * 20)))
    return distance


def spin_domes(stage, start, frames):
    """Turns every dome light once about Y over `frames` frames from `start`."""
    domes = [p for p in stage.Traverse() if p.GetTypeName().startswith("DomeLight")]
    for dome in domes:
        rotate = UsdGeom.Xformable(dome).AddRotateYOp(opSuffix="coreline_spin")
        rotate.Set(0.0, 1)
        for frame in range(start, start + frames + 1):
            rotate.Set(360.0 * (frame - start) / frames, frame)
    return len(domes)


def build(spec):
    low, high = asset_bounds(spec["asset_usd"])
    size = high - low
    write_wrapper(spec, low, high)

    hou.putenv("CORELINE_ASSET", spec["wrapper_usd"].replace("\\", "/"))
    try:
        hou.hipFile.load(spec["template"], suppress_save_prompt=True, ignore_load_warnings=True)
    except hou.LoadWarning as e:
        print(f"Warning while loading the template: {e}", flush=True)
    out = find_out_node()
    if out is None:
        raise RuntimeError(f"The template has no node called {OUT_NODE}.")
    # A USD Render node renders what comes into it; any other node is the end of the scene itself.
    source = out.inputs()[0] if out.parm("rendersettings") is not None and out.inputs() else out
    composed = source.stage()
    if composed is None:
        problems = " ".join(source.errors()) or "it doesn't cook"
        raise RuntimeError(f"The template's scene has errors: {problems}")
    settings_path = out.evalParm("rendersettings") if out.parm("rendersettings") is not None else ""

    # Work on a flattened copy, so the edits below don't depend on how the template is built.
    stage = Usd.Stage.Open(composed.Flatten())
    asset = find_prim(stage, ASSET_PRIM)
    if asset is None:
        raise RuntimeError(f"The asset isn't in the template's scene: is the {ASSET_PRIM} reference "
                           "($CORELINE_ASSET) connected to the rest?")
    camera = find_prim(stage, CAMERA_NAME, UsdGeom.Camera)
    if camera is None:
        raise RuntimeError(f"The template has no camera called {CAMERA_NAME}.")
    distance = frame_camera(stage, camera, asset, size, spec.get("margin", 1.1))

    if spec.get("clay"):
        clay = find_prim(stage, spec.get("clay_material", "clay"), UsdShade.Material)
        material = UsdShade.Material(clay) if clay else preview_material(
            stage, "/coreline_looks/clay", (0.5, 0.5, 0.5), 0.55)
        if not clay:
            emit("WARNING", {"message": "The template has no 'clay' material; using a plain grey one."})
        bind(asset, material, stronger=True)

    spin = int(spec.get("hdri_spin_frames") or 0)
    if spin and not spin_domes(stage, spec["frames"] + 1, spin):
        emit("WARNING", {"message": "The template has no dome light, so there's nothing to spin for the HDRI spin."})

    frames = total_frames(spec)
    stage.SetStartTimeCode(1)
    stage.SetEndTimeCode(frames)
    stage.SetTimeCodesPerSecond(spec["fps"])
    stage.SetFramesPerSecond(spec["fps"])
    if settings_path and stage.GetPrimAtPath(settings_path):
        stage.SetMetadata("renderSettingsPrimPath", settings_path)
    stage.Export(spec["scene_usd"])
    emit("SCENE", {
        "size_m": [round(v, 3) for v in size],
        "camera": str(camera.GetPath()),
        "settings": settings_path if settings_path and stage.GetPrimAtPath(settings_path) else "",
        "camera_distance": round(distance, 2),
        "frames": frames,
    })


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        spec = json.load(f)
    try:
        build(spec)
    except Exception as e:
        emit("ERROR", {"message": f"Couldn't put the asset into the turntable template: {e}"})
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
