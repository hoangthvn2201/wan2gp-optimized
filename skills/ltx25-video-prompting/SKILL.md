---
name: ltx25-video-prompting
description: Create production-ready scene prompts optimized for LTX-2.5 video generation, including independent start-image mode and chained-anchor mode. Use when a user wants to turn a topic, script, story, lesson, ad, explainer, or scene idea into LTX-2.5 image/video prompts.
---

# LTX-2.5 Video Prompting Skill

## Purpose

Turn a user's topic, script, story, lesson, or scene idea into production-ready prompts optimized for **LTX-2.5**. The skill must first verify the creative requirements, then establish the number of scenes, then let the user choose one of two generation modes.

The final prompts must be easy to copy into an image model or LTX-2.5. **Every individual image prompt and every individual video prompt must be written as one self-contained paragraph.**

## Primary Reference

This skill follows the official LTX-2.5 prompting guidance:

- LTX-2.5 Prompt Guide: https://ltx.io/blog/ltx-2-5-prompt-guide
- Official LTX prompting documentation: https://docs.ltx.video/

Core principles taken from the official guidance:

- Establish the shot.
- Set the scene, including lighting, palette, texture, and atmosphere.
- Describe action as a clear chronological sequence.
- Define characters with concrete visible identifiers.
- Express emotion through physical behavior rather than abstract emotion labels.
- Describe camera movement explicitly and relative to the subject.
- Describe audio, ambience, music, speech, or singing when relevant.
- Put spoken dialogue in quotation marks.
- Specify dialogue language and accent when relevant.
- Prefer focused scenes with a small number of clear subjects and actions.
- Keep lighting logic coherent inside a shot.
- Use present-tense action.
- For a simple continuous shot, prefer one flowing paragraph of roughly 4–8 useful descriptive sentences.
- For image-to-video, prefer a single continuous take unless a cut is intentionally required.
- If using multiple shots inside one generation, explicitly name every transition, re-establish framing, preserve identity, and state audio continuity.
- Keep important on-screen text short; critical text or logos should normally be added in post.
- Prefer plausible, readable motion over unnecessarily chaotic physics.

---

# Required Interaction Workflow

Follow these stages **in order**. Do not skip directly to prompt generation unless the user has already explicitly supplied and confirmed the information required by the earlier stages.

## Stage 1 — Verify Topic and Requirements

First, inspect everything the user has already provided. Extract the known requirements and do not ask for information that is already clear.

Summarize the interpreted creative brief and ask the user to confirm or correct it.

At minimum, verify when relevant:

- topic or story;
- objective of the video;
- important events or beats that must appear;
- main character(s), creature(s), product(s), or object(s);
- recurring visual identifiers that must stay consistent;
- environment or location;
- visual style or medium;
- tone or mood;
- dialogue or narration;
- spoken language and accent, if any;
- audio expectations;
- aspect ratio, if provided;
- scene duration or total duration, if provided;
- any forbidden elements or continuity constraints;
- any reference image or existing first frame supplied by the user.

Keep this verification compact. Do not invent missing story-critical facts if they could materially change the scene.

### Required first-stage question pattern

Use a concise confirmation such as:

> I understand the video as: [brief summary of topic, characters, setting, style, dialogue/audio, and key action]. Is this correct, or is there anything you want changed before I split it into scenes?

Do **not** ask about scene count before the topic and requirements are sufficiently verified.

---

## Stage 2 — Ask for Number of Scenes

After the creative brief is verified, ask how many scenes the user wants.

Explain that each scene should normally represent one clear visual/action beat and one LTX-2.5 generation unit.

Use wording similar to:

> How many scenes do you want? I will treat each scene as one focused LTX-2.5 clip, with a clear beginning state, action progression, camera behavior, audio, and ending state.

If the user already provided a scene count, accept it and continue without asking again.

---

## Stage 3 — Ask for Generation Mode

After the scene count is known, ask the user to choose **Mode 1** or **Mode 2**. Clearly explain the practical difference.

### Mode 1 — Start Image for Every Scene

Use this when every scene will be generated from its own separately created first frame.

For **every scene**, output:

1. **Start Image Prompt**
2. **Video Prompt**

Explain to the user:

> **Mode 1 — Start image for every scene:** I create a separate start-image prompt and an LTX-2.5 video prompt for every scene. Choose this when you want maximum control over the composition of each scene, when scenes change location or framing significantly, or when each clip will be generated independently.

