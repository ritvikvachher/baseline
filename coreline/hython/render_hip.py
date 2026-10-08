"""Runs inside Houdini's command-line Python (hython), started by CORELINE.

    hython render_hip.py list   <scene.hip>
    hython render_hip.py render <scene.hip> <node path> <start> <end> <step> [--res 50] [--skip-existing]
                                [--output X:/shots/SH010/render/beauty/name/name.$F4.exr]

Lines starting with CORELINE:: are messages for the pipeline tool; everything
else is Houdini's normal output and goes into the render log. Changes made to
the scene here (e.g. resolution) are never saved.
"""
import json
import os
import sys
import time

import hou

MARK = "CORELINE::"
OUTPUT_PARMS = ("picture", "vm_picture", "outputimage", "sopoutput", "lopoutput", "filename",
                "file", "RS_outputFileNamePrefix", "ar_picture")
# Where Karma nodes (/out) and USD Render nodes (/stage) take their output image.
IMAGE_PARMS = ("picture", "outputimage")


def emit(kind, data=None):
    print(f"{MARK}{kind} {json.dumps(data or {})}", flush=True)


def load(hip):
    try:
        hou.hipFile.load(hip, suppress_save_prompt=True, ignore_load_warnings=True)
    except hou.LoadWarning as e:
        print(f"Warning while loading scene: {e}", flush=True)


def is_render_node(node):
    if isinstance(node, hou.RopNode):
        return True
    # Solaris render nodes (e.g. USD Render ROP in /stage) are LOPs with a Render button.
    return node.type().category().name() == "Lop" and node.parm("execute") is not None \
        and node.parm("trange") is not None


def is_user_facing(node):
    """Skip nodes that live inside another render node (e.g. the ROP inside a Karma node)."""
    parent = node.parent()
    while parent is not None:
        if is_render_node(parent):
            return False
        parent = parent.parent()
    return not node.isInsideLockedHDA()


def frame_range(node):
    start, end = hou.playbar.frameRange()
    step = 1
    parms = [node.parm(name) for name in ("trange", "f1", "f2", "f3")]
    if all(parms) and parms[0].eval() != 0:
        start, end, step = parms[1].eval(), parms[2].eval(), parms[3].eval()
    elif parms[0] is not None and parms[0].eval() == 0:
        start = end = hou.frame()
    return int(round(start)), int(round(end)), max(1, int(round(step)))


def output_path(node, frame):
    for name in OUTPUT_PARMS:
        parm = node.parm(name)
        if parm is not None:
            try:
                value = parm.evalAtFrame(frame)
            except hou.Error:
                continue
            if isinstance(value, str) and value:
                return value
    return ""


def list_nodes(hip):
    load(hip)
    nodes = []
    for node in hou.node("/").allSubChildren(recurse_in_locked_nodes=False):
        if not is_render_node(node) or not is_user_facing(node):
            continue
        if hasattr(node, "isBypassed") and node.isBypassed():
            continue
        start, end, step = frame_range(node)
        nodes.append({
            "path": node.path(),
            "type": node.type().description(),
            "start": start, "end": end, "step": step,
            "output": output_path(node, start),
        })
    emit("NODES", {"nodes": nodes})


def _set(node, name, value):
    parm = node.parm(name)
    if parm is None:
        return False
    try:
        parm.set(value)
        return True
    except hou.Error as e:
        print(f"Could not set {node.path()}/{name}: {e}", flush=True)
        return False


def apply_karma_options(node, res_percent):
    """Resolution override and per-frame progress for Karma / USD Render nodes."""
    # Karma prints "ALF_PROGRESS 42%" while rendering, which CORELINE shows per frame.
    _set(node, "alfprogress", 1)
    if res_percent == 100:
        return
    if node.parm("override_res") is not None:
        # USD Render ROP (/stage)
        ok = _set(node, "override_res", "scale") and _set(node, "res_scale", res_percent)
    elif node.parm("resolutionx") is not None and node.parm("resolutiony") is not None:
        # Karma ROP (/out): it renders at its own Resolution setting, so scale that.
        width, height = node.evalParm("resolutionx"), node.evalParm("resolutiony")
        ok = _set(node, "resolutionx", max(1, round(width * res_percent / 100))) and \
            _set(node, "resolutiony", max(1, round(height * res_percent / 100)))
    else:
        ok = False
    if ok:
        print(f"Rendering at {res_percent}% resolution.", flush=True)
    else:
        emit("WARNING", {"message": "This node can't change resolution; rendering at full size."})


def set_output(node, output):
    """Send the images to `output` (a path with $F4) instead of the node's own path."""
    for name in IMAGE_PARMS:
        parm = node.parm(name)
        if parm is not None:
            try:
                parm.deleteAllKeyframes()
            except hou.Error:
                pass
            if _set(node, name, output):
                folder = os.path.dirname(parm.evalAtFrame(hou.frame()))
                if folder:
                    os.makedirs(folder, exist_ok=True)
                print(f"Writing images to {output}", flush=True)
                return
    emit("WARNING", {"message": "Couldn't set this node's output path; it writes where the scene says."})


def render(hip, node_path, start, end, step, res_percent=100, skip_existing=False, output=""):
    load(hip)
    node = hou.node(node_path)
    if node is None:
        emit("ERROR", {"message": f"Node {node_path} was not found in the scene."})
        return 2
    apply_karma_options(node, res_percent)
    if output:
        set_output(node, output)
    frames = list(range(start, end + 1, step))
    emit("START", {"total": len(frames)})
    for index, frame in enumerate(frames, 1):
        if skip_existing:
            output = output_path(node, frame)
            if output and os.path.isfile(output):
                emit("FRAME_SKIPPED", {"frame": frame, "index": index, "total": len(frames)})
                continue
        emit("FRAME", {"frame": frame, "index": index, "total": len(frames)})
        began = time.time()
        try:
            if isinstance(node, hou.RopNode):
                node.render(frame_range=(frame, frame, 1), verbose=True, output_progress=True)
            else:
                node.parm("trange").set(1)
                node.parmTuple("f").deleteAllKeyframes()
                node.parmTuple("f").set((frame, frame, 1))
                node.parm("execute").pressButton()
        except hou.Error as e:
            emit("ERROR", {"message": f"Frame {frame} failed: {e}"})
            return 1
        errors = node.errors()
        if errors:
            emit("ERROR", {"message": f"Frame {frame} failed: " + " ".join(errors)})
            return 1
        emit("FRAME_DONE", {"frame": frame, "index": index, "total": len(frames),
                            "seconds": round(time.time() - began, 1)})
    emit("FINISHED")
    return 0


def main(argv):
    if len(argv) >= 2 and argv[0] == "list":
        list_nodes(argv[1])
        return 0
    if len(argv) >= 6 and argv[0] == "render":
        options = argv[6:]
        res = int(options[options.index("--res") + 1]) if "--res" in options else 100
        out = options[options.index("--output") + 1] if "--output" in options else ""
        return render(argv[1], argv[2], int(argv[3]), int(argv[4]), int(argv[5]),
                      res_percent=res, skip_existing="--skip-existing" in options, output=out)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
