# End-to-End Episode Studio design

## Goal

Turn the existing Wan2GP/Frameflow server into a complete, resumable episode
maker. A user uploads two files:

- `scene-plan.json`
- `voiceover-source.txt`

The server validates them, generates one visual asset per scene with the model
already loaded by the Colab notebook, synthesizes Kokoro narration, creates a
word-timed transcript, builds and checks a HyperFrames composition, renders the
final MP4, and keeps every artifact available in a browser UI. Any scene can be
regenerated without losing its previously accepted revision.

This design is based on the current `wan2gp_server` implementation in this
repository and the production pipeline in the sibling checkout named
`consumer-trap-youtube-maker ` (the directory name has a trailing space).

## What already exists

### In `wan2gp-optimized`

The current server already provides the hard GPU-facing pieces:

- a single serialized WanGP worker and long-lived session;
- Z-Image Turbo and LTX-2.5 presets;
- text-to-image, text-to-video, and image-to-video requests;
- model warmup through `POST /v1/models/preload`;
- generation progress, cancellation, uploads, downloads, and FFmpeg joining;
- an LTX-oriented scene UI and a high-memory LTX-2.5 Colab notebook.

The LTX presets `ltx25-distilled-image`, `ltx25-distilled`, and
`ltx25-distilled-i2v` all resolve to `ltx2_25_22B_distilled`. Only one warmup is
needed; the task-specific presets merely apply different request settings.

### In `consumer-trap-youtube-maker `

The reusable CPU pipeline includes:

- validation for the existing Hidden Layer scene-plan contract;
- deterministic scene job manifests and stable seeds;
- chunked Kokoro ONNX narration;
- Whisper word timestamps and guarded script reconciliation;
- weighted scene timing;
- image-based HyperFrames composition, validation, inspection, and rendering;
- stage fingerprints and resumable artifacts.

That code should be ported into this repository rather than imported at runtime.
The sibling project assumes a local CLI workspace, images that already exist,
and cached tools under a particular home directory. Those assumptions do not
fit a Colab-hosted browser application.

## Product decisions

### One notebook, three selectable modes

The new notebook should expose one configuration variable near the top:

| Studio mode | Preloaded checkpoint | Scene output | Available generation |
|---|---|---|---|
| `image-z` | Z-Image Turbo | PNG/WebP | text to image |
| `image-ltx` | LTX-2.5 Distilled | PNG | LTX single-frame mode |
| `video-ltx` | LTX-2.5 Distilled | MP4 | text to video, or image to video when a scene has an accepted start frame |

Suggested notebook name: `episode_studio_colab.ipynb`.

The notebook translates the selected mode into server configuration:

```text
image-z   -> preload [z-image-turbo]
image-ltx -> preload [ltx25-distilled]
video-ltx -> preload [ltx25-distilled]
```

For LTX, warm `ltx25-distilled` once. Warming all three LTX preset IDs wastes
work because the preload endpoint deduplicates them by `model_type` anyway.

The browser must not allow a project to silently switch to a checkpoint that is
not the notebook's active family. A mode change that needs another checkpoint
is a deliberate server operation with a warning that model weights will be
swapped.

### Automatic run with a non-destructive review loop

An initial upload starts the whole pipeline automatically. The resulting final
video is a first cut, not an irreversible publish action.

Regenerating a scene creates a candidate revision. The currently accepted
revision and current final cut remain intact until the user selects **Use this
revision**. Accepting a new revision marks only composition/check/render stages
stale. Narration and transcript remain valid.

### Kokoro narration is the master audio

LTX-2.5 can generate synchronized audio, but this product is a narrated
documentary pipeline. Generated scene audio is muted in the HyperFrames
composition. `narration.wav` is the master track. Background music can be added
as a later optional post-mix; it is not required for the first implementation.

### Persistent projects, not browser-only state

The existing Frameflow UI keeps scene state and job IDs only in JavaScript, and
the current job store is in memory. That is insufficient for long Colab jobs,
page refreshes, scene revisions, or resuming after a server restart.

Each upload becomes a server-side project with an atomic `project.json`
manifest. Generation jobs may remain in the current queue, but every meaningful
transition and artifact path is persisted. On startup, queued/running work from
the previous process is marked interrupted and can be resumed safely.

## Project layout

All project data stays below `WAN2GP_SERVER_DATA_DIR/projects`; no API accepts an
arbitrary project output path.

