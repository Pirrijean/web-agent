"use strict";
// Consola mínima de web-agent. Sin dependencias ni secretos embebidos:
// el token lo introduce el usuario y vive solo en sessionStorage.
(function () {
  const TOKEN_KEY = "wa.token";
  const TASKS_KEY = "wa.tasks";
  const POLL_MS = 1500;
  const FINAL = new Set(["terminada", "fallida"]);
  const LABELS = { en_curso: "En curso", terminada: "Terminada", fallida: "Fallida" };
  const INTERNAL = {
    accepted: "Admitida", queued: "En cola", running: "En ejecución", succeeded_verified: "Completada y verificada",
    failed: "Error", blocked: "Bloqueada por la política", needs_human_review: "Requiere revisión humana",
    timed_out: "Plazo agotado", cancelled: "Cancelada", uncertain: "Resultado incierto",
  };
  const VERIF = { verificado: "Verificado", no_verificado: "No verificado", incierto: "Incierto" };

  const $ = (id) => document.getElementById(id);
  const store = {
    get token() { return sessionStorage.getItem(TOKEN_KEY) || ""; },
    set token(v) { v ? sessionStorage.setItem(TOKEN_KEY, v) : sessionStorage.removeItem(TOKEN_KEY); },
    get tasks() {
      try { return JSON.parse(sessionStorage.getItem(TASKS_KEY) || "[]"); } catch { return []; }
    },
    set tasks(v) { sessionStorage.setItem(TASKS_KEY, JSON.stringify(v.slice(0, 50))); },
  };

  // ---------------------------------------------------------------- API
  async function api(path, options = {}) {
    const headers = Object.assign({ Authorization: "Bearer " + store.token }, options.headers || {});
    const res = await fetch(path, Object.assign({}, options, { headers, cache: "no-store" }));
    let body = null;
    try { body = await res.json(); } catch { /* sin cuerpo JSON */ }
    return { status: res.status, ok: res.ok, body };
  }

  // ---------------------------------------------------------------- Estado del servicio
  function setHealth(state, text) {
    $("health").dataset.state = state;
    const [main, detail] = text.split(" · ");
    $("healthText").textContent = main;
    $("healthDetail").textContent = detail ? "· " + detail : "";
  }

  async function checkHealth() {
    if (!store.token) { setHealth("idle", "Sin conectar"); return false; }
    try {
      const r = await api("/health");
      if (r.status === 401) { setHealth("bad", "Token no válido"); return false; }
      if (r.ok && r.body && r.body.ready) {
        const b = r.body;
        setHealth("ok", `Operativo · ${b.running_tasks}/${b.max_concurrent_tasks} en ejecución`);
        return true;
      }
      setHealth("warn", "No preparado");
      return false;
    } catch {
      setHealth("bad", "Sin conexión");
      return false;
    }
  }

  // ---------------------------------------------------------------- Token
  function toggleToken(open) {
    const panel = $("tokenPanel");
    const show = open === undefined ? panel.hidden : open;
    panel.hidden = !show;
    $("tokenToggle").setAttribute("aria-expanded", String(show));
    if (show) $("tokenInput").focus();
  }

  $("tokenToggle").addEventListener("click", () => toggleToken());
  $("tokenForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    store.token = $("tokenInput").value.trim();
    $("tokenInput").value = "";
    if (await checkHealth()) toggleToken(false);
  });
  $("tokenClear").addEventListener("click", () => {
    store.token = "";
    $("tokenInput").value = "";
    setHealth("idle", "Sin conectar");
  });

  // ---------------------------------------------------------------- Tareas
  const list = $("taskList");
  const tpl = $("taskTpl");
  const nodes = new Map();

  function fmtElapsed(s) {
    if (typeof s !== "number") return "";
    if (s < 60) return s.toFixed(1) + " s";
    return Math.floor(s / 60) + " min " + Math.round(s % 60) + " s";
  }

  function render(entry) {
    let li = nodes.get(entry.id);
    if (!li) {
      li = tpl.content.firstElementChild.cloneNode(true);
      const row = li.querySelector(".task__row");
      row.addEventListener("click", () => {
        const d = li.querySelector(".task__detail");
        d.hidden = !d.hidden;
        row.setAttribute("aria-expanded", String(!d.hidden));
      });
      nodes.set(entry.id, li);
      list.prepend(li);
    }
    const s = entry.status || "en_curso";
    li.dataset.status = s;
    li.querySelector(".status__text").textContent = LABELS[s] || s;
    li.querySelector(".task__text").textContent = entry.task;
    li.querySelector(".task__text").title = entry.task;
    li.querySelector(".task__meta").textContent = fmtElapsed(entry.elapsed);
    const set = (k, v) => { li.querySelector(`[data-k="${k}"]`).textContent = v; };
    set("resumen", entry.resumen || (s === "en_curso" ? "En ejecución…" : "—"));
    const verif = li.querySelector('[data-k="verificacion"]');
    verif.textContent = VERIF[entry.verificacion] || entry.verificacion || "—";
    verif.classList.toggle("warn", !!entry.sideEffects);
    if (entry.sideEffects) verif.textContent += " · posibles efectos sin confirmar";
    set("steps", entry.steps != null ? String(entry.steps) : "—");
    set("interno", INTERNAL[entry.interno] || entry.interno || "—");
    set("id", entry.id);
    updateEmpty();
  }

  function updateEmpty() {
    const has = nodes.size > 0;
    $("emptyState").hidden = has;
    $("clearTasks").hidden = !has;
  }

  function saveEntry(entry) {
    const all = store.tasks.filter((t) => t.id !== entry.id);
    all.unshift(entry);
    store.tasks = all;
  }

  async function poll(entry) {
    if (FINAL.has(entry.status)) return;
    try {
      const r = await api("/tasks/" + encodeURIComponent(entry.id));
      if (r.status === 401) { setHealth("bad", "Token no válido"); return; }
      if (r.status === 404) {
        Object.assign(entry, { status: "fallida", resumen: "La tarea ya no existe en el servidor." });
      } else if (r.ok && r.body) {
        const b = r.body;
        Object.assign(entry, {
          status: b.status, resumen: b.resumen, steps: b.steps_executed, elapsed: b.elapsed_seconds,
          interno: b.estado_interno, verificacion: b.verificacion, sideEffects: b.posibles_efectos_sin_confirmar,
        });
      }
    } catch { /* reintento en el siguiente ciclo */ }
    saveEntry(entry);
    render(entry);
    if (!FINAL.has(entry.status)) setTimeout(() => poll(entry), POLL_MS);
    else checkHealth();
  }

  $("clearTasks").addEventListener("click", () => {
    store.tasks = store.tasks.filter((t) => !FINAL.has(t.status));
    for (const [id, li] of nodes) {
      if (FINAL.has(li.dataset.status)) { li.remove(); nodes.delete(id); }
    }
    updateEmpty();
  });

  // ---------------------------------------------------------------- Envío
  function showError(msg) {
    const el = $("formError");
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  function describeError(r) {
    if (r.status === 401) return "Token no válido. Abre «Acceso» e introdúcelo de nuevo.";
    if (r.status === 413) return "La petición es demasiado grande.";
    if (r.status === 429 || r.status === 503) return "El servicio está ocupado o no disponible. Inténtalo en unos segundos.";
    const d = r.body && r.body.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d.length) return "Datos no válidos: " + d.map((e) => (e.loc || []).slice(-1)[0]).join(", ");
    return "No se pudo crear la tarea (HTTP " + r.status + ").";
  }

  $("taskForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    showError("");
    if (!store.token) { toggleToken(true); showError("Introduce el token de acceso para continuar."); return; }
    let task = $("task").value.trim();
    if (task.startsWith("{")) {
      // Se pegó el JSON de una prueba: se envía solo su instrucción.
      try { const j = JSON.parse(task); task = String(j.instruccion || j.tarea || j.task || task).trim(); } catch { /* texto libre */ }
    }
    const url = $("desktopUrl").value.trim();
    const password = $("desktopPassword").value;
    if (!task) { showError("Escribe una instrucción."); $("task").focus(); return; }
    if (!url) { showError("Indica la URL del escritorio."); $("desktopUrl").focus(); return; }

    const btn = $("submitBtn");
    btn.disabled = true;
    btn.textContent = "Enviando…";
    try {
      const r = await api("/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task, desktop: { url, password } }),
      });
      if (!r.ok || !r.body || !r.body.task_id) { showError(describeError(r)); return; }
      const entry = { id: r.body.task_id, task, status: "en_curso", created: Date.now() };
      saveEntry(entry);
      render(entry);
      if ($("task").value.trim() === task) $("task").value = "";
      poll(entry);
    } catch {
      showError("Sin conexión con el servicio.");
    } finally {
      btn.disabled = false;
      btn.textContent = "Enviar tarea";
      $("desktopPassword").value = "";
    }
  });

  // ---------------------------------------------------------------- Inicio
  if (document.body.dataset.docs === "true") $("docsLink").hidden = false;
  for (const entry of store.tasks.slice().reverse()) { render(entry); poll(entry); }
  updateEmpty();
  checkHealth().then((ok) => { if (!store.token) toggleToken(true); return ok; });
  setInterval(() => { if (store.token && !document.hidden) checkHealth(); }, 5000);
})();
