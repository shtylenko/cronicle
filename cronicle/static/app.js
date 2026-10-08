async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 204) return null;
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

const jobsBody = document.querySelector("#jobs tbody");
const runsBody = document.querySelector("#runs tbody");
const form = document.querySelector("#job-form");
const NONE_PROJECT = "__none__"; // filter value meaning Unassigned

function currentProjectParam() {
  // null = all projects (no query param), "" = unassigned, name = one project
  const v = document.querySelector("#project-filter").value;
  if (v === "") return null;
  return v === NONE_PROJECT ? "" : v;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}

async function refreshHealth() {
  try {
    const h = await api("/api/health");
    const el = document.querySelector("#health");
    el.textContent = h.crontab_available ? "crontab ok" : "no crontab binary";
    el.classList.toggle("bad", !h.crontab_available);
    const w = document.querySelector("#cron-warning");
    if (!h.crontab_available) {
      w.hidden = false;
      w.textContent = "No `crontab` on this machine — job edits will fail until cron is installed (see README). Run history still works.";
    } else {
      w.hidden = true;
    }
  } catch (e) {
    document.querySelector("#health").textContent = "backend unreachable";
  }
}

async function refreshProjects() {
  let projects = [];
  try {
    projects = await api("/api/projects");
  } catch (e) {
    // leave project UI at defaults; jobs still work unfiltered
  }
  const filt = document.querySelector("#project-filter");
  const keep = filt.value;
  filt.innerHTML = `<option value="">All</option><option value="${NONE_PROJECT}">Unassigned</option>` +
    projects.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join("");
  filt.value = [...filt.options].some((o) => o.value === keep) ? keep : "";
  const fsel = document.querySelector("#f-project");
  const fkeep = fsel.value;
  fsel.innerHTML = `<option value="">Unassigned</option>` +
    projects.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join("");
  if ([...fsel.options].some((o) => o.value === fkeep)) fsel.value = fkeep;
  renderProjectManager(projects);
}

async function refreshJobs() {
  const proj = currentProjectParam();
  const q = proj === null ? "" : `?project=${encodeURIComponent(proj)}`;
  let jobs;
  try {
    jobs = await api("/api/jobs" + q);
  } catch (e) {
    jobsBody.innerHTML = `<tr><td colspan="8">Failed to load jobs: ${esc(e.message)}</td></tr>`;
    return;
  }
  jobsBody.innerHTML = jobs.length ? "" : `<tr><td colspan="8">No cron jobs yet.</td></tr>`;
  for (const j of jobs) {
    const tr = document.createElement("tr");
    const status = j.enabled
      ? (j.wrapped ? `<span class="pill ok">on · logged</span>` : `<span class="pill running">on · raw</span>`)
      : `<span class="pill off">off</span>`;
    tr.innerHTML =
      `<td>${esc(j.name || "(unnamed)")}<br><span class="mono" style="color:var(--muted)">${esc(j.id)}</span></td><td>${esc(j.project || "—")}</td><td>${esc(j.description || "")}</td>` +
      `<td class="nowrap"><code>${esc(j.schedule)}</code></td><td class="nowrap">${esc(j.schedule_human || j.schedule)}</td><td><code>${esc(j.command)}</code></td>` +
      `<td>${status}</td>` +
      `<td class="actions"><button data-a="run">Run</button><button data-a="edit">Edit</button><button data-a="del">Delete</button></td>`;
    tr.querySelector('[data-a="run"]').onclick = async () => {
      try { await api(`/api/jobs/${j.id}/run`, {method: "POST"}); refreshRuns(); }
      catch (e) { alert("Run failed: " + e.message); }
    };
    tr.querySelector('[data-a="edit"]').onclick = () => {
      document.querySelector("#f-id").value = j.id;
      document.querySelector("#f-name").value = j.name || "";
      document.querySelector("#f-desc").value = j.description || "";
      const fsel = document.querySelector("#f-project");
      if (j.project && ![...fsel.options].some((o) => o.value === j.project)) {
        const o = document.createElement("option");
        o.value = j.project;
        o.textContent = j.project;
        fsel.appendChild(o);
      }
      fsel.value = j.project || "";
      document.querySelector("#f-schedule").value = j.schedule;
      document.querySelector("#f-command").value = j.command;
      document.querySelector("#f-enabled").checked = j.enabled;
      form.hidden = false;
    };
    tr.querySelector('[data-a="del"]').onclick = async () => {
      if (!confirm(`Delete job ${j.id} (${j.name || j.command})? History is kept.`)) return;
      try { await api(`/api/jobs/${j.id}`, {method: "DELETE"}); refreshJobs(); refreshRuns(); }
      catch (e) { alert("Delete failed: " + e.message); }
    };
    jobsBody.appendChild(tr);
  }
  const sel = document.querySelector("#run-filter");
  const cur = sel.value;
  sel.innerHTML = `<option value="">All</option>` +
    jobs.map((j) => `<option value="${esc(j.id)}">${esc(j.name || j.command.slice(0, 30))}</option>`).join("");
  sel.value = cur;
}

function fmtDur(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return ms + " ms";
  return (ms / 1000).toFixed(1) + " s";
}

