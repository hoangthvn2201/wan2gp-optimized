# QA Report — Frameflow LTX-2.5 Video Studio

**Build under test:** `https://mon-imports-soft-zero.trycloudflare.com/` (Cloudflare tunnel)
**Date:** 2026-08-15
**Tester:** automated browser QA (Playwright-driven Chrome)
**Test profile:** all generations run at **Draft · fast / 3 seconds** for fast turnaround
**Scope:** full functional pass of the scene storyboard, all four generation modes, per-scene actions, and final-cut assembly

**Verdict:** the core generation pipeline is solid and every mode produces a valid, playable MP4 with audio. Three defects are open, one of which (**BUG-1**) silently corrupts every multi-scene story built with the app's headline "Last clip" chaining feature and should be fixed first.

---

## 1. Summary

| # | Severity | Area | Title | Status |
|---|----------|------|-------|--------|
| BUG-1 | **High** | Continuation | Source clip is baked into the output of Last-clip / Video modes | Open |
| BUG-2 | **Medium** | Resolution | "Video ratio" silently ignored when a start image or video anchor is supplied | Open |
| BUG-3 | **Medium** | Final cut | Assembly goes stale after the timeline is edited, but stays downloadable | Open |
| OBS-1..6 | Low | UX / polish | See §4 | Open |

**Test coverage:** 13 functional areas exercised, 10 passed clean, 3 failed.

---

## 2. What works (passed)

| Feature | Evidence |
|---|---|
| Page load, GPU status banner | Renders correctly, reports "GPU server online" |
| Text mode | 768×448, 3.04 s, H.264 + AAC |
| Image mode + file upload | Upload, thumbnail preview, and generation all correct |
| Video mode + anchor upload | Accepted MP4, generated successfully |
| Last clip mode gating | Correctly disabled on Scene 01, enabled from Scene 02, shows "Scene 01 linked" |
| Quality selector | Draft/High/Max map to the expected resolutions |
| Length selector | 3 s → 3.04 s (73 frames @ 24 fps — correct temporal-grid snapping) |
| Video ratio in **Text** mode | 9:16 → 448×768 ✅ (contrast with BUG-2) |
| Regenerate | Produced a genuinely new clip (new blob), correct dimensions and duration |
| Delete + renumbering | Scene 04 correctly became Scene 03; scene indices re-flow properly |
| Per-scene Download | `frameflow-scene-01.mp4`, valid H.264 + AAC |
| Concatenate (mixed ratios) | Landscape + portrait normalized to one canvas, **pillarboxed not stretched**, audio intact across all cuts |
| Live progress reporting | Per-scene percentage and WanGP phase detail ("Denoising First Phase") stream correctly |
| Browser console | Clean apart from a favicon 404 |

Mixed-ratio assembly deserves specific credit: a 448×768 portrait scene was correctly pillarboxed into the 768×448 canvas with no distortion, driven by the `force_original_aspect_ratio=decrease` + `pad` filter chain in `wan2gp_server/engine.py:215-216`.

---

## 3. Defects

### BUG-1 — Source clip is baked into the output of Last-clip and Video modes

**Severity:** High — silently corrupts every multi-scene story
**Modes affected:** `Last clip`, `Video` (both take the `video_to_video` path)

#### Observed

| Scene | Mode | Requested | Actual duration |
|---|---|---|---|
| 01 | Text | 3 s | 3.04 s ✅ |
| 02 | Last clip | 3 s | **6.04 s** ❌ (= 3.04 s of Scene 01 + 3 s new) |
| 04 | Video (2.10 s anchor) | 3 s | **5.08 s** ❌ (= 2.10 s anchor + 3 s new) |

#### Proof the prefix is a literal copy of the source

Downscaled frames were compared pixel-by-pixel (mean absolute RGB difference per pixel, 0 = identical):

```
Scene01@0.2s vs Scene02@0.2s →  1     (identical)
Scene01@1.0s vs Scene02@1.0s →  1     (identical)
Scene01@2.5s vs Scene02@2.5s →  1     (identical)
Scene02@0.2s vs Scene02@4.5s → 54     (control: genuinely different frames)
Scene01@0.2s vs Scene01@2.5s → 44     (control: genuinely different frames)
```

The residual `1` is re-encode noise. Scene 02's first three seconds are Scene 01, frame for frame.

#### Impact on the final cut

The duplication propagates into the delivered MP4. On the two-scene final cut (`frameflow-final-cut.mp4`, 9.10 s):