### Mode 2 — Previous Scene Becomes the Next Anchor

Use this when Scene 1 begins from a generated start image and the **final frame of each generated scene becomes the visual anchor / start image for the next scene**.

Output:

- **Scene 1:** Start Image Prompt + Video Prompt
- **Scene 2 onward:** Video Prompt only

Explain to the user:

> **Mode 2 — Chained anchor:** I create one start-image prompt only for Scene 1. After Scene 1 is generated, its final frame becomes the starting anchor for Scene 2; Scene 2's final frame anchors Scene 3, and so on. I therefore write only video prompts for Scene 2 onward, with each prompt designed to continue naturally from the exact visible state left by the previous scene. Choose this for continuous action, stronger spatial continuity, or a sequence that should feel like one uninterrupted evolving moment.

If the user already specified a mode, do not ask again.

---

# Scene Planning Rules

Before writing prompts, internally create a scene plan. Do not expose the internal plan unless the user asks for it.

For every scene, determine:

- scene purpose;
- opening visual state;
- primary subject;
- subject position in frame;
- secondary subjects already visible;
- environment;
- stable character identifiers;
- clothing and props;
- lighting;
- shot scale and camera angle;
- core action;
- camera movement;
- dialogue;
- ambience / sound / music;
- ending visual state;
- transition requirement;
- what must remain consistent into the next scene.

Each scene should have **one main visual job**. Avoid packing several unrelated story beats into a single generation.

Default to **one continuous shot per scene**. Only create multiple cuts inside one scene when the user asks for them or the action truly requires them.

---

# LTX-2.5 Video Prompt Construction

## Default Prompt Shape

For a normal LTX-2.5 scene, write **one chronological paragraph** that naturally covers:

**shot/framing → scene and lighting → visible subjects → action progression → physical performance → camera movement → ending composition → audio/dialogue**

Do not output this as a checklist. The final prompt must read as natural cinematic prose.

### Example structural pattern

`[Shot scale and angle] frames [subject] in [environment with lighting, palette, texture, atmosphere]. [Character/object identifiers and spatial placement]. [First action] then [next natural action], with [physical cues]. The camera [specific movement relative to subject], ending with [clear final composition/state]. [Ambient sound/music]. [Speaker] says in [language/accent/delivery], "[dialogue]."`

This is a structural guide, not a literal template that must sound identical every time.

---

# Core Prompting Rules

## 1. Establish the Shot

Use concrete camera language appropriate to the scene:

- wide establishing shot;
- medium shot;
- medium close-up;
- close-up;
- extreme close-up;
- over-the-shoulder;
- low-angle;
- high-angle;
- overhead;
- profile view;
- static frame;
- handheld documentary framing.

Do not stack incompatible shot types.

## 2. Set the Scene

Describe only useful visual information:

- location;
- time of day;
- coherent lighting source;
- color palette;
- surface texture;
- weather or atmosphere;
- depth/background elements that matter.

Use one clear lighting logic. Avoid contradictory instructions such as strong warm sunset light and cold overhead fluorescent light unless both sources are intentionally visible and spatially plausible.

## 3. Describe Action Chronologically

Use present tense.

Actions must progress in the order the viewer sees them.

Prefer:

`She turns toward the back row, pauses, raises one hand, then speaks.`

Avoid unordered bundles such as:

`She is turning, talking, writing, walking, and reacting.`

When timing matters, use natural temporal markers:

- initially;
- then;
- a moment later;
- as this happens;
- after a brief pause;
- simultaneously, only when actions genuinely occur together;
- finally.

Do not overload a short scene with too many independent actions.

## 4. Define Characters Visually

For recurring characters, maintain a stable identity description.

Useful identifiers include:

- approximate age;
- species if non-human;
- hairstyle or fur;
- face shape or distinguishing feature;
- clothing;
- accessories;
- body scale;
- recurring prop.

Prefer visible physical cues over abstract labels.

Instead of:

`The teacher is angry.`

Write:

`The teacher's brows tighten, her ears angle backward, and she turns sharply toward the back row.`

When multiple characters are present, reduce pronoun ambiguity. Re-identify the subject when necessary.

## 5. Describe Camera Motion Explicitly

State:

- what the camera does;
- when it does it;
- what it follows or reveals;
- what the final framing looks like.

Examples:

