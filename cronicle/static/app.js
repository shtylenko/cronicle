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

async function refreshJobs() {
  let jobs;
  try {
    jobs = await api("/api/jobs");
  } catch (e) {
    jobsBody.innerHTML = `<tr><td colspan="5">Failed to load jobs: ${esc(e.message)}</td></tr>`;
    return;
  }
  jobsBody.innerHTML = jobs.length ? "" : `<tr><td colspan="5">No cron jobs yet.</td></tr>`;
  for (const j of jobs) {
    const tr = document.createElement("tr");
    const status = j.enabled
      ? (j.wrapped ? `<span class="pill ok">on · logged</span>` : `<span class="pill running">on · raw</span>`)
      : `<span class="pill off">off</span>`;
    tr.innerHTML =
      `<td>${esc(j.name || "(unnamed)")}<br><span class="mono" style="color:#666">${esc(j.id)}</span></td>` +
      `<td><code>${esc(j.schedule)}</code></td><td><code>${esc(j.command)}</code></td>` +
      `<td>${status}</td>` +
      `<td class="actions"><button data-a="run">Run</button><button data-a="edit">Edit</button><button data-a="del">Delete</button></td>`;
    tr.querySelector('[data-a="run"]').onclick = async () => {
      try { await api(`/api/jobs/${j.id}/run`, {method: "POST"}); refreshRuns(); }
      catch (e) { alert("Run failed: " + e.message); }
    };
    tr.querySelector('[data-a="edit"]').onclick = () => {
      document.querySelector("#f-id").value = j.id;
      document.querySelector("#f-name").value = j.name || "";
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
  const q = jobId ? `?job_id=${encodeURIComponent(jobId)}&limit=50` : "?limit=50";
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
document.querySelector("#log-close").onclick = () => {
  document.querySelector("#log-section").hidden = true;
};
document.querySelector("#runs-refresh").onclick = refreshRuns;
document.querySelector("#run-filter").onchange = refreshRuns;

form.onsubmit = async (e) => {
  e.preventDefault();
  const id = document.querySelector("#f-id").value;
  const payload = {
    name: document.querySelector("#f-name").value,
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
refreshJobs();
refreshRuns();
