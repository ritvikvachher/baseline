"""Runs inside Maya's command-line Python (mayapy), started by CORELINE for turntables.

    mayapy export_geo.py <scene.ma|.mb> <out.usd> <stats.json>

Exports the meshes in the OUT_CORELINE_geo set (or, without that set, every visible mesh)
to USD in metres, and writes a few numbers for the turntable slate. The scene is never saved.
Lines starting with CORELINE:: are messages for the pipeline tool.
"""
import json
import sys
import time

MARK = "CORELINE::"
SET_NAME = "OUT_CORELINE_geo"


def emit(kind, data=None):
    print(f"{MARK}{kind} {json.dumps(data or {})}", flush=True)


def note(text):
    emit("NOTE", {"message": text})


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    scene, out_usd, stats_file = argv
    began = time.time()
    note("Starting Maya in the background...")
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds

    note("Opening the scene...")
    try:
        cmds.file(scene, open=True, force=True, ignoreVersion=True, prompt=False)
    except RuntimeError as e:
        # Missing references or plugins raise but usually still open the scene.
        print(f"Warning while opening the scene: {e}", flush=True)

    sets = [s for s in cmds.ls(SET_NAME, type="objectSet") or [] if cmds.sets(s, q=True)]
    # A group (transform) with that name works too: everything under it is the turntable geometry.
    groups = [] if sets else cmds.ls(SET_NAME, type="transform", long=True) or []
    if sets or groups:
        roots = cmds.ls(cmds.sets(sets[0], q=True), long=True) if sets else groups[:1]
        meshes = cmds.listRelatives(roots, allDescendents=True, type="mesh", fullPath=True) or []
        meshes += cmds.ls(roots, type="mesh", long=True)
        meshes = [m for m in cmds.ls(meshes, noIntermediate=True, long=True) if cmds.ls(m, visible=True)]
    else:
        meshes = [m for m in cmds.ls(type="mesh", noIntermediate=True, long=True) if cmds.ls(m, visible=True)]
        roots = sorted({"|" + m.split("|")[1] for m in meshes})
        emit("WARNING", {"message": f"No '{SET_NAME}' set or group in this scene, so every visible mesh was "
                                    f"used. Make a set (or group) called {SET_NAME} with just the turntable "
                                    "geometry (e.g. only the high-res group) to leave out the rest."})
    if not meshes:
        emit("ERROR", {"message": f"Found no visible meshes to export"
                                  f"{' in ' + SET_NAME if sets or groups else ''}."})
        return 1

    def has_uvs(mesh):
        count = cmds.polyEvaluate(mesh, uvcoord=True)  # a string like "Nothing counted" if none
        return isinstance(count, int) and count > 0

    with_uvs = sum(1 for m in meshes if has_uvs(m))
    box = cmds.exactWorldBoundingBox(meshes)
    to_metres = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0, "in": 0.0254, "ft": 0.3048,
                 "yd": 0.9144, "mi": 1609.344}.get(cmds.currentUnit(q=True, linear=True), 0.01)
    stats = {
        "meshes": len(meshes),
        "faces": int(cmds.polyEvaluate(meshes, face=True)),
        "triangles": int(cmds.polyEvaluate(meshes, triangle=True)),
        "meshes_with_uvs": with_uvs,
        "size_m": [round((box[3] - box[0]) * to_metres, 3), round((box[4] - box[1]) * to_metres, 3),
                   round((box[5] - box[2]) * to_metres, 3)],
        "used_set": bool(sets or groups),
        "maya": cmds.about(version=True),
    }
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    note(f"{stats['triangles']:,} triangles in {len(meshes)} meshes; exporting to USD...")

    cmds.loadPlugin("mayaUsdPlugin", quiet=True)
    cmds.select(roots, replace=True)
    cmds.mayaUSDExport(file=out_usd, selection=True, exportUVs=True, exportVisibility=True,
                       exportDisplayColor=False, shadingMode="none", defaultMeshScheme="none",
                       stripNamespaces=True, metersPerUnit=1.0, exportDistanceUnit=True)
    emit("EXPORTED", {"seconds": round(time.time() - began, 1), "stats": stats})
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    # Maya's own shutdown can take a while and sometimes crashes; the work is done. Even os._exit
    # lets every plugin shut down on Windows, and V-Ray's shutdown can hang forever ("hdVRay AppSDK
    # instance still alive at end of program"), so end the process outright.
    import os
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    except (AttributeError, OSError):
        pass
    os._exit(code)