```text
projects/<project-id>/
├── project.json
├── events.jsonl
├── source/
│   ├── scene-plan.json
│   └── voiceover-source.txt
├── narration/
│   ├── narration.wav
│   ├── transcript.raw.json
│   ├── transcript.json
│   ├── reconciliation-report.json
│   └── qa-report.json
├── scenes/
│   └── <scene-id>/
│       ├── r0001/
│       │   ├── request.json
│       │   ├── output.png|mp4
│       │   └── result.json
│       └── r0002/...
├── composition/
│   ├── index.html
│   ├── design.md
│   └── assets/...
├── renders/
│   ├── cut-r0001.mp4
│   └── cut-r0002.mp4
└── reports/
    └── run-report.json
```

The manifest records source hashes, selected runtime mode, stage states,
accepted scene revisions, current candidate revisions, generation job IDs,
resolved timing, and final render revision. Writes use a temporary file followed
by an atomic replace, guarded by a per-project lock.

## Input contract

The current `scene-plan-example.json` must work unchanged. The canonical JSON
Schema is in `docs/scene-plan.schema.json`.

Required compatibility fields are:

- project: `title`, `visual_concept`, `aspect_ratio`, `continuity`, `scenes`;
- scene: `id`, `narrative_role`, `script_reference`, `prompt`;
- the existing Hidden Layer descriptive fields remain accepted and are retained
  for the composition and UI.

Optional generation extensions are deliberately small:

- `image_prompt`: overrides `prompt` only for still generation;
- `video_prompt`: overrides `prompt` only for clip generation;
- `negative_prompt`;
- `duration_weight`, default `1.0`;
- `clip_duration_seconds`, default `8`, capped by the selected preset;
- `seed`, otherwise deterministically derived from project ID, scene ID, and
  revision;
- `start_image_scene_id`, for an LTX video scene that should animate another
  scene's accepted image.

Mode and model family belong to the project upload options, not to the editorial
scene plan. This lets the same plan be rendered once as stills and once as LTX
clips without rewriting source material.

Prompt resolution is deterministic:

```text
image mode -> image_prompt || prompt
video mode -> video_prompt || (prompt + explicit Motion field when present)
```

No prompt enhancer runs implicitly. The exact resolved prompt and seed are saved
with every revision.

`voiceover-source.txt` is clean spoken prose: at least twenty words, no URLs,
Markdown headings, stage directions, or SSML. The original upload is immutable.

## Pipeline

### Stage graph

```text
upload + validate
       |
       +-------------------------+
       |                         |
       v                         v
Kokoro narration         model readiness check
       |
       v
transcribe + reconcile
       |
       v
resolve scene timing
       |
       v
generate scene revisions (GPU queue, one at a time)
       |
       v
build HyperFrames composition
       |
       v
lint + validate + inspect
       |
       v
render final MP4 + media verification
```

The model is normally already warm because the notebook waits for warmup before
publishing the UI. The readiness stage still verifies the active family and
surfaces a useful recovery action when warmup failed.

Narration is intentionally produced before clip generation in `video-ltx` mode.
The final audio duration is otherwise unknown, so a video-first pipeline cannot
assign meaningful scene spans. In still modes, narration and the first still
jobs may run concurrently at the orchestration level, although GPU generation
continues to be serialized.

### Timing

For the first implementation, divide the reconciled narration duration using
`duration_weight`, preserving the current consumer pipeline's deterministic
behavior. Transitions overlap adjacent visual spans by a small fixed amount.

An optional future `narration_text` or word-range field can provide semantic
scene boundaries. `script_reference` should not be treated as an exact quote;
the example plan uses summaries, so fuzzy matching it to the transcript would
create unstable timing.

For video scenes, the generated clip duration is
`min(clip_duration_seconds, resolved_scene_duration, preset_maximum)`. If a
scene's narration span is longer than its clip, the composer uses a restrained
loop or holds the final frame according to a saved `fit` policy. It never
stretches video playback enough to create visibly unnatural motion.

### Stage states and invalidation

Every project stage uses:

```text
pending | queued | running | ready | failed | stale | cancelled | interrupted
```

Each ready stage stores a fingerprint of its inputs. Resume skips a stage only
when the fingerprint and output files are both valid.

Invalidation rules:

| Change | Invalidated |
|---|---|
| Voice, speed, or voiceover | narration onward, including scene timing |
| Scene prompt/seed/model or accepted scene revision | that scene generation, composition, checks, render |
| Caption styling or composition settings | composition, checks, render |
| Render quality/FPS only | render |
| Unaccepted candidate revision | nothing downstream |

Initial failures do not erase completed stages. **Resume pipeline** starts at the
first failed, interrupted, stale, or missing stage.

## Server architecture

Extend `wan2gp_server` rather than introduce a second web server.

Suggested modules:

