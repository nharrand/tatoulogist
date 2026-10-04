"use strict";

// Status helpers ----------------------------------------------------------
const STATUS = { passed: "passed", failed: "failed", error: "error" };
function statusClass(s) { return STATUS[s] || "never"; }
function fmtTime(iso) {
  if (!iso) return "never run";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString(undefined,
    { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
async function getJSON(url) {
  const r = await fetch(url, { headers: { "Accept": "application/json" } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
  return r.json();
}

// Build one scenario row --------------------------------------------------
// Renders into the given container. `group` scopes all requests.
function scenarioRow(group, s) {
  const last = s.last;
  const cls = last ? statusClass(last.status) : "never";
  const row = el("div", `row st-${cls}`);

  const head = el("div", "row-head");
  const caret = el("button", "caret", "▶");
  caret.setAttribute("aria-label", "Toggle history");
  const name = el("div", "name");
  name.textContent = s.scenario + (s.unknown ? "  (not in current code)" : "");
  const when = el("div", "when", last ? fmtTime(last.finished_at || last.started_at) : "never run");
  const checks = el("div", "checks");
  if (last) checks.textContent = `${last.checks_passed}/${last.checks_total}`;

  const right = el("div");
  right.style.display = "flex";
  right.style.gap = "10px";
  right.style.alignItems = "center";
  const badge = el("span", `badge ${cls}`, last ? last.status : "never run");
  const rerun = el("button", "btn small", "Rerun");
  const state = el("span", "rerun-state");
  right.append(badge, rerun, state);

  head.append(caret, name, when, checks, right);

  const body = el("div", "row-body");
  const inner = el("div", "inner");
  const history = el("div", "history");
  inner.append(history);
  body.append(inner);

  let loaded = false;
  function toggle() {
    const open = body.classList.toggle("open");
    caret.classList.toggle("open", open);
    if (open && !loaded) { loaded = true; loadHistory(group, s.scenario, history); }
  }
  caret.addEventListener("click", toggle);
  name.addEventListener("click", toggle);

  rerun.addEventListener("click", () => doRerun(group, s.scenario, rerun, state, () => {
    loaded = false;
    if (body.classList.contains("open")) loadHistory(group, s.scenario, history);
    // Refresh the row's own summary regardless.
    refreshRowSummary(group, s.scenario, row, when, checks, badge);
  }));

  row.append(head, body);
  return row;
}

async function refreshRowSummary(group, scenario, row, when, checks, badge) {
  try {
    const all = await getJSON(`/api/group/${encodeURIComponent(group)}/scenarios`);
    const s = all.find(x => x.scenario === scenario);
    if (!s) return;
    const cls = s.last ? statusClass(s.last.status) : "never";
    row.className = `row st-${cls}`;
    badge.className = `badge ${cls}`;
    badge.textContent = s.last ? s.last.status : "never run";
    when.textContent = s.last ? fmtTime(s.last.finished_at || s.last.started_at) : "never run";
    checks.textContent = s.last ? `${s.last.checks_passed}/${s.last.checks_total}` : "";
  } catch (e) { /* leave as is */ }
}

async function loadHistory(group, scenario, container) {
  container.replaceChildren(el("div", "empty", "Loading…"));
  let runs;
  try {
    runs = await getJSON(
      `/api/group/${encodeURIComponent(group)}/scenario/${encodeURIComponent(scenario)}/history`);
  } catch (e) {
    container.replaceChildren(el("div", "empty", "Could not load history: " + e.message));
    return;
  }
  if (!runs.length) {
    container.replaceChildren(el("div", "empty", "No runs yet. Use Rerun to run this scenario."));
    return;
  }
  container.replaceChildren(...runs.map(run => runRow(group, scenario, run)));
}

function runRow(group, scenario, run) {
  const cls = statusClass(run.status);
  const wrap = el("div", "run");
  const head = el("div", "run-head");
  head.append(
    el("span", `dot ${cls}`),
    el("span", `badge ${cls}`, run.status),
    el("span", "when", fmtTime(run.finished_at || run.started_at)),
    el("span", "checks", `${run.checks_passed}/${run.checks_total} checks`));
  const detail = el("div", "run-detail");
  wrap.append(head, detail);

  let loaded = false;
  head.addEventListener("click", () => {
    const open = detail.classList.toggle("open");
    if (open && !loaded) { loaded = true; loadRunDetail(group, scenario, run.source, detail); }
  });
  return wrap;
}

async function loadRunDetail(group, scenario, source, container) {
  container.replaceChildren(el("div", "empty", "Loading…"));
  let result;
  try {
    const url = `/api/group/${encodeURIComponent(group)}/run`
      + `?source=${encodeURIComponent(source)}&scenario=${encodeURIComponent(scenario)}`;
    result = await getJSON(url);
  } catch (e) {
    container.replaceChildren(el("div", "empty", "Could not load logs: " + e.message));
    return;
  }
  container.replaceChildren();

  if (result.aborted) container.append(line("Aborted: " + result.aborted, "no"));
  if (result.error) {
    container.append(el("div", "section-label", "Error"));
    container.append(logBlock(result.error + (result.traceback ? "\n\n" + result.traceback : "")));
  }

  const checks = result.checks || [];
  if (checks.length) {
    container.append(el("div", "section-label", "Checks"));
    const box = el("div");
    for (const c of checks) {
      const ok = !!c.passed;
      const item = el("div", "check " + (ok ? "ok" : "no"));
      item.append(el("span", "mark", ok ? "✔" : "✘"), el("span", "desc", c.description));
      if (!ok && c.details != null) {
        item.append(el("span", "detail", " — " + compact(c.details)));
      }
      box.append(item);
    }
    container.append(box);
  }

  const steps = result.steps || [];
  if (steps.length) {
    container.append(el("div", "section-label", "Steps"));
    const box = el("div", "steps");
    for (const st of steps) {
      const row = el("div", "step");
      const left = el("span", "", `${st.step}`);
      const req = (st.request || {});
      const mid = el("span", "", `${req.method || ""} ${shortUrl(req.url)}`);
      const code = st.response ? st.response.status_code : null;
      let codeCls = "none", codeText = st.error ? "ERR" : "—";
      if (code != null) { codeCls = code < 400 ? "ok" : "bad"; codeText = String(code); }
      row.append(left, mid, el("span", "code " + codeCls, codeText));
      box.append(row);
    }
    container.append(box);
  }

  if (!checks.length && !steps.length && !result.error) {
    container.append(el("div", "empty", "No steps or checks recorded."));
  }
}

function line(text, kind) {
  const d = el("div", "check " + (kind || ""));
  d.append(el("span", "mark", kind === "no" ? "✘" : ""), el("span", "", text));
  return d;
}
function logBlock(text) { const p = el("pre", "log"); p.textContent = text; return p; }
function shortUrl(u) { if (!u) return ""; try { return new URL(u).pathname; } catch { return u; } }
function compact(v) {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 200 ? s.slice(0, 200) + "…" : s;
}

// Rerun + poll ------------------------------------------------------------
async function doRerun(group, scenario, btn, state, onDone) {
  btn.disabled = true;
  state.innerHTML = '<span class="spinner"></span> queued';
  let job;
  try {
    const r = await fetch(`/api/group/${encodeURIComponent(group)}/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario }),
    });
    const data = await r.json();
    if (!r.ok) {
      state.textContent = data.busy ? "busy — try again shortly" : (data.error || "failed to start");
      btn.disabled = false;
      return;
    }
    job = data.job;
  } catch (e) {
    state.textContent = "network error";
    btn.disabled = false;
    return;
  }

  state.innerHTML = '<span class="spinner"></span> running';
  const poll = setInterval(async () => {
    let j;
    try { j = await getJSON(`/api/job/${job}`); }
    catch (e) { clearInterval(poll); state.textContent = "lost job"; btn.disabled = false; return; }
    if (j.state === "running") return;
    clearInterval(poll);
    btn.disabled = false;
    if (j.state === "failed") state.textContent = "rerun failed: " + (j.error || "");
    else state.textContent = "done: " + (j.status || "");
    onDone();
  }, 1000);
}

// Page bootstrap ----------------------------------------------------------
async function renderGroup(group, container) {
  container.replaceChildren(el("div", "empty", "Loading scenarios…"));
  let scenarios;
  try {
    scenarios = await getJSON(`/api/group/${encodeURIComponent(group)}/scenarios`);
  } catch (e) {
    container.replaceChildren(el("div", "empty", "Could not load scenarios: " + e.message));
    return;
  }
  if (!scenarios.length) {
    container.replaceChildren(el("div", "empty", "No scenarios found."));
    return;
  }
  container.replaceChildren(...scenarios.map(s => scenarioRow(group, s)));
}

// Admin: one expandable row per group, each holding a group table.
function groupRow(g) {
  const row = el("div", "row");
  const head = el("div", "row-head");
  const caret = el("button", "caret", "▶");
  const name = el("div", "name", g.group);
  const note = el("div", "checks", g.has_reports ? "" : "no reports yet");
  const spacer = el("div"); const spacer2 = el("div");
  head.append(caret, name, note, spacer, spacer2);

  const body = el("div", "row-body");
  const inner = el("div", "inner");
  const rows = el("div", "rows");
  inner.append(rows); body.append(inner);

  let loaded = false;
  function toggle() {
    const open = body.classList.toggle("open");
    caret.classList.toggle("open", open);
    if (open && !loaded) { loaded = true; renderGroup(g.group, rows); }
  }
  caret.addEventListener("click", toggle);
  name.addEventListener("click", toggle);
  row.append(head, body);
  return row;
}

async function renderAdmin(container) {
  container.replaceChildren(el("div", "empty", "Loading groups…"));
  let groups;
  try { groups = await getJSON("/api/groups"); }
  catch (e) { container.replaceChildren(el("div", "empty", "Could not load groups: " + e.message)); return; }
  if (!groups.length) { container.replaceChildren(el("div", "empty", "No groups in groups.csv.")); return; }
  container.replaceChildren(...groups.map(groupRow));
}

document.addEventListener("DOMContentLoaded", () => {
  const g = document.getElementById("group-table");
  if (g) renderGroup(g.dataset.group, g);
  const a = document.getElementById("admin-table");
  if (a) renderAdmin(a);
});
