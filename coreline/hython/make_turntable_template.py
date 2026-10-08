"""Makes the starter turntable template for CORELINE. Run once with hython:

    hython make_turntable_template.py <folder> [hdri file name]

Writes <folder>/turntable_template.hipnc and <folder>/preview_asset.usda. The team then
restyles the template in Houdini however they like, keeping these few things, which are
what CORELINE looks for (they're also written in a sticky note inside the file):

  * a Reference node whose file is $CORELINE_ASSET   - CORELINE puts the asset (already
    spinning, standing on the ground at the origin) there
  * a camera prim called turntable_cam                - CORELINE keeps its lens and angle but
    moves it closer or further so each asset fits. Things under it (the grey and chrome
    balls) move with it, so they stay in the corner of the frame
  * a render node called OUT_CORELINE_turntable       - CORELINE renders what reaches it,
    with its own frame range, samples and resolution
  * a material called clay                            - used on the asset for geo turntables
"""
import os
import sys

import hou
from pxr import Gf, Sdf, Usd, UsdGeom

NOTE = """CORELINE TURNTABLE TEMPLATE

Restyle this however you like. CORELINE needs just these:
 - CORELINE_ASSET: a Reference whose file is $CORELINE_ASSET
   (with nothing set it shows preview_asset.usda so you can see the look)
 - a camera prim called turntable_cam: CORELINE keeps its lens and angle,
   and moves it nearer/further so each asset fits. Anything under it
   (the grey + chrome balls) stays in the same place in the frame.
 - a render node called OUT_CORELINE_turntable at the end
 - a material called clay (for geo turntables)

The HDRI is $HIP/__HDRI__. Frames, samples and resolution come from
CORELINE's Turntable dialog. Save as turntable_template.hipnc in this folder."""

# Balls: half a metre in front of the camera, so even small props never hide them,
# placed in the lower-right corner of a 50 mm lens on a 16:9 frame.
BALL_DISTANCE = 0.4
BALL_RADIUS = 0.016


