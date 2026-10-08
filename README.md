# CORELINE

The pipeline tool for our CGI short film. It keeps everyone's scene files
named, versioned and in the right place, launches the right software version,
and renders Houdini scenes from the command line.

## Starting it

Double-click **`run_coreline.bat`**.

It uses the computer's Python if there is one. If not (e.g. on university PCs),
it uses the Python that comes with Houdini, so nothing needs installing.

The first time on each computer it asks for your **name** (used in file names,
since uni logins are student IDs like `s5707305`) and the **project folder**:
the shared OneDrive folder with the film in it, e.g.
`C:\Users\s5707305\OneDrive - Bournemouth University\FMP_FILM`.
It's saved relative to OneDrive, so the same setting works for each of you. If that folder isn't a CORELINE project yet,
it offers to set one up. That only adds `coreline_project.json` (plus `assets/` and
`shots/` if they're missing); existing folders and files are never moved or renamed.

## Setting up on OneDrive (do once)

1. Put the project folder in the shared OneDrive, e.g. `OneDrive/FMP_FILM/`.
2. Put this tool inside it too, e.g. `OneDrive/FMP_FILM/_pipeline/CORELINE/`.
   Every computer that syncs OneDrive then has the tool, always up to date,
   and finds the project automatically without asking.
3. Tip: in OneDrive, right-click the project folder and choose
   *Always keep on this device*, so Houdini/Maya never wait for files to download.

### Why it shows the project as drive X:

Everyone's OneDrive is at a different path (`C:/Users/<name>/OneDrive - ...`),
so a file path saved inside a Houdini or Maya scene would break on anyone else's
computer. CORELINE shows the project folder as **drive X:** on every computer, so
all paths look the same: `X:/assets/characters/cheetah/...`.

- In Houdini, `$JOB` is set to `X:`. Use `$JOB/...` or `$HIP/...` in paths.
- Start Houdini/Maya **from CORELINE**, so X: exists and `$JOB` is set.
- The letter is set in `coreline_project.json` (`"drive_letter"`). If X: is already
  in use on a computer, CORELINE says so and uses the full path instead. It can
  be turned off per person in *Settings...*.

## The pages

**Dashboard**: how far along the film is. The big gauge is the whole film; the
small ones are each department, worked out from task statuses. Below: *My tasks*
(tasks assigned to you; *View all* shows everything in progress), *Recent
publishes*, and the *Shot tracker* (every shot x every shot department, coloured
by status). Click any task, publish or tracker cell to jump to it. The delivery
date is set in Settings.

**Assets & Shots**: pick a folder on the left (asset type or sequence), filter
with the chips (a department, *In review*, *Mine*), and click a card. The panel
on the right shows its **pipeline steps**: click a status to change it, and the
initials to assign someone. Under that are the step's work files, with:
- *Open* in the chosen Houdini/Maya version (picked to match the file).
- *Version up*, *Publish...*, *Render...*, *New scene...*, *Bring in file...*, *Show folder*.

To give an asset or shot a picture, click *+ THUMBNAIL* in the corner of the panel's
image and choose any photo. It's shown on the cards, the dashboard and the Publish page.

**Publish** (Version up): choose the asset/shot, step and work file. CORELINE
checks it first (saved, named right, which version saved it, newest version,
changed since the last publish), then publishes a numbered read-only copy with
your notes, sets the step's status, and (optionally) versions you up so you keep
working in a fresh file.

**Render**: renders a Houdini scene with hython, in the background.
1. Choose the scene (*Use selected* or *Browse...*); the Houdini version is matched to it.
2. The render nodes are found automatically (Karma nodes in `/out` or `/stage` first).
3. Set frames (e.g. `1001-1096`, or *Node's range* / *Shot's range*), resolution
   (100% final, 75/50/25% previews) and *Skip frames that are already rendered*.
4. *Submit render*. Renders run one at a time with progress bars and a live log.
   Logs are saved in `_ADMIN/coreline/logs/render/`. Closing CORELINE stops renders.
5. For a shot, *Save to the shot's render folder* (on by default) sends Karma's images to
   `shots/SH010/render/beauty/<scene name>/`, one folder per scene version. The AOVs stay
   inside the multi-layer EXR; the other `render/` folders are there for comp to split into.

**Parts** (Assets & Shots, *+ part* next to a step's work files): split a step into parts
worked on separately, e.g. the cheetah's geo into skeleton, muscle and skin. Each part has
its own versions (`cheetah_geo_muscle_v004_liangyi.ma`), publishes
(`geo/published/muscle/v002/`), status, assignee and turntable; *All* shows every file in the
step. *New scene* and *Bring in file* go into the selected part. A step without parts works as
before. Publishing a Maya file in a geo step (part or not) also saves a USD copy next to it
(`cheetah_geo_muscle_v002.usd`, metres, Y-up, Maya's names kept) in the background, for
Houdini (Otis, Solaris) to load.

**Turntables** (Assets & Shots, *Turntable...*, geo and lookdev steps): pick a publish (or a
work file, as a test), the number of frames and the samples, and CORELINE makes a turntable
MP4 for dailies: clay for geo (Maya files), shaded for lookdev (Houdini files; texturing
counts as lookdev). Nothing renders unless someone clicks it. For lookdev, step 1 is
instead: hython opens the `.hip` and exports the stage at the LOP node called
**`OUT_CORELINE_lookdev`** (e.g. the scene's Karma Render Settings), keeping the geometry
and materials but not its cameras, lights or render settings. In the template, the asset
spins once and then the HDRI spins once (`"lookdev_hdri_spin": false` turns that off).
In the background it:
1. opens the Maya file with `mayapy` and exports the **`OUT_CORELINE_geo`** set (or group) to USD in
   metres. Make that set in Maya with just the turntable geometry (e.g. only the high-res
   group); without it every visible mesh is used and the slate says so;
2. puts it into the team's **turntable template** (below), spinning once, and moves the
   template's camera nearer or further so the asset fits;
3. renders it with Karma (`husk`) to EXR frames, then turns their colour into display
   colour with Houdini's `hoiiotool` (colour only, so transparency never shows up in the MP4).
   `ocio_config`, `display` and `view` in `config/turntable.json` choose the colour; left
   empty, they match Houdini's own viewer. `ocio_config` is the lab's
   `S:/tools/OCIO/BU_config.ocio`; a PC that can't reach it (off the uni network) uses
   Houdini's own and says so in the log and the MP4's `.json`;
4. adds a slate (asset, version, artist, size, triangle count, UVs, publish notes) and a
   strip along the bottom with the CORE logo, and saves an MP4 with Houdini's `hffmpeg`:
   `published/v004/turntable/cheetah_geo_v004_turntable.mp4` (work-file tests go in
   `wip/turntables/`). A `.json` with the same numbers is saved next to it.

Progress shows on the Render page like any render. *Play turntable* in the asset panel
opens the latest one. Team defaults (frames, samples, resolution...) are in
`config/turntable.json`.

*Render on* in the Turntable dialog can send it to the **farm** instead (see Farm below):
- *Farm: one PC makes it all*: the first free farm PC makes the whole turntable;
- *Farm: split between PCs*: each PC takes *Frames per PC* frames, exports and builds the
  scene itself, renders its frames and saves a short MP4 piece in the farm folder; when
  every piece is done, one PC adds the slate and joins them into the turntable (the job's
  *MP4* chunk on the Farm page). Only the small pieces travel between PCs.
Farm PCs need Houdini, and Maya for geo turntables.

**The turntable template** is `_ADMIN/pipeline/turntable/turntable_template.hipnc`, a normal
Houdini (Solaris) scene next to its HDRI. Restyle it however you like, keeping:
- a Reference node whose file is `$CORELINE_ASSET` (opened by hand it shows
  `preview_asset.usda`, a stand-in, so you can judge the look);
- a camera prim called `turntable_cam`. CORELINE keeps its lens and angle and only moves it
  along its view line; anything under it (the grey and chrome balls) moves with it;
- a render node called `OUT_CORELINE_turntable` at the end;
- a material called `clay`, used on the asset for geo turntables.

A fresh starter template can be made with
`hython coreline/hython/make_turntable_template.py <folder>`. With `"template": ""` in
`config/turntable.json`, CORELINE uses its own plain grey studio instead.

**Farm** (lab render farm): several lab PCs share one Houdini render.
1. On each lab PC you're logged into, open the Farm page and click *Render farm jobs on
   this PC*, or double-click `CORELINE Farm Worker.bat` (no window needed; close it to stop).
   A PC doing farm work doesn't go to sleep.
2. On the Render page, set up the render as usual, tick *Send to the render farm*, choose
   *Frames per chunk* (10 by default) and click *Send to farm*.
3. Each PC takes a chunk of frames, renders it with *skip frames that are already rendered*,
   then takes the next. The Farm page shows every job, its chunks (who rendered what, and how
   long it took) and every PC, and can cancel a job, retry failed chunks or stop a PC.

There are **two farms**, and a worker watches both (yours first):
- **My PCs (H:)**: `H:/CORELINE_farm`, your own uni network drive. Only PCs logged in as you
  see it; fast, and no chunk is ever rendered twice. Change it on the Farm page.
- **Team (OneDrive)**: `_ADMIN/coreline/farm` in the project, so any teammate's worker can
  help. OneDrive syncs with a delay, so a PC that picks a chunk waits about a minute for
  other PCs' claims to arrive and the earliest claim wins; chunks start a little later.
  A PC with no H: drive (off the uni network) only uses this one.
Choose which farm when you send a render (*Farm* on the Render page) or a turntable (*Which
farm*). The Farm page shows both, each job tagged MINE or TEAM.

If a PC restarts (e.g. Windows updates) its chunk goes to another PC after 10 minutes (15 on
the team farm) and carries on from the last finished frame. A chunk is tried up to 3 times
(at most twice on the same PC) before it's marked failed. The farms only share small text
files; scenes and frames stay in the project, so each PC finds the scene in its own copy of
the project folder.

**Settings**: your name, the project folder, the final delivery date (shared
with the team), the drive letter, and the software list (make a version your
default, launch it, or add a location).

Tip: *Launch DCC* (Dashboard) starts any installed Houdini/Maya in the selected
task. Search with `Ctrl+K`. `F5` refreshes.

## Where things are stored

CORELINE works with the team's existing folders:

```
<project folder>/
  coreline_project.json                        shared settings: layout, tasks, fps, drive letter, delivery date
  assets/<type>/<name>/thumbnail.jpg           the asset's picture (shots can have one too)
  assets/<type>/<name>/<task>/wip/             e.g. assets/characters/cheetah/geo/wip/
  assets/<type>/<name>/<task>/published/v001/  numbered, read-only publishes
  shots/<shot>/<task>/wip/ + published/        e.g. shots/SH010/lighting/wip/
  shots/<shot>/render/beauty/<scene name>/     renders from CORELINE
  _ADMIN/coreline/                             tracking: status, assignee, frame ranges, publish notes, logs
```

- **Assets and shots** are simply the folders: add `assets/vehicles/pit_crew/` in
  Explorer and it shows up. Folders starting with `_` or `.` are skipped.
- **Tasks** are the folders inside an asset/shot that have a `wip` or `published`
  folder, so each asset can have its own (only the cheetah has `groom`).
- **Work files**: every scene file in `wip/` is listed, whatever its name. Files CORELINE
  makes (*Version up*, *New scene*, *Bring in file*) are named `cheetah_geo_v004_ritvik.mb`;
  the publish check warns about other names but doesn't stop you.
- **Tracking data** mirrors the folders under `_ADMIN/coreline/`, e.g.
  `_ADMIN/coreline/assets/characters/cheetah/geo/task.json`, so the artists' folders
  only hold their files. Each task has its own small file, so OneDrive doesn't make conflicts.
- **Shot frame ranges**: click *Frames not set* on a shot in Assets & Shots.
- **Thumbnails**: CORELINE saves the chosen picture shrunk to `thumbnail.jpg`; you can also
  drop a `thumbnail.png`/`.jpg` into the folder yourself. Each computer keeps resized
  copies in `%LOCALAPPDATA%/CORELINE/thumbs/`, made by Windows (no extra software needed).

The layout is set in `coreline_project.json` under `"layout"` (folder names, `"sequences"`,
`"tracking"`, `"shot_render"`, `"shot_folders"` made with each new shot), and the task
order under `"tasks"`. New projects start from `config/project_template.json`. Projects
without a `"layout"` use CORELINE's original layout (`work/`, `publish/`, sequences).

Each farm folder (`H:/CORELINE_farm`, and `_ADMIN/coreline/farm` for the team) holds
`jobs/<job>/job.json`, a `chunks/` folder of small claim/done/fail files (one per PC, so
OneDrive never merges two PCs' edits) and the chunks' `logs/`, and `workers/` (each PC's
heartbeat). Removing a job on the Farm page deletes only these files.

Personal settings (your name, project location, default versions) are kept in
`%APPDATA%/CORELINE/settings.json` on each computer, not in the shared folder.

## Adding software

Software is listed in `config/apps.json`: the program file, file extensions,
where to look (registry keys and folders), and how to open a file with it.
Copy the Houdini or Maya entry as a starting point.

## Tests

```
python -m unittest discover -s tests
```
