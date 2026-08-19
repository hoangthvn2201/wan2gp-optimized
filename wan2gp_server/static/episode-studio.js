(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const state = { config: null, project: null, filter: "all", poll: null };
  const ACTIVE = new Set(["queued", "running"]);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { const body = await response.json(); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch (_) {}
      throw new Error(detail);
    }
    return response.json();
  }

  function toast(message, error = false) {
    const node = document.createElement("div");
    node.className = `toast${error ? " error" : ""}`;
    node.textContent = message;
    $("#toastRegion").append(node);
    setTimeout(() => node.remove(), 4500);
  }

  async function loadConfig() {
    state.config = await api("/v1/studio/config");
    $("#runtimeLabel").textContent = `${state.config.model_family} · ready`;
    $("#studioMode").innerHTML = state.config.allowed_modes.map(mode => `<option value="${mode}">${mode === "image-z" ? "Still images · Z-Image" : mode === "image-ltx" ? "Still images · LTX-2.5" : "Video clips · LTX-2.5"}</option>`).join("");
  }

  async function loadProjects() {
    const result = await api("/v1/projects");
    const list = $("#projectList");
    if (!result.projects.length) { list.innerHTML = '<p class="empty">No episode projects yet.</p>'; return; }
    list.innerHTML = result.projects.map(project => `<button class="project-item" data-project="${esc(project.id)}"><strong>${esc(project.title)}</strong><span>${esc(project.configuration.studio_mode)} · ${esc(project.status)} · ${project.scenes.length} scenes</span></button>`).join("");
  }

  function selectedRevision(scene) {
    const id = scene.candidate_revision || scene.accepted_revision;
    return scene.revisions.find(revision => revision.id === id) || null;
  }

  function overallProgress(project) {
    const stages = Object.values(project.stages);
    return Math.round(stages.reduce((sum, stage) => sum + (stage.status === "ready" ? 100 : Number(stage.progress || 0)), 0) / stages.length);
  }

  function renderWorkspace() {
    const project = state.project;
    if (!project) return;
    $("#importView").classList.add("is-hidden");
    $("#workspaceView").classList.remove("is-hidden");
    const progress = overallProgress(project);
    $("#workspaceHeader").innerHTML = `<div class="workspace-title"><div><p class="eyebrow">${esc(project.configuration.studio_mode)} · ${esc(project.status)}</p><h1>${esc(project.title)}</h1><p>${project.scenes.length} scenes · ${progress}% complete${project.error ? ` · ${esc(project.error)}` : ""}</p></div><div class="workspace-actions"><button class="resume" data-project-action="resume">Resume pipeline</button><button class="cancel" data-project-action="cancel">Cancel</button></div></div><div class="overall"><i style="width:${progress}%"></i></div>`;
    $("#stageRail").innerHTML = Object.entries(project.stages).map(([name, stage]) => `<div class="stage ${esc(stage.status)}"><strong>${esc(name)}</strong><small>${esc(stage.detail || stage.status)}${ACTIVE.has(stage.status) ? ` · ${stage.progress || 0}%` : ""}</small></div>`).join("");
    $("#sceneCount").textContent = `${project.scenes.length} planned scenes`;
    const narration = project.narration || {};
    $("#narrationPanel").innerHTML = narration.audio_url ? `<audio controls preload="metadata" src="${esc(narration.audio_url)}"></audio><p class="muted">${Number(narration.duration_seconds || 0).toFixed(1)} seconds · ${esc(narration.voice || project.configuration.voice)}</p>` : '<p class="muted">Narration has not started.</p>';
    const visible = project.scenes.filter(scene => state.filter === "all" || (state.filter === "review" && scene.candidate_revision) || (state.filter === "failed" && scene.status === "failed"));
    $("#sceneBoard").innerHTML = visible.map((scene, index) => sceneCard(scene, project.scenes.indexOf(scene))).join("") || '<p class="empty">No scenes match this filter.</p>';
    renderDelivery();
  }

  function sceneCard(scene, index) {
    const revision = selectedRevision(scene);
    const preview = revision ? (revision.media_type === "video" ? `<video src="${esc(revision.url)}" muted controls loop playsinline preload="metadata"></video>` : `<img src="${esc(revision.url)}" alt="${esc(scene.id)}">`) : '<div class="preview-empty">Waiting for generation</div>';
    const timing = scene.timing ? `${Number(scene.timing.start).toFixed(1)}s – ${(Number(scene.timing.start) + Number(scene.timing.duration)).toFixed(1)}s` : "Timing pending";
    return `<article class="scene-card" data-scene="${esc(scene.id)}"><div class="preview"><span class="scene-number">${String(index + 1).padStart(2,"0")}</span>${scene.candidate_revision ? '<span class="candidate-tag">NEW CANDIDATE</span>' : ""}${preview}</div><div class="scene-body"><div class="scene-top"><div><h3>${esc(scene.narrative_role.replaceAll("-"," "))}</h3><div class="scene-meta">${esc(scene.id)} · ${timing} · ${scene.accepted_revision || "no revision"}</div></div><span class="status ${esc(scene.status)}">${esc(scene.status)}</span></div><textarea data-prompt>${esc(revision?.prompt || scene.prompt)}</textarea>${ACTIVE.has(scene.status) ? `<div class="scene-progress"><i style="width:${scene.progress || 0}%"></i></div>` : ""}<p class="scene-detail">${esc(scene.detail || "Waiting")}</p><div class="scene-actions"><button class="regenerate" data-action="regenerate">Regenerate</button>${scene.candidate_revision ? `<button class="accept" data-action="accept" data-revision="${esc(scene.candidate_revision)}">Use this revision</button>` : ""}${revision ? `<button class="download" data-action="download">Download asset</button>` : ""}</div></div></article>`;
  }

  function renderDelivery() {
    const project = state.project;
    const final = project.final || {};
    if (!final.url) {
      $("#deliveryPanel").innerHTML = `<p class="eyebrow">FINAL CUT</p><h2>${project.status === "failed" ? "Production needs attention" : "Building the first cut"}</h2><p>${esc(project.stages.render.detail)}</p>`;
      return;
    }
    $("#deliveryPanel").innerHTML = `<p class="eyebrow">FINAL CUT ${final.stale ? "· STALE" : "· READY"}</p><h2>${final.stale ? "A newer scene is ready to rebuild" : "Your episode is ready"}</h2><p>${Number(final.duration_seconds || 0).toFixed(1)} seconds · revision ${final.revision}</p><video src="${esc(final.url)}" controls preload="metadata"></video><div class="delivery-actions"><a href="${esc(final.url)}" download>Download MP4</a><button data-project-action="render">Rebuild final</button></div>`;
  }

  async function openProject(id) {
    state.project = await api(`/v1/projects/${encodeURIComponent(id)}`);
    renderWorkspace();
    schedulePoll();
  }

  function schedulePoll() {
    clearTimeout(state.poll);
    if (!state.project) return;
    const active = ACTIVE.has(state.project.status) || Object.values(state.project.stages).some(stage => ACTIVE.has(stage.status)) || state.project.scenes.some(scene => ACTIVE.has(scene.status));
    state.poll = setTimeout(async () => {
      try { await openProject(state.project.id); } catch (error) { toast(error.message, true); }
    }, active ? 1800 : 6000);
  }

  $("#scenePlan").addEventListener("change", async event => {
    const file = event.target.files[0]; $("#scenePlanName").textContent = file?.name || "Choose scene-plan.json";
    if (!file) return;
    try { const plan = JSON.parse(await file.text()); $("#sourceSummary").textContent = `${plan.title || "Untitled"} · ${plan.scenes?.length || 0} scenes · ${plan.aspect_ratio || "ratio unknown"}`; $("#sourceSummary").classList.remove("is-hidden"); } catch (_) { $("#sourceSummary").textContent = "Scene plan JSON could not be parsed"; $("#sourceSummary").classList.remove("is-hidden"); }
  });
  $("#voiceover").addEventListener("change", event => { const file = event.target.files[0]; $("#voiceoverName").textContent = file?.name || "Choose voiceover-source.txt"; });
  $("#projectForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $(".create-button"); button.disabled = true; button.firstElementChild.textContent = "Creating project…";
    $("#formError").classList.add("is-hidden");
    try { const project = await api("/v1/projects", { method:"POST", body:new FormData(event.target) }); state.project = project; toast("Episode queued."); renderWorkspace(); schedulePoll(); } catch (error) { $("#formError").textContent = error.message; $("#formError").classList.remove("is-hidden"); } finally { button.disabled = false; button.firstElementChild.textContent = "Create episode"; }
  });
  $("#projectList").addEventListener("click", event => { const button = event.target.closest("[data-project]"); if (button) openProject(button.dataset.project).catch(error => toast(error.message,true)); });
  $("#refreshProjects").addEventListener("click", () => loadProjects().catch(error => toast(error.message,true)));
  $("#homeButton").addEventListener("click", () => { clearTimeout(state.poll); state.project = null; $("#workspaceView").classList.add("is-hidden"); $("#importView").classList.remove("is-hidden"); loadProjects().catch(()=>{}); });
  $(".filters").addEventListener("click", event => { const button = event.target.closest("[data-filter]"); if (!button) return; state.filter = button.dataset.filter; document.querySelectorAll("[data-filter]").forEach(item => item.classList.toggle("active", item === button)); renderWorkspace(); });
  $("#sceneBoard").addEventListener("click", async event => {
    const button = event.target.closest("[data-action]"); if (!button) return;
    const card = button.closest("[data-scene]"); const sceneId = card.dataset.scene; const scene = state.project.scenes.find(item => item.id === sceneId);
    try {
      if (button.dataset.action === "regenerate") { await api(`/v1/projects/${state.project.id}/scenes/${sceneId}/regenerate`, {method:"POST", body:JSON.stringify({prompt:$("[data-prompt]",card).value})}); toast(`${sceneId} regeneration queued.`); await openProject(state.project.id); }
      if (button.dataset.action === "accept") { state.project = await api(`/v1/projects/${state.project.id}/scenes/${sceneId}/accept/${button.dataset.revision}`, {method:"POST",body:"{}"}); toast("Revision accepted. Final cut is now stale."); renderWorkspace(); }
      if (button.dataset.action === "download") { const revision = selectedRevision(scene); const link=document.createElement("a"); link.href=revision.url; link.download=revision.path.split("/").pop(); link.click(); }
    } catch (error) { toast(error.message,true); }
  });
  document.addEventListener("click", async event => {
    const button = event.target.closest("[data-project-action]"); if (!button || !state.project) return;
    try { const action=button.dataset.projectAction; if(action==="resume") await api(`/v1/projects/${state.project.id}/run`,{method:"POST",body:"{}"}); if(action==="cancel") await api(`/v1/projects/${state.project.id}/cancel`,{method:"POST",body:"{}"}); if(action==="render") await api(`/v1/projects/${state.project.id}/render`,{method:"POST",body:"{}"}); toast(`${action} requested.`); await openProject(state.project.id); } catch(error){toast(error.message,true);}
  });

  Promise.all([loadConfig(), loadProjects()]).catch(error => { $("#runtimeLabel").textContent = "Server unavailable"; toast(error.message,true); });
})();