async function refreshRuns() {
  const jobId = document.querySelector("#run-filter").value;
  const proj = currentProjectParam();
  let q;
  if (jobId) q = `?job_id=${encodeURIComponent(jobId)}&limit=50`;
  else if (proj !== null) q = `?project=${encodeURIComponent(proj)}&limit=50`;
  else q = "?limit=50";
  let runs;
  try {
    runs = await api("/api/runs" + q);
  } catch (e) {
    runsBody.innerHTML = `<tr><td colspan="5">Failed to load runs: ${esc(e.message)}</td></tr>`;
    return;
  }
  runsBody.innerHTML = runs.length ? "" : `<tr><td colspan="5">No runs recorded yet.</td></tr>`;
  for (const r of runs) {
    const tr = document.createElement("tr");
    tr.className = "run-row";
    tr.innerHTML =
      `<td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td>` +
      `<td>${esc(r.job_name || r.job_id)}</td><td class="mono">${esc(r.started_at || "")}</td>` +
      `<td>${fmtDur(r.duration_ms)}</td><td class="mono">${r.exit_code == null ? "—" : r.exit_code}</td>`;
    tr.onclick = () => showLog(r.id);
    runsBody.appendChild(tr);
  }
}

async function showLog(runId) {
  try {
    const out = await api(`/api/runs/${runId}/log`);
    document.querySelector("#log-id").textContent = runId;
    document.querySelector("#log").textContent = out.text + (out.truncated ? "\n…(truncated)…" : "");
    document.querySelector("#log-section").hidden = false;
    document.querySelector("#log-section").scrollIntoView();
  } catch (e) {
    alert("Log failed: " + e.message);
  }
}

document.querySelector("#add-toggle").onclick = () => {
  form.reset();
  document.querySelector("#f-id").value = "";
  document.querySelector("#f-enabled").checked = true;
  form.hidden = !form.hidden;
};
document.querySelector("#f-cancel").onclick = () => { form.hidden = true; };
document.querySelector("#nl-toggle").onclick = () => {
  const row = document.querySelector("#nl-input-row");
  row.hidden = !row.hidden;
  if (!row.hidden) document.querySelector("#nl-text").focus();
};
async function applyPlainEnglish() {
  const text = document.querySelector("#nl-text").value.trim();
  const err = document.querySelector("#nl-error");
  if (!text) return;
  try {
    const out = await api("/api/parse-schedule", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text}),
    });
    document.querySelector("#f-schedule").value = out.schedule;
    err.hidden = true;
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  }
}
document.querySelector("#nl-go").onclick = applyPlainEnglish;
document.querySelector("#nl-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); applyPlainEnglish(); }
});
document.querySelector("#log-close").onclick = () => {
  document.querySelector("#log-section").hidden = true;
};
document.querySelector("#runs-refresh").onclick = refreshRuns;
document.querySelector("#run-filter").onchange = refreshRuns;
document.querySelector("#project-filter").onchange = () => {
  document.querySelector("#run-filter").value = "";
  refreshJobs();
  refreshRuns();
};
document.querySelector("#projects-toggle").onclick = () => {
  const pm = document.querySelector("#project-manager");
  pm.hidden = !pm.hidden;
};

function renderProjectManager(projects) {
  const list = document.querySelector("#project-list");
  list.innerHTML = "";
  if (!projects.length) {
    list.innerHTML = `<p class="hint">No projects yet. Jobs stay Unassigned until linked.</p>`;
  }
  for (const p of projects) {
    const row = document.createElement("div");
    row.className = "pm-row";
    const n = p.job_count === 1 ? "1 job" : `${p.job_count} jobs`;
    row.innerHTML = `<input value="${esc(p.name)}">` +
      `<span class="mono muted">${esc(n)}</span>` +
      `<button data-a="save">Rename</button><button data-a="del">Delete</button>`;
    const err = document.querySelector("#pm-error");
    row.querySelector('[data-a="save"]').onclick = async () => {
      try {
        await api(`/api/projects/${encodeURIComponent(p.name)}`, {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({name: row.querySelector("input").value}),
        });
        err.hidden = true;
        refreshProjects();
        refreshJobs();
        refreshRuns();
      } catch (e) {
        err.textContent = e.message;
        err.hidden = false;
      }
    };
    row.querySelector('[data-a="del"]').onclick = async () => {
      if (!confirm(`Delete project '${p.name}'?`)) return;
      try {
        await api(`/api/projects/${encodeURIComponent(p.name)}`, {method: "DELETE"});
        err.hidden = true;
        refreshProjects();
        refreshJobs();
        refreshRuns();
      } catch (e) {
        err.textContent = e.message;
        err.hidden = false;
      }
    };
    list.appendChild(row);
  }
}

async function addProject() {
  const input = document.querySelector("#pm-new");
  const err = document.querySelector("#pm-error");
  if (!input.value.trim()) return;
  try {
    await api("/api/projects", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name: input.value}),
    });
    input.value = "";
    err.hidden = true;
    refreshProjects();
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  }
}
document.querySelector("#pm-add").onclick = addProject;
document.querySelector("#pm-new").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); addProject(); }
});

form.onsubmit = async (e) => {
  e.preventDefault();
  const id = document.querySelector("#f-id").value;
  const payload = {
    name: document.querySelector("#f-name").value,
    description: document.querySelector("#f-desc").value,
    project: document.querySelector("#f-project").value,
    schedule: document.querySelector("#f-schedule").value,
    command: document.querySelector("#f-command").value,
    enabled: document.querySelector("#f-enabled").checked,
  };
  try {
    if (id) await api(`/api/jobs/${id}`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    else await api("/api/jobs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    form.hidden = true;
    refreshJobs();
  } catch (err) {
    alert("Save failed: " + err.message);
  }
};

refreshHealth();
refreshProjects().then(() => {
  refreshJobs();
  refreshRuns();
});