- the camera slowly pushes in toward her face;
- the camera pans right to reveal the student in the final row;
- the camera tracks beside him as he walks;
- the camera remains locked off while the action happens inside the frame;
- the camera tilts upward from the desk to her face.

Avoid unexplained camera teleportation.

## 6. Describe Audio

When audio matters, include:

- room tone or ambience;
- Foley;
- music;
- dialogue;
- voice quality;
- delivery volume.

Put spoken dialogue in quotation marks.

Specify spoken language and accent when relevant.

Example:

`She speaks in Vietnamese with a clear, warm northern accent, mildly stern but controlled: "Em cuối lớp kia tên gì, vào sổ đầu bài ngồi nhé."`

Do not accidentally assign the same dialogue to multiple characters.

---

# Image-to-Video Rules

LTX-2.5 image-to-video should normally be treated as **continuation from an already visible first frame**.

The video prompt must respect what exists in the anchor image.

Do not make the prompt behave as though the model must recreate the entire first frame from scratch.

Prioritize:

1. motion;
2. physical performance;
3. camera behavior;
4. evolving spatial relationships;
5. dialogue and sound;
6. the desired ending state.

Repeat identity or environmental details only when they help preserve continuity.

By default, use a **single continuous take** for image-to-video. If a cut is necessary, describe it explicitly.

---

# Start Image Prompt Rules

A start-image prompt defines the **static opening frame**, not the full future action.

Write exactly **one paragraph**.

Include:

- visual style / medium;
- shot scale;
- camera angle;
- subject identity;
- wardrobe/accessories;
- subject pose;
- facial or physical expression;
- exact spatial placement;
- secondary characters and where they are;
- environment;
- important props;
- coherent lighting;
- color palette and atmosphere;
- depth/background;
- aspect ratio or composition needs if known.

Do not describe a long chain of future motion in the image prompt.

Avoid temporal language such as:

- then;
- afterward;
- begins walking and later stops;
- camera pans;
- she turns and then speaks.

Instead capture the precise state **just before the scene's main motion begins**.

### Good principle

The image prompt answers:

> What must be visibly true in frame zero so that the intended video motion can begin without anything needing to appear from nowhere?

---

# Mode 1 Rules — Independent Start Image for Every Scene

For every scene:

1. Create one Start Image Prompt.
2. Create one Video Prompt optimized for that start image.

Because scenes are generated independently, repeat important stable identity details in each start-image prompt.

Keep recurring items consistent:

- character appearance;
- clothing;
- accessories;
- proportions;
- environment identity;
- important props;
- time of day;
- visual style.

A scene may intentionally change location, outfit, or lighting only if the story requires it.

The video prompt should assume its own start image already establishes the opening composition.

---

# Mode 2 Rules — Chained Anchor Continuity

Scene 1 gets a Start Image Prompt and Video Prompt.

For Scene 2 onward, create **Video Prompt only**.

The starting anchor of Scene N is the final frame of Scene N-1.

## Mandatory Chaining Logic

Before writing the next scene, internally preserve the previous scene's ending state:

- who is visible;
- exact approximate position of each subject;
- facing direction;
- pose;
- object placement;
- which hand holds a prop;
- camera position;
- framing;
- lighting;
- weather/atmosphere;
- wardrobe;
- background;
- current motion;
- current emotional/physical expression.

The next scene must begin from this state.

Do not reset the composition without explanation.

Do not make a character suddenly appear at a desk, beside the camera, or in the foreground if that character was previously elsewhere or not visible. If a new subject must enter, explicitly describe a plausible entrance or reveal.

Do not make props appear, disappear, change hands, or change size without a visible cause.

Do not change the side of the frame or screen direction casually when that would break spatial continuity.

If the next action needs a new framing, transition into it with camera movement or an explicit cut.

## End-Frame Handoff

Every chained scene must end in a frame that makes the next scene easy to continue.

When planning Scene N, consider what Scene N+1 needs.

Examples:

- If the next scene needs the teacher to address a student in the back row, Scene N can end after the teacher notices the student, with the student's location already established in frame or through a camera reveal.
- If the next scene needs a character to pick up an object, Scene N should end with the character close enough to reach it.
- If the next scene needs a camera push-in, Scene N should end with a composition that supports that movement.

This is a key rule for preventing teleportation, sudden character insertion, and composition jumps.

---

# Spatial Continuity Guardrails

These rules are mandatory whenever multiple characters or objects interact.

## Establish Before Interaction

A character should not suddenly become important from an undefined location.

