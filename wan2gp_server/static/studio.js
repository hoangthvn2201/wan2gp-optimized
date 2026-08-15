(() => {
  "use strict";

  const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
  const MODES = {
    text: { label: "Plain prompt", short: "Text", icon: "text" },
    image: { label: "Start image + prompt", short: "Image", icon: "image" },
    video: { label: "Video anchor + prompt", short: "Video", icon: "video" },
    last: { label: "Last video + prompt", short: "Last clip", icon: "last" },
  };
  const RESOLUTIONS = {
    "16:9": { draft: [768, 448], standard: [1280, 704], max: [1920, 1088] },
    "9:16": { draft: [448, 768], standard: [704, 1280], max: [1088, 1920] },
    "1:1": { draft: [704, 704], standard: [1024, 1024], max: [1472, 1472] },
    "4:3": { draft: [768, 576], standard: [1024, 768], max: [1536, 1152] },
    "21:9": { draft: [896, 384], standard: [1344, 576], max: [1792, 768] },
  };
  const DURATIONS = Array.from({ length: 30 }, (_, index) => index + 1);

  const state = {
    scenes: [],
    assembly: null,
    connected: false,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const sceneList = $("#sceneList");

  function uid() {
    return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function newScene(mode = "image") {
    return {
      id: uid(), mode, prompt: "", imagePrompt: "", quality: "standard", ratio: "16:9", duration: 8,
      file: null, inputUrl: null, asset: null, job: null, outputUrl: null,
      status: "idle", progress: 0, detail: "Ready for direction", error: null,
      startImageJob: null, startImageFile: null, imageStatus: "idle",
      imageProgress: 0, imageDetail: "Describe or upload a start frame", imageError: null,
    };
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>'"]/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[char]);
  }

  function icon(name) {
    const paths = {
      text: '<path d="M5 6h14M9 6v12m6-12v12M7 18h10"/>',
      image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m4 17 5-5 4 4 2-2 5 4"/>',
      video: '<rect x="3" y="5" width="14" height="14" rx="2"/><path d="m17 10 4-2v8l-4-2"/>',
      last: '<path d="M5 6v12M8 12h11m0 0-4-4m4 4-4 4"/>',
      generate: '<path d="m12 3 1.3 4.7L18 9l-4.7 1.3L12 15l-1.3-4.7L6 9l4.7-1.3L12 3Z"/><path d="m18 15 .7 2.3L21 18l-2.3.7L18 21l-.7-2.3L15 18l2.3-.7L18 15Z"/>',
      download: '<path d="M12 3v12m0 0 5-5m-5 5-5-5M5 20h14"/>',
      empty: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m8 9 8 6m0-6-8 6"/>',
      upload: '<path d="M12 16V4m0 0-4 4m4-4 4 4M5 14v5h14v-5"/>',
    };
    return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.empty}</svg>`;
  }

  function sceneStatus(scene) {
    if (["queued", "running"].includes(scene.imageStatus)) return ["Creating frame", "is-running"];
    if (scene.status === "succeeded") return ["Ready", "is-ready"];
    if (["uploading", "queued", "running"].includes(scene.status)) return ["Generating", "is-running"];
    if (["failed", "cancelled"].includes(scene.status)) return [scene.status === "failed" ? "Needs attention" : "Cancelled", "is-error"];
    return ["Draft", ""];
  }

  function isSceneBusy(scene) {
    return ["uploading", "queued", "running"].includes(scene.status) ||
      ["queued", "running"].includes(scene.imageStatus);
  }

  function previewMarkup(scene) {
    const source = scene.outputUrl || scene.inputUrl;
    const isVideo = Boolean(scene.outputUrl) || scene.file?.type?.startsWith("video/");
    if (source && isVideo) return `<video src="${esc(source)}" controls playsinline preload="metadata"></video>`;
    if (source) return `<img src="${esc(source)}" alt="Scene input preview">`;
    return `<div class="preview-empty">${icon("empty")}<span>Your generated scene will appear here</span></div>`;
  }

  function mediaInputMarkup(scene, index) {
    if (scene.mode === "text") return "";
    if (scene.mode === "last") {
      const previous = state.scenes[index - 1];
      const available = previous?.status === "succeeded";
      return `<div class="last-source">${icon("last")}<div><strong>${available ? `Scene ${String(index).padStart(2, "0")} linked` : "Previous scene required"}</strong><small>${available ? "Uses the exact preceding clip as continuation context" : "Generate the scene above before continuing it"}</small></div></div>`;
    }
    if (scene.mode === "image") return "";
    const wantsVideo = scene.mode === "video";
    const typeLabel = "video anchor";
    const emptyHint = "MP4, MOV or WebM";
    return `<label class="media-input">
      <input type="file" data-field="file" accept="${wantsVideo ? "video/mp4,video/quicktime,video/webm,video/x-matroska,video/x-msvideo" : "image/*"}">
      <span>${icon("upload")}<strong>${scene.file ? esc(scene.file.name) : `Add ${typeLabel}`}</strong><small>${scene.file ? "Choose another file" : emptyHint}</small></span>
    </label>`;
  }

  function startImageMarkup(scene) {
    const busy = ["queued", "running"].includes(scene.imageStatus);
    const hasImage = Boolean(scene.inputUrl);
    const generated = Boolean(scene.startImageFile) && !scene.file;
    const failed = scene.imageStatus === "failed";
    const stateLabel = busy ? "Generating" : failed ? "Generation failed" : generated ? "AI frame ready" : scene.file ? "Upload ready" : "Optional";
    const progressVisible = busy || scene.imageStatus === "failed";
    return `<section class="start-image-builder" aria-label="Start image creator">
      <div class="start-image-head">
        <div><span>START FRAME</span><strong>Create with LTX-2.5 or upload your own</strong></div>
        <em class="start-image-state ${busy ? "is-running" : failed ? "is-error" : hasImage ? "is-ready" : ""}">${esc(stateLabel)}</em>
      </div>
      <div class="start-image-grid">
        <label class="start-image-source ${hasImage ? "has-image" : ""}">
          <input type="file" data-field="file" accept="image/*" ${busy ? "disabled" : ""}>
          ${hasImage ? `<img src="${esc(scene.inputUrl)}" alt="Selected start frame">` : `<span>${icon("upload")}<strong>Upload start image</strong><small>PNG, JPG or WebP</small></span>`}
          ${hasImage ? `<b>${icon("upload")}Replace image</b>` : ""}
        </label>
        <div class="start-image-compose">
          <div class="prompt-wrap image-prompt-wrap">
            <textarea data-field="imagePrompt" maxlength="3000" placeholder="Describe the opening frame: subject, composition, lighting, lens, and style…" ${busy ? "disabled" : ""}>${esc(scene.imagePrompt)}</textarea>
            <span>START IMAGE PROMPT</span>
          </div>
          <div class="start-image-actions">
            <button class="button button-soft" data-action="generate-image" type="button" ${busy ? "disabled" : ""}>${icon("generate")}${generated ? "Regenerate start image" : "Generate start image"}</button>
            <small>Controls the still image only. If blank, Generate scene uses the director's prompt.</small>
          </div>
        </div>
      </div>
      <div class="image-progress ${progressVisible ? "" : "is-hidden"}">
        <div class="progress-track"><i style="width:${scene.imageProgress}%"></i></div>
        <p><span>${esc(scene.imageError || scene.imageDetail)}</span><strong>${scene.imageProgress}%</strong></p>
      </div>
    </section>`;
  }

  function renderScene(scene, index) {
    const [statusLabel, statusClass] = sceneStatus(scene);
    const busy = isSceneBusy(scene);
    const canDelete = !busy;
    const inputMarkup = mediaInputMarkup(scene, index);
    const progressVisible = ["uploading", "queued", "running", "failed"].includes(scene.status);
    return `<article class="scene-card" data-scene-card="${scene.id}">
      <div class="scene-preview">
        ${previewMarkup(scene)}
        <span class="scene-number">SCENE ${String(index + 1).padStart(2, "0")}</span>
        <span class="preview-status ${statusClass}"><i></i><b>${statusLabel}</b></span>
      </div>
      <div class="scene-editor">
        <div class="scene-editor-head">
          <div><h3>Scene ${String(index + 1).padStart(2, "0")}</h3><p>${esc(MODES[scene.mode].label)}</p></div>
          <button class="delete-button" data-action="delete" type="button" ${canDelete ? "" : "disabled"} aria-label="Delete scene">Delete</button>
        </div>
        <div class="mode-grid" role="group" aria-label="Generation mode">
          ${Object.entries(MODES).map(([value, mode]) => `<button class="mode-button ${scene.mode === value ? "is-active" : ""}" data-action="mode" data-value="${value}" type="button" ${busy || value === "last" && index === 0 ? "disabled" : ""}>${icon(mode.icon)}${mode.short}</button>`).join("")}
        </div>
        ${scene.mode === "image" ? startImageMarkup(scene) : ""}
        <div class="input-row ${scene.mode === "text" || scene.mode === "image" ? "text-only-input" : ""}">
          ${inputMarkup}
          <div class="prompt-wrap">
            <textarea data-field="prompt" maxlength="3000" placeholder="Describe the action, camera movement, atmosphere, sound, and dialogue…">${esc(scene.prompt)}</textarea>
            <span>DIRECTOR'S PROMPT</span>
          </div>
        </div>
        <div class="settings-row">
          <div class="select-field"><label>Quality</label><select data-field="quality">
            <option value="draft" ${scene.quality === "draft" ? "selected" : ""}>Draft · fast</option>
            <option value="standard" ${scene.quality === "standard" ? "selected" : ""}>High · 720p</option>
            <option value="max" ${scene.quality === "max" ? "selected" : ""}>Max · 1080p</option>
          </select></div>
          <div class="select-field"><label>Video ratio</label><select data-field="ratio">
            ${Object.keys(RESOLUTIONS).map(ratio => `<option value="${ratio}" ${scene.ratio === ratio ? "selected" : ""}>${ratio}${ratio === "16:9" ? " · Landscape" : ratio === "9:16" ? " · Portrait" : ""}</option>`).join("")}
          </select></div>
          <div class="select-field"><label>Length</label><select data-field="duration">
            ${DURATIONS.map(value => `<option value="${value}" ${scene.duration === value ? "selected" : ""}>${value} second${value === 1 ? "" : "s"}</option>`).join("")}
          </select></div>
        </div>
        <div class="scene-progress ${progressVisible ? "" : "is-hidden"}">
          <div class="progress-track"><i style="width:${scene.progress}%"></i></div>
          <p><span>${esc(scene.error || scene.detail)}</span><strong>${scene.progress}%</strong></p>
        </div>
        <div class="scene-actions">
          <button class="button ${scene.status === "succeeded" ? "button-soft" : "button-coral"}" data-action="generate" type="button" ${busy ? "disabled" : ""}>${icon("generate")}${scene.status === "succeeded" ? "Regenerate" : "Generate scene"}</button>
          <button class="button button-ghost ${scene.status === "succeeded" ? "" : "is-hidden"}" data-action="download" type="button">${icon("download")}Download</button>
          ${busy && scene.job ? '<button class="button button-ghost" data-action="cancel" type="button">Cancel</button>' : ""}
        </div>
      </div>
    </article>`;
  }

  function renderAll() {
    sceneList.innerHTML = state.scenes.map((scene, index) => {
      const card = renderScene(scene, index);
      if (index === state.scenes.length - 1) return card;
      const next = state.scenes[index + 1];
      const enabled = scene.status === "succeeded" && next.status === "succeeded";
      return `${card}<div class="scene-connector"><button class="join-button" data-action="join" data-left="${scene.id}" data-right="${next.id}" type="button" ${enabled ? "" : "disabled"}>Join these scenes</button></div>`;
    }).join("");
    updateDelivery();
    updateGlobalProgress();
  }

  function findScene(id) {
    return state.scenes.find(scene => scene.id === id);
  }

  function sceneFromElement(element) {
    return findScene(element.closest("[data-scene-card]")?.dataset.sceneCard);
  }

  function syncProgress(scene) {
    const card = document.querySelector(`[data-scene-card="${CSS.escape(scene.id)}"]`);
    if (!card) return;
    const progress = $(".scene-progress", card);
    progress?.classList.remove("is-hidden");
    const bar = $(".scene-progress .progress-track i", card);
    const detail = $(".scene-progress p span", card);
    const percent = $(".scene-progress p strong", card);
    if (bar) bar.style.width = `${scene.progress}%`;
    if (detail) detail.textContent = scene.error || scene.detail;
    if (percent) percent.textContent = `${scene.progress}%`;
    const status = $(".preview-status", card);
    if (status) {
      status.className = "preview-status is-running";
      const label = $("b", status);
      if (label) label.textContent = "Generating";
    }
    updateGlobalProgress();
  }

  function syncImageProgress(scene) {
    const card = document.querySelector(`[data-scene-card="${CSS.escape(scene.id)}"]`);
    if (!card) return;
    const progress = $(".image-progress", card);
    progress?.classList.remove("is-hidden");
    const bar = $(".image-progress .progress-track i", card);
    const detail = $(".image-progress p span", card);
    const percent = $(".image-progress p strong", card);
    if (bar) bar.style.width = `${scene.imageProgress}%`;
    if (detail) detail.textContent = scene.imageError || scene.imageDetail;
    if (percent) percent.textContent = `${scene.imageProgress}%`;
    updateGlobalProgress();
  }

  function updateGlobalProgress() {
    const active = state.scenes.find(scene => ["uploading", "queued", "running"].includes(scene.status));
    const activeImage = state.scenes.find(scene => ["queued", "running"].includes(scene.imageStatus));
    const panel = $("#globalProgress");
    if (!active && !activeImage && !state.assembly?.active) {
      panel.classList.add("is-hidden");
      return;
    }
    const operation = active || activeImage || state.assembly;
    const operationScene = active || activeImage;
    const index = operationScene ? state.scenes.indexOf(operationScene) + 1 : null;
    panel.classList.remove("is-hidden");
    $("#globalProgressLabel").textContent = active
      ? `Generating scene ${String(index).padStart(2, "0")}`
      : activeImage
        ? `Creating start image for scene ${String(index).padStart(2, "0")}`
        : "Assembling final cut";
    const progress = activeImage && !active ? operation.imageProgress : operation.progress;
    const detail = activeImage && !active ? operation.imageDetail : operation.detail;
    $("#globalProgressPercent").textContent = `${progress || 0}%`;
    $("#globalProgressBar").style.width = `${progress || 0}%`;
    $("#globalProgressDetail").textContent = detail || "Preparing…";
  }

  function updateDelivery() {
    const completed = state.scenes.filter(scene => scene.status === "succeeded" && scene.job);
    const button = $("#concatAll");
    button.disabled = completed.length < 2 || Boolean(state.assembly?.active);
    if (!state.assembly) return;
    $("#deliveryLabel").textContent = state.assembly.label || "Final cut";
    $("#deliveryMeta").textContent = state.assembly.detail || "Preparing assembly…";
    $("#downloadAssembly").classList.toggle("is-hidden", state.assembly.status !== "succeeded");
  }

  function toast(message, error = false) {
    const node = document.createElement("div");
    node.className = `toast${error ? " is-error" : ""}`;
    node.textContent = message;
    $("#toastRegion").append(node);
    setTimeout(() => node.remove(), 4500);
  }

  function extractError(body, fallback) {
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map(item => item.msg || JSON.stringify(item)).join("; ");
    return fallback;
  }

  async function apiFetch(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      let body = null;
      try { body = await response.json(); } catch (_) { /* response is not JSON */ }
      throw new Error(extractError(body, `${response.status} ${response.statusText}`));
    }
    return response;
  }

  async function postJson(path, body) {
    const response = await apiFetch(path, { method: "POST", body: JSON.stringify(body) });
    return response.json();
  }

  async function uploadAsset(file) {
    const data = new FormData();
    data.append("file", file, file.name);
    const response = await apiFetch("/v1/assets", { method: "POST", body: data });
    return response.json();
  }

  async function pollJob(jobId, scene, label, offset = 0, span = 100) {
    while (true) {
      const response = await apiFetch(`/v1/jobs/${encodeURIComponent(jobId)}`);
      const job = await response.json();
      const raw = job.progress?.percent || 0;
      scene.status = job.status;
      scene.progress = Math.min(99, Math.round(offset + raw * span / 100));
      scene.detail = `${label} · ${job.progress?.status || job.progress?.phase || job.status}`;
      syncProgress(scene);
      if (TERMINAL.has(job.status)) {
        if (job.status !== "succeeded") throw new Error(job.error || `Job ${job.status}`);
        return job;
      }
      await new Promise(resolve => setTimeout(resolve, 1800));
    }
  }

  async function pollStartImageJob(jobId, scene, { sceneOffset = null, sceneSpan = 0 } = {}) {
    while (true) {
      const response = await apiFetch(`/v1/jobs/${encodeURIComponent(jobId)}`);
      const job = await response.json();
      const raw = job.progress?.percent || 0;
      scene.imageStatus = job.status;
      scene.imageProgress = Math.min(99, Math.round(raw));
      scene.imageDetail = `Creating start frame · ${job.progress?.status || job.progress?.phase || job.status}`;
      if (sceneOffset !== null) {
        scene.progress = Math.min(99, Math.round(sceneOffset + raw * sceneSpan / 100));
        scene.detail = scene.imageDetail;
        syncProgress(scene);
      }
      syncImageProgress(scene);
      if (TERMINAL.has(job.status)) {
        if (job.status !== "succeeded") throw new Error(job.error || `Start-image job ${job.status}`);
        return job;
      }
      await new Promise(resolve => setTimeout(resolve, 1800));
    }
  }

  async function loadStartImagePreview(scene, job) {
    const file = job?.files?.find(item => item.media_type === "image") || job?.files?.[0];
    if (!file) throw new Error("LTX-2.5 produced no start image.");
    const response = await apiFetch(file.url);
    const blob = await response.blob();
    if (scene.inputUrl) URL.revokeObjectURL(scene.inputUrl);
    scene.inputUrl = URL.createObjectURL(blob);
    scene.file = null;
    scene.asset = null;
    scene.startImageFile = file;
    return file;
  }

  async function generateStartImage(scene, { forScene = false, width, height } = {}) {
    const imagePrompt = (scene.imagePrompt || scene.prompt).trim();
    if (!imagePrompt) {
      const error = new Error("Add a start-image prompt or director's prompt first.");
      if (!forScene) toast(error.message, true);
      throw error;
    }
    if (!width || !height) [width, height] = RESOLUTIONS[scene.ratio][scene.quality];

    scene.imageError = null;
    scene.imageStatus = "queued";
    scene.imageProgress = 1;
    scene.imageDetail = "Sending start-image prompt to LTX-2.5";
    scene.startImageJob = null;
    if (!forScene) renderAll();

    try {
      let job = await postJson("/v1/generations/text-to-image", {
        prompt: imagePrompt, model: "ltx25-distilled-image", width, height, wait: false,
      });
      scene.startImageJob = job;
      job = await pollStartImageJob(job.id, scene, forScene ? { sceneOffset: 3, sceneSpan: 31 } : {});
      scene.startImageJob = job;
      const file = await loadStartImagePreview(scene, job);
      scene.imageStatus = "succeeded";
      scene.imageProgress = 100;
      scene.imageDetail = "Start image ready";
      if (!forScene) {
        toast("Start image is ready.");
        renderAll();
      }
      return { image_path: file.path };
    } catch (error) {
      scene.imageStatus = scene.imageStatus === "cancelled" ? "cancelled" : "failed";
      scene.imageError = error.message || String(error);
      scene.imageDetail = scene.imageError;
      if (!forScene) {
        toast(scene.imageError, true);
        renderAll();
      }
      throw error;
    }
  }

  async function loadOutputPreview(scene) {
    const file = scene.job?.files?.find(item => item.media_type === "video") || scene.job?.files?.[0];
    if (!file) return;
    const response = await apiFetch(file.url);
    const blob = await response.blob();
    if (scene.outputUrl) URL.revokeObjectURL(scene.outputUrl);
    scene.outputUrl = URL.createObjectURL(blob);
  }

  async function generateScene(scene) {
    if (!scene.prompt.trim()) {
      toast("Add a director's prompt before generating.", true);
      return;
    }
    const index = state.scenes.indexOf(scene);
    const [width, height] = RESOLUTIONS[scene.ratio][scene.quality];
    scene.error = null;
    scene.status = "uploading";
    scene.progress = 2;
    scene.detail = "Preparing scene inputs";
    scene.job = null;
    if (scene.outputUrl) { URL.revokeObjectURL(scene.outputUrl); scene.outputUrl = null; }
    renderAll();

    try {
      let job;
      const common = {
        prompt: scene.prompt.trim(), width, height,
        duration_seconds: Number(scene.duration), wait: false,
      };

      if (scene.mode === "text") {
        job = await postJson("/v1/generations/text-to-video", { ...common, model: "ltx25-distilled" });
        scene.job = job;
        job = await pollJob(job.id, scene, "Rendering video", 4, 94);
      } else if (scene.mode === "image") {
        let imageSource;
        let createdAutomatically = false;
        if (scene.file) {
          scene.detail = "Uploading start image";
          syncProgress(scene);
          const asset = await uploadAsset(scene.file);
          if (asset.media_type !== "image") throw new Error("Start-image mode requires an image file.");
          scene.asset = asset;
          imageSource = { image_asset_id: asset.asset_id };
          scene.progress = 8;
        } else if (scene.startImageFile) {
          scene.detail = "Using the generated start image";
          scene.progress = 8;
          syncProgress(scene);
          imageSource = { image_path: scene.startImageFile.path };
        } else {
          scene.detail = "No image supplied · creating a start frame with LTX-2.5";
          syncProgress(scene);
          createdAutomatically = true;
          imageSource = await generateStartImage(scene, { forScene: true, width, height });
        }
        job = await postJson("/v1/generations/image-to-video", {
          ...common, ...imageSource, model: "ltx25-distilled-i2v",
        });
        scene.job = job;
        job = await pollJob(job.id, scene, "Animating scene", createdAutomatically ? 35 : 9, createdAutomatically ? 63 : 89);
      } else if (scene.mode === "video") {
        if (!scene.file) throw new Error("Choose a video anchor for this mode.");
        scene.detail = "Uploading video anchor";
        syncProgress(scene);
        const asset = await uploadAsset(scene.file);
        if (asset.media_type !== "video") throw new Error("Video-anchor mode requires a video file.");
        scene.asset = asset;
        job = await postJson("/v1/generations/video-to-video", {
          ...common, model: "ltx25-distilled", continuation_mode: "source",
          video_asset_id: asset.asset_id,
        });
        scene.job = job;
        job = await pollJob(job.id, scene, "Continuing video anchor", 8, 90);
      } else {
        const previous = state.scenes[index - 1];
        if (!previous?.job || previous.status !== "succeeded") {
          throw new Error("Generate the preceding scene before using Last video mode.");
        }
        job = await postJson("/v1/generations/video-to-video", {
          ...common, model: "ltx25-distilled", continuation_mode: "last",
          source_job_id: previous.job.id,
        });
        scene.job = job;
        job = await pollJob(job.id, scene, "Continuing previous scene", 4, 94);
      }

      scene.job = job;
      scene.status = "succeeded";
      scene.progress = 100;
      scene.detail = "Scene ready";
      await loadOutputPreview(scene);
      toast(`Scene ${index + 1} is ready.`);
    } catch (error) {
      scene.status = scene.status === "cancelled" ? "cancelled" : "failed";
      scene.error = error.message || String(error);
      scene.detail = scene.error;
      toast(scene.error, true);
    }
    renderAll();
  }

  async function cancelScene(scene) {
    if (!scene.job?.id) return;
    try {
      await postJson(`/v1/jobs/${encodeURIComponent(scene.job.id)}/cancel`, {});
      scene.status = "cancelled";
      scene.detail = "Generation cancelled";
      renderAll();
    } catch (error) { toast(error.message, true); }
  }

  async function downloadJobFile(job, suggestedName) {
    const file = job?.files?.find(item => item.media_type === "video") || job?.files?.[0];
    if (!file) throw new Error("No downloadable output is available.");
    const response = await apiFetch(file.url);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = suggestedName || file.filename || "frameflow-output.mp4";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function assemble(jobIds, label) {
    state.assembly = { active: true, status: "queued", progress: 0, label, detail: "Assembly queued", job: null };
    renderAll();
    try {
      let job = await postJson("/v1/concatenations", { job_ids: jobIds, fps: 24 });
      state.assembly.job = job;
      while (!TERMINAL.has(job.status)) {
        await new Promise(resolve => setTimeout(resolve, 1400));
        const response = await apiFetch(`/v1/jobs/${encodeURIComponent(job.id)}`);
        job = await response.json();
        state.assembly.job = job;
        state.assembly.status = job.status;
        state.assembly.progress = job.progress?.percent || 0;
        state.assembly.detail = job.progress?.status || job.status;
        updateDelivery();
        updateGlobalProgress();
      }
      if (job.status !== "succeeded") throw new Error(job.error || `Assembly ${job.status}`);
      state.assembly.active = false;
      state.assembly.status = "succeeded";
      state.assembly.progress = 100;
      state.assembly.detail = `${jobIds.length} scenes · MP4 with audio · ready to download`;
      toast(`${label} is ready.`);
    } catch (error) {
      state.assembly.active = false;
      state.assembly.status = "failed";
      state.assembly.detail = error.message || String(error);
      toast(state.assembly.detail, true);
    }
    renderAll();
  }

  sceneList.addEventListener("input", event => {
    const scene = sceneFromElement(event.target);
    if (!scene) return;
    const field = event.target.dataset.field;
    if (field === "prompt") scene.prompt = event.target.value;
    if (field === "imagePrompt") scene.imagePrompt = event.target.value;
  });

  sceneList.addEventListener("change", event => {
    const scene = sceneFromElement(event.target);
    if (!scene) return;
    const field = event.target.dataset.field;
    if (field === "file") {
      const file = event.target.files?.[0] || null;
      if (scene.inputUrl) URL.revokeObjectURL(scene.inputUrl);
      scene.file = file;
      scene.inputUrl = file ? URL.createObjectURL(file) : null;
      scene.asset = null;
      scene.startImageJob = null;
      scene.startImageFile = null;
      scene.imageStatus = "idle";
      scene.imageProgress = 0;
      scene.imageDetail = file ? "Uploaded start image ready" : "Describe or upload a start frame";
      scene.imageError = null;
      renderAll();
    } else if (field === "quality" || field === "ratio") {
      scene[field] = event.target.value;
    } else if (field === "duration") {
      scene.duration = Number(event.target.value);
    }
  });

  sceneList.addEventListener("click", event => {
    const actionButton = event.target.closest("[data-action]");
    if (!actionButton) return;
    const action = actionButton.dataset.action;
    if (action === "join") {
      const left = findScene(actionButton.dataset.left);
      const right = findScene(actionButton.dataset.right);
      if (left?.job && right?.job) assemble([left.job.id, right.job.id], "Joined scenes");
      return;
    }
    const scene = sceneFromElement(actionButton);
    if (!scene) return;
    if (action === "mode") {
      scene.mode = actionButton.dataset.value;
      scene.file = null;
      scene.asset = null;
      if (scene.inputUrl) URL.revokeObjectURL(scene.inputUrl);
      scene.inputUrl = null;
      scene.startImageJob = null;
      scene.startImageFile = null;
      scene.imageStatus = "idle";
      scene.imageProgress = 0;
      scene.imageDetail = "Describe or upload a start frame";
      scene.imageError = null;
      renderAll();
    } else if (action === "generate-image") {
      generateStartImage(scene).catch(() => { /* surfaced in the start-image panel */ });
    } else if (action === "generate") {
      generateScene(scene);
    } else if (action === "download") {
      const index = state.scenes.indexOf(scene) + 1;
      downloadJobFile(scene.job, `frameflow-scene-${String(index).padStart(2, "0")}.mp4`).catch(error => toast(error.message, true));
    } else if (action === "cancel") {
      cancelScene(scene);
    } else if (action === "delete") {
      if (["queued", "running"].includes(scene.status) && scene.job) cancelScene(scene);
      if (["queued", "running"].includes(scene.imageStatus) && scene.startImageJob) {
        postJson(`/v1/jobs/${encodeURIComponent(scene.startImageJob.id)}/cancel`, {}).catch(() => {});
      }
      if (scene.inputUrl) URL.revokeObjectURL(scene.inputUrl);
      if (scene.outputUrl) URL.revokeObjectURL(scene.outputUrl);
      state.scenes = state.scenes.filter(item => item !== scene);
      renderAll();
    }
  });

  $("#addScene").addEventListener("click", () => {
    state.scenes.push(newScene("image"));
    renderAll();
    document.querySelector(".scene-card:last-of-type")?.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  $("#concatAll").addEventListener("click", () => {
    const completed = state.scenes.filter(scene => scene.status === "succeeded" && scene.job);
    assemble(completed.map(scene => scene.job.id), "Final cut");
  });

  $("#downloadAssembly").addEventListener("click", () => {
    downloadJobFile(state.assembly?.job, "frameflow-final-cut.mp4").catch(error => toast(error.message, true));
  });

  async function checkConnection() {
    const status = $("#serverStatus");
    status.className = "server-pill is-checking";
    status.innerHTML = "<span></span>Connecting";
    try {
      await apiFetch("/v1/models?task=t2v");
      state.connected = true;
      status.className = "server-pill";
      status.innerHTML = "<span></span>GPU server online";
    } catch (error) {
      state.connected = false;
      status.className = "server-pill is-offline";
      status.innerHTML = "<span></span>Server unavailable";
    }
  }
  state.scenes.push(newScene("image"));
  renderAll();
  checkConnection();
})();
