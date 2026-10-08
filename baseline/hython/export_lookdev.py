"""Runs inside Houdini's command-line Python (hython), started by BASELINE for lookdev turntables.

    hython export_lookdev.py <scene.hip> <out.usd> <stats.json>

Opens the lookdev scene and saves the stage arriving at the node called OUT_BASELINE_lookdev
(any LOP, e.g. the scene's Karma Render Settings; for a USD Render node, what comes into it)
as one USD file holding just the shaded asset: geometry and materials, all under /asset.
The scene's own cameras, lights and render settings are left out, since the turntable
template brings its own. The scene file is never saved.
"""
import json
import sys
import time

import hou
from pxr import Sdf, Usd, UsdGeom, UsdLux, UsdRender

OUT_NODE = "OUT_BASELINE_lookdev"
MARK = "BASELINE::"


def emit(kind, data=None):
    print(f"{MARK}{kind} {json.dumps(data or {})}", flush=True)


def note(text):
    print(text, flush=True)


def find_out_node():
    for node in hou.node("/").allSubChildren():
        if node.name() == OUT_NODE and isinstance(node, hou.LopNode):
            return node
    return None


def left_out(prim):
    """Things the template provides itself, or that aren't part of the asset."""
    if prim.IsA(UsdGeom.Camera) or prim.IsA(UsdRender.SettingsBase) or prim.IsA(UsdRender.Var):
        return True
    if prim.HasAPI(UsdLux.LightAPI) or prim.GetTypeName().startswith(("DomeLight", "LightFilter")):
        return True
    return prim.GetTypeName() == "HoudiniLayerInfo" or prim.GetName() == "Render"


def keep_paths(stage):
    """Top-level prims to copy, and the ones inside them to remove."""
    tops, remove = [], []
    for prim in stage.GetPseudoRoot().GetChildren():
        if left_out(prim):
            continue
        children = prim.GetChildren()
        if children and all(left_out(c) for c in children):
            continue  # e.g. /cameras or /lights, holding nothing else
        inside = [p for p in Usd.PrimRange(prim) if p != prim and left_out(p)]
        tops.append(prim.GetPath())
        remove += [p.GetPath() for p in inside]
    return tops, remove


def remap(path, moves):
    for old, new in moves:
        if path.HasPrefix(old):
            return path.ReplacePrefix(old, new)
    return path


def regroup(flat_layer, tops, remove):
    """Copies the top-level prims under /asset, keeping material bindings and shader
    connections pointing at the copies."""
    layer = Sdf.Layer.CreateAnonymous(".usda")
    root = Sdf.CreatePrimInLayer(layer, "/asset")
    root.specifier = Sdf.SpecifierDef
    root.typeName = "Xform"
    layer.defaultPrim = "asset"
    moves = [(top, Sdf.Path("/asset").AppendChild(top.name)) for top in tops]
    for old, new in moves:
        Sdf.CopySpec(flat_layer, old, layer, new)
    for path in remove:
        moved = remap(path, moves)
        spec = layer.GetPrimAtPath(moved)
        if spec:
            parent = spec.nameParent or layer.pseudoRoot
            del parent.nameChildren[spec.name]

    def fix(spec_path):
        spec = layer.GetObjectAtPath(spec_path)
        if isinstance(spec, Sdf.RelationshipSpec):
            items = list(spec.targetPathList.explicitItems or spec.targetPathList.prependedItems or [])
            if items:
                fixed = [remap(p, moves) for p in items]
                if spec.targetPathList.isExplicit:
                    spec.targetPathList.explicitItems = fixed
                else:
                    spec.targetPathList.prependedItems = fixed
        elif isinstance(spec, Sdf.AttributeSpec):
            items = list(spec.connectionPathList.explicitItems or spec.connectionPathList.prependedItems or [])
            if items:
                fixed = [remap(p, moves) for p in items]
                if spec.connectionPathList.isExplicit:
                    spec.connectionPathList.explicitItems = fixed
                else:
                    spec.connectionPathList.prependedItems = fixed

    layer.Traverse(Sdf.Path("/asset"), fix)
    return layer


def stats_for(stage):
    meshes = [UsdGeom.Mesh(p) for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)
              and UsdGeom.Imageable(p).ComputeVisibility() != UsdGeom.Tokens.invisible]
    triangles = faces = with_uvs = 0
    for mesh in meshes:
        counts = mesh.GetFaceVertexCountsAttr().Get() or []
        faces += len(counts)
        triangles += sum(max(0, c - 2) for c in counts)
        primvars = UsdGeom.PrimvarsAPI(mesh)
        if any(primvars.HasPrimvar(name) for name in ("st", "uv", "st0", "map1")):
            with_uvs += 1
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    box = cache.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
    size = [0.0, 0.0, 0.0] if box.IsEmpty() else [round(v, 3) for v in box.GetSize()]
    return {"meshes": len(meshes), "faces": faces, "triangles": triangles, "meshes_with_uvs": with_uvs,
            "size_m": size, "used_set": True, "houdini": hou.applicationVersionString()}


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    scene, out_usd, stats_file = argv
    began = time.time()
    note(f"Opening {scene}...")
    try:
        hou.hipFile.load(scene, suppress_save_prompt=True, ignore_load_warnings=True)
    except hou.LoadWarning as e:
        note(f"Warning while opening the scene: {e}")

    out = find_out_node()
    if out is None:
        emit("ERROR", {"message": f"There's no LOP node called {OUT_NODE} in this scene. Name the node "
                                  "at the end of the lookdev (e.g. the Karma Render Settings) "
                                  f"{OUT_NODE}, save, and try again."})
        return 1
    # A USD Render node renders what comes into it; any other node is the end of the scene itself.
    source = out.inputs()[0] if out.type().name().startswith("usdrender_rop") and out.inputs() else out
    note(f"Using the stage at {source.path()}...")
    composed = source.stage()
    if composed is None:
        problems = " ".join(source.errors()) or "it doesn't cook"
        emit("ERROR", {"message": f"{OUT_NODE} has errors: {problems}"})
        return 1

    flat = composed.Flatten()
    flat_stage = Usd.Stage.Open(flat)
    tops, remove = keep_paths(flat_stage)
    if not tops:
        emit("ERROR", {"message": f"The stage at {OUT_NODE} has no geometry or materials."})
        return 1
    layer = regroup(flat, tops, remove)
    layer.SetPermissionToEdit(True)
    asset = Usd.Stage.Open(layer)
    UsdGeom.SetStageUpAxis(asset, UsdGeom.GetStageUpAxis(flat_stage))
    UsdGeom.SetStageMetersPerUnit(asset, UsdGeom.GetStageMetersPerUnit(flat_stage))
    stats = stats_for(asset)
    if not stats["meshes"]:
        emit("ERROR", {"message": f"The stage at {OUT_NODE} has no visible meshes."})
        return 1
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    note(f"{stats['triangles']:,} triangles in {stats['meshes']} meshes; kept "
         + ", ".join(str(t) for t in tops) + f"; left out {len(remove)} camera/light/render prim(s) inside them.")
    asset.Export(out_usd)
    emit("EXPORTED", {"seconds": round(time.time() - began, 1), "stats": stats})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