```
final@1.0s vs final@4.0s →  2.4    (duplicated segment)
final@1.0s vs final@7.5s → 39.1    (control)
```

So the viewer sees **Scene 01, then Scene 01 again, then Scene 02**. Every story built with the chaining feature ships with each scene repeated.

#### Root cause

`wan2gp_server/app.py:354-370` submits the continuation job with:

```python
extra_settings={
    **req.settings,
    "image_prompt_type": "V" if source_path is not None else "L",
    **({"video_source": str(source_path.resolve())} if source_path is not None else {}),
},
```

In WanGP, `image_prompt_type="V"` + `video_source` means *continue this video*, and the engine **prepends the retained source frames to the rendered output**. How many frames are retained is governed by `keep_frames_video_source`, which the server never sets — see `wgp.py:342-353`:

```python
keep_frames = max_source_video_frames if len(str(keep_frames_video_source or "")) == 0 else int(keep_frames_video_source)
```

With the setting absent, WanGP defaults to keeping the **entire** source clip, which is exactly the observed `source_duration + requested_duration` output.

#### Suggested fix

The continuation context should condition the generation without being delivered as output. Two viable approaches:

1. **Preferred — trim on return.** Keep the full source as conditioning (it improves continuity), then cut the leading `source_frames` off the output before handing the file back, so the scene the user receives is exactly the duration they asked for.
2. **Alternative — bound the retained frames.** Set `keep_frames_video_source` to a small overlap window (e.g. the last ~8–16 frames) so only a short conditioning tail is prepended, then trim that tail.

Whichever is chosen, `duration_seconds` must mean *the length of the resulting clip*, since that is what the UI promises. Note the temporal grid: durations snap to `frame_quant * n + 1` frames (`wan2gp_server/presets.py:308-321`), so trim on a frame boundary rather than a timestamp.

#### Regression test

Generate Scene 01 (3 s, Text), then Scene 02 (3 s, Last clip). Assert `scene02.duration ≈ 3.04 s`, and assert `Scene01@1.0s` vs `Scene02@1.0s` frame difference is **> 20**.

---

### BUG-2 — "Video ratio" is silently ignored when a start image or video anchor is supplied

**Severity:** Medium — misleading control, wrong deliverable format
**Modes affected:** `Image`, `Video`, `Last clip` (any mode with source media)

#### Observed

| Scene | Mode | Ratio selected in UI | Output |
|---|---|---|---|
| 03 | Image (1280×720 start image) | **9:16 · Portrait** | **768×448 landscape** ❌ |
| 04 | Text | **9:16 · Portrait** | 448×768 portrait ✅ |

Both scenes were configured identically apart from the mode. The dropdown continued to display "9:16 · Portrait" after generation, with no warning, toast, or visual indication that the setting had been discarded. A user targeting a portrait deliverable gets a landscape file and no explanation.

#### Root cause

The frontend does send the right dimensions — `wan2gp_server/static/studio.js:281` resolves `RESOLUTIONS[scene.ratio][scene.quality]` and passes them on every path, so this is not a frontend plumbing bug. The override happens downstream in WanGP: `wgp.py:5392` calls

```python
height, width = calculate_new_dimensions(height, width, frame_height, frame_width, fit_into_canvas=fit_canvas, block_size=block_size)
```

which recomputes the target dimensions from the **source media's** aspect ratio. The server never sets `fit_canvas`, so the input image's 16:9 shape wins over the requested 9:16.

#### Suggested fix

Pick one and make the UI consistent with it:

- **Honour the selector:** set `fit_canvas` so the source is fitted (letterboxed/cropped) into the requested canvas rather than dictating it.
- **Honour the source:** derive the ratio from the uploaded media, and in the UI either disable the ratio dropdown with a note ("matched to your start image") or auto-select the ratio the source implies.

The current behaviour — offering the control, accepting the input, then discarding it silently — is the worst of the three.

---

### BUG-3 — Final cut goes stale after the timeline is edited but remains downloadable

**Severity:** Medium — user ships a video that does not match their timeline

#### Reproduction

1. Generate 4 scenes, click **Concatenate all** → final cut is 15.19 s.
2. Delete one 3.04 s scene. Timeline is now 3 scenes; expected assembly length 12.12 s.
3. **Download final cut** is still enabled and still present in the panel.
4. The downloaded file is **15.19 s** — the old 4-scene render, including the deleted scene.