```text
wan2gp_server/
├── projects.py       # project repository, locks, manifests, artifact URLs
├── scene_plan.py     # validation and prompt resolution
├── production.py     # stage DAG, fingerprints, resume/invalidation
├── narration.py      # HyperFrames Kokoro/transcribe wrappers + reconciliation
├── composition.py    # still/video HyperFrames composition builder
└── static/episode-*  # new UI files
```

The existing `Wan2GPEngine` remains the only path to WanGP. A separate
production coordinator submits GPU jobs to it and runs CPU subprocesses in a
bounded executor. HyperFrames and FFmpeg commands must not execute in the
FastAPI event loop.

The first version can use project manifests plus `events.jsonl`; SQLite is not
required. Keep job output paths private and expose project-scoped file URLs.
Absolute filesystem paths should no longer be returned to a public browser.

### API surface

New endpoints:

```text
POST   /v1/projects
       multipart: scene_plan, voiceover, studio_mode, voice, speed,
                  render_quality, render_fps

GET    /v1/projects
GET    /v1/projects/{project_id}
GET    /v1/projects/{project_id}/events?after=<sequence>
POST   /v1/projects/{project_id}/run
POST   /v1/projects/{project_id}/cancel

POST   /v1/projects/{project_id}/scenes/{scene_id}/regenerate
       JSON: prompt overrides, negative_prompt, seed policy, clip duration
POST   /v1/projects/{project_id}/scenes/{scene_id}/accept/{revision}

POST   /v1/projects/{project_id}/render
GET    /v1/projects/{project_id}/files/{artifact_id}
```

`POST /v1/projects` returns after validation and coordinator submission. The UI
polls the project document and monotonic event sequence; server-sent events can
replace polling later without changing the project model.

Keep the existing low-level `/v1/generations/*` endpoints for agents and expert
clients. Project generation internally calls the engine directly so it can
record exact job lineage without downloading and re-uploading server files.

## UI design

Replace the current blank-scene-first workflow with an episode-first workspace.
The current visual language from `DESIGN.md` can remain, but the information
architecture changes.

### 1. Import screen

- two visible drop zones for the plan and narration;
- parsed title, scene count, aspect ratio, voiceover word count, and validation
  errors before starting;
- locked runtime badge showing `Z-Image Turbo` or `LTX-2.5`;
- output choice limited by the runtime (`Images` only for Z-Image; `Images` or
  `Video clips` for LTX);
- Kokoro voice, speed, render quality, and FPS;
- primary action: **Create episode**.

### 2. Episode workspace

```text
+---------------------------------------------------------------+
| Episode title        Runtime: LTX-2.5      Overall 7/11 stages |
| [Resume pipeline] [Cancel] [Download final]                    |
+----------------------+----------------------------------------+
| Pipeline rail        | Scene board                            |
| ✓ Inputs             | 01 Familiar situation     Ready        |
| ✓ Narration          | [preview] prompt summary                |
| ✓ Transcript         | [Edit prompt] [Regenerate]              |
| ● Scene assets 4/6   |                                        |
| ○ Composition        | 02 Decision moment        Generating    |
| ○ Checks             | [previous preview]  62% inference       |
| ○ Render             | ...                                    |
+----------------------+----------------------------------------+
| Narration player + word-highlighted transcript                 |
+---------------------------------------------------------------+
```

Each scene card shows:

- stable scene ID, role, resolved narration span, and generation status;
- image or video preview;
- accepted revision and candidate revision side by side when applicable;
- exact prompt, negative prompt, seed, model preset, and clip fit settings in an
  expandable editor;
- **Regenerate**, **Use this revision**, **Keep current**, and download actions;
- local queue position/progress and actionable error details.

Only one scene editor is expanded at a time. Long plans remain usable through a
compact scene navigator and status filters (`All`, `Needs review`, `Failed`).

### 3. Delivery panel

The final panel contains the rendered player, duration/resolution/FPS, source
revision, warnings, **Download MP4**, and **Rebuild final**. A final cut is labeled
stale—but remains playable and downloadable—after an accepted scene changes.

### Browser behavior

- Reopening the URL restores projects from the server.
- Refreshing does not cancel work.
- The UI polls only while work is active and backs off when idle.
- All progress includes a phase label; percent alone is never the status.
- Cancel applies to the active stage and preserves previous artifacts.
- Destructive project deletion is outside the first implementation.

## HyperFrames composition

Port the Hidden Layer design and caption grouping logic, then generalize the
visual node:

- still mode: `<img>` with controlled pan/scale and role-aware reveals;
- video mode: `<video muted playsinline>` trimmed to the resolved span, using the
  saved loop/hold policy when necessary;