Before one character addresses, touches, hands something to, or reacts to another character, make the second character spatially legible.

Use one of these methods:

- include both in the opening image;
- reveal the second character with a pan or track;
- show them in background depth;
- have them enter through a visible doorway or edge of frame;
- use an explicit cut that re-establishes the geography.

## Preserve Geography

Keep stable:

- front/back of room;
- left/right side;
- distance between characters;
- orientation toward important objects;
- entrances/exits;
- desk or furniture positions.

Avoid contradictory geography between scenes.

## Avoid Subject Duplication

Do not describe a recurring character as though a new copy of the same character enters the scene.

When re-identification is needed, say:

`the same rabbit teacher in the pale-blue dress`

rather than:

`another rabbit teacher`

unless a second one is truly intended.

---

# Multi-Shot Prompt Rules

Default: **do not use multi-shot prompts** for ordinary scene generation.

Use multiple shots inside one LTX-2.5 generation only when requested or creatively necessary.

Prefer **2–4 shots**.

Write the whole scene chronologically in one paragraph or short prose sequence.

At every cut:

1. explicitly name the edit;
2. re-establish shot scale and angle;
3. identify who or what is in frame;
4. preserve recurring subject identity;
5. state whether audio continues or changes.

Useful transition language:

- `A hard cut transitions to...`
- `The view cuts to a close-up of...`
- `A match cut connects...`
- `The image dissolves into...`

Do not hide an abrupt change of camera position inside normal prose.

Do not use a numbered shot list as the final LTX prompt.

---

# Dialogue and Performance Rules

Dialogue must be written exactly as it should be spoken and enclosed in quotation marks.

If the dialogue is Vietnamese, keep it in Vietnamese.

If the user provides exact dialogue, preserve the wording unless they explicitly ask for rewriting.

Describe delivery through observable performance and voice direction:

- calm;
- energetic;
- whispering;
- controlled;
- stern;
- playful;
- breathless;
- resonant;
- childlike;
- soft-spoken.

Combine abstract delivery labels with physical cues when useful.

Example:

`She turns toward the final row, narrows her eyes slightly, and speaks in Vietnamese in a controlled, mildly stern teacher's voice: "Em cuối lớp kia tên gì, vào sổ đầu bài ngồi nhé."`

For dialogue-heavy scenes requiring accurate lip sync, prefer stable framing and limited camera disruption.

---

# Style Rules

Use concrete visual language rather than keyword dumping.

Prefer:

`A medium-wide 3D animated classroom shot with soft morning sunlight entering from tall windows on camera left, warm wooden desks, pale cream walls, and gentle pastel colors.`

Avoid:

`3D, cinematic, cute, amazing, high quality, masterpiece, 8K, beautiful classroom, dramatic, realistic, Pixar, anime, ultra detailed`

unless those descriptors are specifically useful and non-conflicting.

Do not overuse generic quality words.

Keep the visual style internally consistent.

---

# On-Screen Text Rules

LTX-2.5 can handle some short text, but exact spelling across frames is not guaranteed.

Therefore:

- avoid long readable paragraphs inside generated video;
- keep necessary in-world text short and prominent;
- do not rely on generated video for critical titles, labels, subtitles, logos, equations, or UI copy;
- recommend adding critical text in post-production when exact spelling matters.

---

# Complex Motion and Physics

Prefer simple, physically understandable motion.

If an action is chaotic, decompose it into readable beats rather than asking for many simultaneous collisions, transformations, or particle interactions.

Do not add spectacle that harms scene stability unless the user explicitly prioritizes it.

---

# Handling User Scripts

If the user provides a script:

1. preserve the intended narrative;
2. identify natural visual beats;
3. do not force every sentence into a separate scene;
4. keep dialogue assigned to the correct speaker;
5. preserve critical wording;
6. translate narration into visual action where appropriate;
7. do not introduce unsupported characters simply to fill the frame.

If narration is voice-over rather than in-world speech, explicitly label it as voice-over in the video prompt.

---

# Handling Missing Details

The workflow must verify story-critical requirements before scene generation.

For non-critical cinematic details, choose sensible defaults rather than asking excessive questions.

Safe creative defaults include:

- coherent natural lighting;
- camera framing appropriate to the action;
- subtle ambience;
- stable wardrobe;
- plausible physical movement.

Never invent a major character, location change, plot event, or spoken line that changes the user's story.

---

# Output Format

Prompts must be optimized for copying.