def preview_asset(path):
    """A 1 m tall stand-in so the template shows something when opened by hand."""
    stage = Usd.Stage.CreateNew(path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/preview")
    stage.SetDefaultPrim(root.GetPrim())
    base = UsdGeom.Cylinder.Define(stage, "/preview/base")
    base.CreateRadiusAttr(0.3)
    base.CreateHeightAttr(0.5)
    base.CreateExtentAttr([Gf.Vec3f(-0.3, -0.25, -0.3), Gf.Vec3f(0.3, 0.25, 0.3)])
    UsdGeom.XformCommonAPI(base).SetTranslate(Gf.Vec3d(0, 0.25, 0))
    ball = UsdGeom.Sphere.Define(stage, "/preview/ball")
    ball.CreateRadiusAttr(0.25)
    ball.CreateExtentAttr([Gf.Vec3f(-0.25, -0.25, -0.25), Gf.Vec3f(0.25, 0.25, 0.25)])
    UsdGeom.XformCommonAPI(ball).SetTranslate(Gf.Vec3d(0, 0.75, 0))
    stage.GetRootLayer().Save()


def material(library, name, base_color, metalness=0.0, specular=0.5, roughness=0.5):
    shader = library.createNode("mtlxstandard_surface", name)
    shader.parmTuple("base_color").set(base_color)
    shader.parm("base").set(1.0)
    shader.parm("metalness").set(metalness)
    shader.parm("specular").set(specular)
    shader.parm("specular_roughness").set(roughness)
    return shader


def build(folder, hdri_name):
    hou.hipFile.clear(suppress_save_prompt=True)
    target = os.path.join(folder, "turntable_template.hipnc")
    hou.hipFile.setName(target)  # so $HIP is this folder while building
    hou.playbar.setFrameRange(1, 120)
    hou.setFps(24)
    preview_asset(os.path.join(folder, "preview_asset.usda"))
    stage = hou.node("/stage")
    chain = []

    def add(node_type, name, **parms):
        node = stage.createNode(node_type, name)
        for parm, value in parms.items():
            target = node.parmTuple(parm) if isinstance(value, tuple) else node.parm(parm)
            target.set(value)
        if chain:
            node.setInput(0, chain[-1])
        chain.append(node)
        return node

    asset = add("reference::2.0", "CORELINE_ASSET", num_files=1, primpath1="/turntable_asset")
    # CORELINE sets $CORELINE_ASSET when it renders; opened by hand, the preview stand-in shows.
    asset.parm("filepath1").setExpression(
        'hou.getenv("CORELINE_ASSET") or hou.text.expandString("$HIP/preview_asset.usda")',
        hou.exprLanguage.Python)
    add("cube", "ground", primpath="/studio/ground", t=(0, -0.01, 0), s=(40, 0.02, 40))
    # Karma's "Background" holdout: the ground shows the HDRI behind it but still catches shadows.
    add("rendergeometrysettings", "ground_shadow_catcher", primpattern="/studio/ground",
        xn__primvarskarmaobjectholdoutmode_control_02bfg="set",
        xn__primvarskarmaobjectholdoutmode_zpbfg="Background")
    add("domelight::3.0", "hdri", primpath="/studio/hdri",
        xn__inputstexturefile_control_shbh="set", xn__inputstexturefile_r3ah="$HIP/" + hdri_name,
        xn__inputskarmalightrenderlightgeo_control_y2bff="set", xn__inputskarmalightrenderlightgeo_xpbff=1)
    add("camera", "turntable_cam", primpath="/cameras/turntable_cam", t=(0, 1.0, 6.0), r=(-8, 0, 0),
        focalLength=50, horizontalAperture=36)
    for name, x in (("grey_ball", 0.118), ("chrome_ball", 0.078)):
        add("sphere", name, primpath=f"/cameras/turntable_cam/balls/{name}",
            t=(x, -0.052, -BALL_DISTANCE), radius=BALL_RADIUS)
    library = add("materiallibrary", "materials", matpathprefix="/materials/")
    material(library, "clay", (0.5, 0.5, 0.5), specular=0.4, roughness=0.55)
    material(library, "grey_ball", (0.18, 0.18, 0.18), specular=0.0, roughness=1.0)
    material(library, "chrome_ball", (1.0, 1.0, 1.0), metalness=1.0, specular=1.0, roughness=0.0)
    library.layoutChildren()
    assign = add("assignmaterial", "assign_ball_materials", nummaterials=2)
    for i, name in enumerate(("grey_ball", "chrome_ball"), 1):
        assign.parm(f"primpattern{i}").set(f"/cameras/turntable_cam/balls/{name}")
        assign.parm(f"matspecpath{i}").set(f"/materials/{name}")
    add("karmarendersettings", "render_settings", engine="xpu", camera="/cameras/turntable_cam",
        samplesperpixel=64)  # resolution: CORELINE sets it when rendering
    out = add("usdrender_rop", "OUT_CORELINE_turntable")
    out.inputs()[0].setDisplayFlag(True)  # the render node itself is a ROP, which has no display flag
    stage.layoutChildren()
    note = stage.createStickyNote("coreline_rules")
    note.setText(NOTE.replace("__HDRI__", hdri_name))
    note.setSize(hou.Vector2(6.5, 4.2))
    note.setPosition(chain[0].position() + hou.Vector2(4, 0))

    # Check it composes: the asset, camera, balls and materials are where CORELINE expects.
    composed = out.inputs()[0].stage()
    for node in chain:
        for problem in node.errors() + node.warnings():
            print(f"{node.name()}: {problem}", flush=True)
    if composed is None:
        raise RuntimeError("The template network doesn't cook; see the messages above.")
    for path in ("/turntable_asset", "/cameras/turntable_cam", "/cameras/turntable_cam/balls/grey_ball",
                 "/materials/clay", "/studio/hdri", "/Render/rendersettings"):
        if not composed.GetPrimAtPath(path):
            print(f"MISSING {path}", flush=True)
    hou.hipFile.save(target)
    print(f"Saved {target}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    build(os.path.abspath(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "studio_small_08_4k.exr")