There is no invalidation, no "out of date" badge, and no prompt to re-assemble. A user who deletes a bad take and downloads will ship the bad take.

#### Root cause

`wan2gp_server/static/studio.js:476-482` — the delete handler revokes the scene's object URLs and filters `state.scenes`, but never touches `state.assembly`:

```js
} else if (action === "delete") {
  if (["queued", "running"].includes(scene.status) && scene.job) cancelScene(scene);
  if (scene.inputUrl) URL.revokeObjectURL(scene.inputUrl);
  if (scene.outputUrl) URL.revokeObjectURL(scene.outputUrl);
  state.scenes = state.scenes.filter(item => item !== scene);
  renderAll();
}
```

`state.assembly` still holds the completed job, so `renderDelivery` (`studio.js:203-207`) keeps `#downloadAssembly` visible and `#downloadAssembly`'s click handler (`studio.js:497`) keeps serving the old `state.assembly.job`.

#### Suggested fix

Invalidate the assembly whenever the set of delivered scenes changes — on delete, on **Regenerate**, and on add-after-assembly. Simplest correct approach: record the ordered list of job IDs used to build the assembly, and on every render compare it against the current completed-scene job IDs; if they differ, mark the assembly stale (hide the download button, or show "Timeline changed — re-assemble to update"). This also covers **Regenerate**, which today replaces a scene's underlying job while the old assembly stays downloadable.

---

## 4. Observations (low severity)

- **OBS-1 — No preview player for the final cut.** Per-scene clips get an inline player, but the assembled cut can only be inspected by downloading the MP4. Reviewing a story means leaving the app.
- **OBS-2 — Delete has no confirmation and no undo.** One click permanently destroys a generated clip (`studio.js:476`). Given each clip costs GPU time, a confirm step or an undo toast is worth adding.
- **OBS-3 — New scenes do not inherit prior settings.** Every added scene resets to `quality: "standard"`, `ratio: "16:9"`, `duration: 5` (`studio.js:34`). Working at Draft/3 s means re-selecting both on every scene. Inheriting the previous scene's settings would match how the tool is actually used.
- **OBS-4 — Draft 16:9 is 12:7, not 16:9.** `RESOLUTIONS["16:9"].draft = [768, 448]` (`studio.js:12`) is 1.714, where true 16:9 is 1.778. Standard (1280×704) and Max (1920×1088) are likewise slightly off. These appear to be deliberate choices to sit on the model's 64-pixel grid, so this is flagged for confirmation rather than as a bug — but a 16:9 deliverable will not be exactly 16:9.
- **OBS-5 — Output audio is quiet.** Final cut measured mean −36 dB / peak −25.5 dB, and −35.7 dB / −14.3 dB on the four-scene assembly. Non-silent and arguably appropriate for the prompted content ("gentle wind", "quiet crackle"), but low for a delivered master. Consider loudness normalization at the concat stage.
- **OBS-6 — `favicon.ico` 404s on every page load.** `favicon.png` exists at the repo root but is not wired into `wan2gp_server/static/index.html`. This is the only console error observed during the entire session.
- **OBS-7 — Last-clip chain source is resolved at generate time.** `studio.js:341` picks `state.scenes[index - 1]`. If a scene is deleted and a later Last-clip scene is regenerated, it will silently chain from a different neighbour than the one shown when it was first built. Not observed as a failure, but worth a guard.

---

## 5. Test environment and method

- Driven headlessly through Playwright against Chrome; all interactions performed through the real UI (clicks, file choosers, native `select` changes).
- Video properties verified with `ffprobe`; frame-level duplication verified by decoding frames to raw RGB at 32×18 and computing mean absolute per-pixel difference; audio levels measured with `ffmpeg -af volumedetect`.
- Files exercised: a generated 1280×720 PNG (start image) and a 2.10 s MP4 trimmed from a prior render (video anchor).
- Six generations completed; each Draft/3 s clip took roughly 35–45 s wall clock.
- Downloaded artifacts and screenshots were kept out of the repository; the working tree was left clean.

## 6. Suggested fix order

1. **BUG-1** — highest impact, corrupts the primary multi-scene workflow, and the fix is contained (trim the prepended source before returning the clip).
2. **BUG-3** — small frontend change, prevents users shipping the wrong video.
3. **BUG-2** — needs a product decision (honour the selector vs. honour the source) before implementation.
4. **OBS-3, OBS-2, OBS-6** — cheap UX wins.
5. **OBS-1, OBS-5** — polish for the delivery step.