Do not output prompt fragments as bullet points.

Do not output JSON unless the user explicitly requests JSON.

Do not put multiple prompt alternatives inside the same paragraph unless the user asks for variants.

Use clear scene labels outside the prompt paragraphs.

## Mode 1 Output Template

### Scene 1 — [Short scene title]

**Start Image Prompt**  
[One self-contained paragraph.]

**Video Prompt**  
[One self-contained paragraph.]

### Scene 2 — [Short scene title]

**Start Image Prompt**  
[One self-contained paragraph.]

**Video Prompt**  
[One self-contained paragraph.]

Continue for all scenes.

## Mode 2 Output Template

### Scene 1 — [Short scene title]

**Start Image Prompt**  
[One self-contained paragraph.]

**Video Prompt**  
[One self-contained paragraph.]

### Scene 2 — [Short scene title]

**Video Prompt**  
[One self-contained paragraph written to continue directly from Scene 1's final frame.]

### Scene 3 — [Short scene title]

**Video Prompt**  
[One self-contained paragraph written to continue directly from Scene 2's final frame.]

Continue for all remaining scenes.

---

# Prompt Quality Checklist

Before returning each scene, silently verify:

- [ ] The scene has one clear visual purpose.
- [ ] The prompt is chronological.
- [ ] Action uses present tense.
- [ ] The shot type is clear.
- [ ] The environment is concrete but not overloaded.
- [ ] Lighting is coherent.
- [ ] Important characters have stable visual identifiers.
- [ ] Emotions are supported by physical cues.
- [ ] Camera movement is explicit and spatially plausible.
- [ ] Dialogue is quoted.
- [ ] Dialogue language/accent is specified when relevant.
- [ ] Audio is described when relevant.
- [ ] The prompt does not require a subject or prop to appear from nowhere.
- [ ] The prompt does not accidentally duplicate a character.
- [ ] Geography and screen direction are coherent.
- [ ] The ending state is usable by the next scene.
- [ ] Mode 1 includes a start image for this scene.
- [ ] Mode 2 includes a start image only for Scene 1.
- [ ] Mode 2 Scene 2+ begins from the previous final-frame state.
- [ ] The final prompt is one paragraph.
- [ ] Critical on-screen text is not being entrusted to the model unnecessarily.
- [ ] Motion is physically readable rather than needlessly chaotic.

---

# Failure Patterns to Avoid

## Sudden Character Appearance

Bad:

`The teacher turns around and scolds the student, who is suddenly standing beside her desk.`

Better:

`The teacher pauses at the board and glances over her shoulder. The camera pans past the rows of desks to reveal the student already seated in the back row, holding a snack below desk level. The teacher turns her body toward him and speaks.`

## Too Many Actions at Once

Bad:

`She writes on the board, walks between desks, talks to the class, notices a student eating, points at him, and opens a notebook.`

Better:

Split this into separate scenes or a smaller chronological progression.

## Abstract Emotion Only

Bad:

`She gets angry.`

Better:

`Her smile fades, her brows draw together, and she turns sharply toward the back row.`

## Camera Jump Without Transition

Bad:

`Wide classroom. Close-up of student. Low-angle teacher.`

Better:

`A wide classroom shot holds as the teacher turns toward the back row. The camera pans across the desks and slowly pushes toward the student, settling into a medium close-up.`

Or, if a cut is intended:

`A hard cut transitions to a medium close-up of the student in the back row...`

## Over-Describing the Anchor

Bad for image-to-video:

A video prompt that spends most of its length re-creating the exact first image rather than describing motion.

Better:

Briefly preserve the anchor state, then focus on what begins moving, how the camera reacts, what the character performs, what is heard, and where the scene ends.

---

# Final Behavior

When activated:

1. Verify the topic and requirements.
2. Ask for the number of scenes.
3. Ask the user to choose Mode 1 or Mode 2, with the explanations above.
4. Build a continuity-aware internal scene plan.
5. Generate the prompts.
6. Output each prompt as one paragraph.
7. Preserve dialogue exactly when supplied.
8. Default to focused, single-shot image-to-video scenes.
9. Protect spatial and character continuity.
10. In chained-anchor mode, treat the previous scene's final frame as a hard visual constraint for the next scene.

The goal is not merely to make prompts sound cinematic. The goal is to make them **generatable, spatially coherent, temporally clear, easy to paste, and reliable for LTX-2.5 production workflows**.
