# Frameflow Design Specification

## Product intent

Frameflow is a scene-based video-making interface for the Wan2GP LTX-2.5 API. Its job is to make a technically heavy generation pipeline feel like a calm editorial workspace: direct one scene, review it, add the next, then assemble the story.

The creative north star is **“the director’s contact sheet.”** The UI combines the confidence of a film slate, the rhythm of a storyboard, and the restraint of an editorial layout. It should feel purposeful and cinematic without imitating a complex nonlinear editor.

The implementation lives in:

- `wan2gp_server/static/index.html`
- `wan2gp_server/static/studio.css`
- `wan2gp_server/static/studio.js`

## Core workflow

The interface follows one vertical path:

1. Confirm the GPU server connection.
2. Direct a scene using one of four modes.
3. Choose quality, aspect ratio, and duration.
4. Generate while following global and scene-level progress.
5. Review, regenerate, download, or delete the scene.
6. Add a connected scene below it.
7. Join an adjacent pair or concatenate every completed scene.
8. Download the final MP4.

The default mode is **Start image + prompt**. When its image input is empty, the application first generates a start frame with the LTX-2.5 Distilled model and then animates it. This automatic two-stage behavior is stated directly in the upload affordance.

### Generation modes

| UI mode | Input | API behavior |
|---|---|---|
| Plain prompt | Text | LTX-2.5 text-to-video |
| Start image + prompt | Optional image + text | LTX-2.5 image-to-video; automatically creates the start frame if empty |
| Video anchor + prompt | Video + text | Continues the uploaded source video |
| Last video + prompt | Previous completed scene + text | Continues the exact preceding scene job |

“Last video” deliberately references a job ID rather than process-global model history. This keeps a multi-scene timeline deterministic even when other clients submit jobs to the same server.

## Visual language

### Palette

The palette uses warm editorial surfaces against an inky cinematic anchor.

| Token | Value | Role |
|---|---:|---|
| `--paper` | `#f4f1eb` | Page background |
| `--surface` | `#fffdf9` | Scene cards and dialogs |
| `--surface-soft` | `#ebe7df` | Inputs and quiet controls |
| `--navy` | `#192a44` | Primary action and structural color |
| `--navy-deep` | `#101d31` | Preview and delivery surfaces |
| `--coral` | `#f26b51` | Generative action and emphasis |
| `--blue-soft` | `#dce5f2` | Selected/supporting actions |
| `--green` | `#28775d` | Ready and connected state |
| `--danger` | `#a23c35` | Error and destructive state |

Pure black is avoided. Preview wells use deep navy so video blacks remain distinct from the surrounding interface.

### Typography

- Display and headings: Manrope when available, falling back to Inter and the system sans stack.
- Interface and body: Inter when available, falling back to the system sans stack.
- Large headlines use tight tracking and a compact line height.
- Small uppercase coral eyebrows identify major stages: AI FILM WORKSPACE, STORYBOARD, and FINAL CUT.
- Control labels remain short and high-contrast. Secondary help copy stays at least 10px and is never the only place a required action is communicated.

The UI intentionally uses local fallbacks and does not require a public font CDN, so it renders reliably through a Colab tunnel.

### Surfaces and depth

Major areas are separated by tonal shifts rather than permanent 1px outlines:

- Warm paper base
- Slightly darker storyboard workspace
- White scene cards
- Deep navy preview and delivery panels

Ambient navy-tinted shadows are reserved for scene cards, dialogs, and the final-cut module. Controls use filled surfaces and focus rings instead of decorative borders.

### Shape

- Page modules: 28px radius
- Scene cards: 22px radius
- Inputs: 10–12px radius
- Status and connector actions: full pills

The rounded geometry softens the dense controls without making the product playful or toy-like.

## Component behavior

### Connection header

The sticky header keeps the Frameflow identity, active model, and server status available. States are:

- Amber: connecting
- Green: authenticated and online
- Red: server unavailable

The Frameflow notebook runs the API without authentication and publishes a clean tunnel URL, so no generated key, URL fragment, or browser setup is required.

### Scene card

Each card is split between a media well and a scene editor on wide screens. On smaller screens, the preview stacks above the controls.

The preview always exposes:

- Stable scene number
- Draft/generating/ready/error status
- Input image/video before generation
- Generated video with native playback controls after completion

The editor always exposes:

- Four creation modes
- Relevant media input
- Director’s prompt
- Quality, ratio, and duration
- Current progress and server status text
- Generate/regenerate, download, cancel, and delete actions as applicable

### Progress

Progress appears in two synchronized places:

1. A dark global progress module above the storyboard for the currently active operation.
2. A local rail inside the affected scene.

Progress text includes the meaningful Wan2GP phase/status, such as loading the model, generating the automatic start frame, inference, decoding, or joining clips. Percent alone is not relied upon.

### Scene connectors

Adjacent scenes have a centered **Join these scenes** pill. It remains disabled until both clips succeed. Joining creates a downloadable assembly but leaves the source scenes intact, preserving non-destructive editing.

The **Add next scene** control visually resembles a small clip with a coral plus badge. Every new scene defaults to Start image + prompt, matching the primary workflow, while all four modes remain available.

### Final cut

The final-cut module is the dark visual endpoint of the page. Concatenate All becomes available once two scenes have succeeded. Server-side FFmpeg normalization letterboxes clips with different ratios onto the first clip’s canvas, standardizes video/audio streams, and then joins them.

The result exposes an explicit final download button. Source scene downloads remain available independently.

## Responsive behavior

- Above 980px: two-column scene cards and a three-part delivery row.
- 681–980px: stacked scene card, two-column delivery module.
- 680px and below: single-column controls, two-by-two mode grid, full-width actions, compact workspace padding.

The minimum supported viewport is 320px. No essential action depends on hover.

## Accessibility

- Native buttons, selects, textareas, labels, dialog, video controls, and progress announcements are used.
- Every icon-only control has an accessible label.
- Keyboard focus uses a visible coral ring.
- Status uses words and shape in addition to color.
- The toast and progress regions use polite live announcements.
- Reduced-motion preferences suppress decorative transitions and pulse animation.
- Text and control colors are selected for readable contrast on their surfaces.

## Content style

Interface language is concise and production-oriented:

- “Generate scene,” not “Submit”
- “Director’s prompt,” not “Payload”
- “Video anchor,” not “video_path”
- “Final cut,” not “concatenation output”

Errors preserve useful server detail but appear in the scene where the action happened. Long-running work explains what is occurring instead of only displaying a spinner.

## Non-goals

Frameflow is not a frame-accurate nonlinear editor. It does not trim clips, edit audio tracks, add transitions, or reorder scenes by drag-and-drop. Its scope is generation, continuity, review, simple ordered assembly, and download.