- narration: one master audio element copied into composition assets;
- captions: groups derived from reconciled word timestamps;
- final dip and transition overlap remain deterministic.

Composition output must be self-contained below the project directory. Run:

```text
hyperframes lint
hyperframes validate
hyperframes inspect
hyperframes render --strict
```

The render worker uses a single Chrome worker on constrained Colab systems. It
verifies the final file with `ffprobe`: video stream present, audio stream
present, non-zero duration, expected aspect ratio, and duration close to the
narration.

## Colab notebook design

The notebook should be runnable top to bottom:

1. Verify GPU, VRAM, system RAM, and free disk.
2. Select `STUDIO_MODE`, memory profile, output/cache paths, and optional Drive
   persistence.
3. Clone/update this repository.
4. Install FFmpeg and browser libraries.
5. Install the pinned WanGP Python environment and server extras.
6. Install a pinned HyperFrames CLI, ensure its Chrome browser, and install
   Kokoro/transcription dependencies.
7. Start the server with project data below `/content/episode-studio` or a
   mounted Drive directory.
8. Submit and monitor the selected model warmup. Do not expose the UI until the
   warmup succeeds.
9. Start a Cloudflare tunnel and show the Episode Studio link.
10. Provide diagnostics and clean shutdown cells.

Suggested runtime policy:

- `image-z`: WanGP profile 5; suitable for constrained/T4-class sessions.
- `image-ltx` and `video-ltx`: default to the proven high-memory profile 1
  checks from `ltx25_video_studio_colab.ipynb`, with an explicit advanced
  override for lower-memory profiles.
- Pin PyTorch and HyperFrames versions. Do not rely on the newest package at
  every notebook run.
- Print the server log tail and resource snapshot when warmup, rendering, or a
  subprocess dies.
- If Drive persistence is enabled, keep project artifacts there but keep
  transient frame caches under `/content` for performance.

Authentication should follow the deployment context. A private Colab quick
tunnel may omit an API key for convenience, matching the current Frameflow
notebook. If public sharing is expected, generate an API key and have the UI use
a short-lived same-origin session cookie; do not put the key in a URL fragment.

## Implementation sequence

### Phase 1: project core

- Add scene-plan/voiceover validation and project persistence.
- Add project API and filesystem-scoped artifact serving.
- Add coordinator states, fingerprints, resume, cancellation, and startup
  recovery.
- Unit test the supplied example plan unchanged.

### Phase 2: narration and still-image end to end

- Wrap pinned HyperFrames Kokoro and transcription commands.
- Port guarded transcript reconciliation and audio QA.
- Generate Z-Image and LTX still revisions through the existing engine.
- Port/generalize the image HyperFrames composition and final media checks.

This phase produces a complete `image-z` and `image-ltx` workflow.

### Phase 3: LTX video scenes

- Add resolved video prompts, clip-duration policy, and optional accepted start
  frames.
- Add muted video nodes, loop/hold behavior, and video-specific checks.
- Test mixed image/video internal composition even if the initial UI keeps a
  project-level output mode.

### Phase 4: episode UI

- Build import, pipeline rail, scene revisions, transcript, and delivery views.
- Restore projects after refresh and test cancel/resume/error recovery.
- Keep the old low-level Frameflow page reachable during migration if useful.

### Phase 5: notebook and full Colab verification

- Create `episode_studio_colab.ipynb` with the three modes.
- Verify cold Z-Image and cold LTX downloads separately.
- Run the supplied scene plan and a real voiceover through complete render.
- Confirm a scene regeneration, candidate acceptance, and final-cut rebuild.

## Acceptance criteria

- The supplied `scene-plan-example.json` validates without modification.
- Uploading the two source files starts a project and survives page refresh.
- The configured checkpoint is warm before the UI is published.
- Every scene produces an accepted revision with exact prompt/seed lineage.
- One failed scene can be retried without rerunning successful scenes.
- A regenerated candidate does not destroy the accepted asset or current cut.
- Kokoro output, raw transcript, reconciled transcript, and QA report are
  downloadable.
- HyperFrames lint/validate/inspect pass before render.
- Final MP4 contains both video and Kokoro narration and matches narration
  duration within a small tolerance.
- Restarting the server can resume an interrupted project from disk.
- No public API response exposes an unrestricted server filesystem path.

## Explicit non-goals for the first version

- frame-accurate nonlinear editing;
- arbitrary scene reordering after upload;
- automatic script or prompt rewriting;
- multi-GPU or concurrent WanGP generations;
- cloud object storage or multi-user accounts;
- publishing directly to YouTube;
- background-music generation and mastering.

