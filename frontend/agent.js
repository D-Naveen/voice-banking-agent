"use strict";
const $ = id => document.getElementById(id);
let selectedId = null;
let cases = [];

// which extracted fields belong to the "uncertain_field" reported by the system
const FIELD_KEYS = {
  amount: ["amount", "amount_text", "alternatives", "cross_check"],
  payee: ["payee_id", "payee_name", "alternatives", "cross_check"],
  transcript: ["asr_confidence"],
  voice_authenticity: ["spoof_score", "provider"],
  voice_liveness: ["liveness_failures"],
  pin: [], customer_request: [], request: ["amount_text"],
};

function msg(text, isError = false) {
  const m = $("agentMessage");
  m.textContent = text;
  m.className = `notice ${isError ? "error" : "success"}`;
}

async function req(url, opts = {}) {
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? "Please fill in all required fields (a note is required)." : `Request failed (${r.status})`);
  }
  return data;
}
const post = (url, body) => req(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

/* ───────── queue ───────── */
async function loadCases() {
  $("refreshButton").disabled = true;
  try {
    cases = (await req(`/agent/cases?status=${$("statusFilter").value}`)).cases;
    const list = $("caseList"); list.replaceChildren();
    if (!cases.length) list.appendChild(el("p", "muted", "No cases here."));
    cases.forEach(c => {
      const b = el("button", `queue-item${c.id === selectedId ? " active" : ""}`);
      b.type = "button"; b.dataset.caseId = c.id;
      const top = el("div"); top.append(el("span", "ref", c.case_ref), document.createTextNode(" "), el("span", `pill ${c.status}`, c.status));
      b.append(top, el("div", "small", `${c.customer || "customer"} · ${c.uncertain_field || "—"}`),
               el("div", "muted small", (c.created_at || "").replace("T", " ").slice(0, 16)));
      b.addEventListener("click", () => { selectedId = c.id; renderCase(c); document.querySelectorAll(".queue-item").forEach(x => x.classList.toggle("active", x === b)); });
      list.appendChild(b);
    });
    const cur = cases.find(c => c.id === selectedId);
    if (cur) renderCase(cur); else { selectedId = null; $("caseDetail").replaceChildren(el("p", "muted", "Select a case from the queue.")); }
  } catch (e) { msg(e.message, true); } finally { $("refreshButton").disabled = false; }
}

function renderCase(c) {
  const box = $("caseDetail"); box.replaceChildren();
  const h = el("h2", "", `${c.case_ref} `); h.appendChild(el("span", `pill ${c.status}`, c.status)); box.appendChild(h);
  box.appendChild(el("p", "muted small", `Customer: ${c.customer || "—"} · opened ${(c.created_at || "").replace("T", " ").slice(0, 19)}`));

  const reason = el("div", "reason-box"); reason.append(el("strong", "", "Why it was escalated: "), document.createTextNode(c.reason));
  box.appendChild(reason);

  box.appendChild(el("h3", "", "What the customer said"));
  box.appendChild(el("p", "", c.transcript || "(no transcript)"));

  box.appendChild(el("h3", "", "Extracted fields"));
  const highlight = new Set(FIELD_KEYS[c.uncertain_field] || []);
  const tbl = el("table", "kv");
  const ef = c.extracted_fields || {};
  const rows = Object.entries(ef).filter(([k]) => k !== "recent_turns");
  const uncertainRow = (k) => highlight.has(k);
  tbl.replaceChildren();
  const head = el("tr", "uncertain"); head.dataset.field = "uncertain_field";
  head.append(el("td", "", "UNCERTAIN FIELD"), el("td", "", c.uncertain_field || "—")); tbl.appendChild(head);
  rows.forEach(([k, v]) => {
    const tr = el("tr", uncertainRow(k) ? "uncertain" : "");
    tr.append(el("td", "", k), el("td", "", typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "—")));
    tbl.appendChild(tr);
  });
  box.appendChild(tbl);

  if (ef.recent_turns && ef.recent_turns.length) {
    box.appendChild(el("h3", "", "Conversation so far"));
    const t = el("div", "turns");
    ef.recent_turns.forEach(x => t.appendChild(el("div", x.role, `${x.role === "agent" ? "Assistant" : "Customer"}: ${x.text}`)));
    box.appendChild(t);
  }

  if (c.agent_note) box.appendChild(el("p", "muted small", `Previous note: ${c.agent_note} (${c.resolution})`));

  if (c.status === "resolved") { box.appendChild(el("p", "muted", "This case is resolved.")); return; }

  box.appendChild(el("label", "", "Decision note (required)"));
  const note = el("textarea"); note.rows = 3; note.id = "caseNote"; note.maxLength = 1000;
  note.placeholder = "Explain your decision. For 'Ask customer to repeat', this note is shown to the customer.";
  box.appendChild(note);
  const err = el("p", "error-text"); err.id = "caseError"; box.appendChild(err);

  const row = el("div", "btn-row");
  [["approve", "Approve", "success"], ["reject", "Reject", "danger"], ["ask_again", "Ask customer to repeat", ""]].forEach(([action, label, cls]) => {
    const b = el("button", `btn ${cls}`, label); b.type = "button"; b.dataset.action = action;
    b.addEventListener("click", async () => {
      if (note.value.trim().length < 3) { err.textContent = "A note of at least 3 characters is required."; note.focus(); return; }
      err.textContent = ""; row.querySelectorAll("button").forEach(x => x.disabled = true);
      try {
        const r = await post(`/agent/cases/${c.id}/action`, { action, note: note.value.trim() });
        msg(`${r.case_ref}: ${r.message}`);
        await loadCases(); await loadAudit();
      } catch (e) { err.textContent = e.message; row.querySelectorAll("button").forEach(x => x.disabled = false); }
    });
    row.appendChild(b);
  });
  box.appendChild(row);
  box.appendChild(el("p", "muted small", "Agent actions update the case only. They never move money."));
}

/* ───────── settings ───────── */
async function loadSettings() {
  const data = await req("/agent/settings");
  const body = $("settingsBody"); body.replaceChildren();
  data.settings.forEach(s => {
    const row = el("div", "set-row");
    const lab = el("label", "", s.key); lab.htmlFor = `set_${s.key}`;
    const inp = el("input"); inp.id = `set_${s.key}`; inp.type = "number"; inp.step = "any"; inp.value = s.value; inp.min = s.min; inp.max = s.max;
    const noteIn = el("input"); noteIn.placeholder = "Reason for change (required)"; noteIn.setAttribute("aria-label", `Note for ${s.key}`);
    const save = el("button", "btn", "Save"); save.type = "button"; save.dataset.key = s.key;
    save.addEventListener("click", async () => {
      try {
        const r = await post("/agent/settings", { key: s.key, value: Number(inp.value), note: noteIn.value.trim() });
        msg(`${r.key}: ${r.old} → ${r.new}`); await loadSettings(); await loadAudit();
      } catch (e) { msg(e.message, true); }
    });
    row.append(lab, inp, el("div", "desc", `${s.description} (default ${s.default}, range ${s.min}–${s.max})`), noteIn, save);
    body.appendChild(row);
  });
  if (data.changes.length) {
    body.appendChild(el("h3", "", "Recent changes"));
    data.changes.forEach(c => body.appendChild(el("div", "audit-row", `${(c.at || "").replace("T", " ").slice(0, 19)} · ${c.key}: ${c.old} → ${c.new} · ${c.note || ""}`)));
  }
}
$("settingsToggle").addEventListener("click", async () => {
  const body = $("settingsBody"); const open = body.classList.contains("hidden");
  body.classList.toggle("hidden", !open);
  $("settingsToggle").textContent = open ? "Hide" : "Show"; $("settingsToggle").setAttribute("aria-expanded", String(open));
  if (open) { try { await loadSettings(); } catch (e) { msg(e.message, true); } }
});

/* ───────── audit ───────── */
async function loadAudit() {
  $("refreshAuditButton").disabled = true;
  try {
    const { events } = await req("/agent/audit-logs?limit=40");
    const box = $("auditLogList"); box.replaceChildren();
    if (!events.length) box.appendChild(el("p", "muted", "No events yet."));
    events.forEach(e => box.appendChild(el("div", "audit-row", `${(e.created_at || "").replace("T", " ").slice(0, 19)} · ${e.event_type} · ${JSON.stringify(e.details || {})}`)));
  } catch (e) { msg(e.message, true); } finally { $("refreshAuditButton").disabled = false; }
}

$("refreshButton").addEventListener("click", loadCases);
$("statusFilter").addEventListener("change", () => { selectedId = null; loadCases(); });
$("refreshAuditButton").addEventListener("click", loadAudit);

/* ───────── session ───────── */
async function enter() {
  try {
    const me = await req("/auth/me");
    if (me.role !== "agent") throw new Error("Please sign in with the agent account (agent1).");
    $("agentLoginPanel").classList.add("hidden"); $("agentApp").classList.remove("hidden");
    $("agentLogout").classList.remove("hidden"); $("agentMessage").classList.add("hidden");
    await loadCases(); await loadAudit();
  } catch (e) {
    $("agentLoginPanel").classList.remove("hidden"); $("agentApp").classList.add("hidden");
    if (!/log in|sign in/i.test(e.message)) msg(e.message, true);
  }
}
$("agentLoginForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  const username = $("agentUsername").value.trim(), password = $("agentPassword").value;
  if (!username || !password) { msg("Enter your username and password.", true); return; }
  try { await post("/auth/login", { username, password }); await enter(); }
  catch (e) {
    // A customer login is refused here (it has no voice-consent step): explain the real reason.
    msg(/voice-processing/.test(e.message) ? "This console is for human agents only. Customers should use the customer screen." : e.message, true);
  }
});
$("agentLogout").addEventListener("click", async () => {
  await fetch("/auth/logout", { method: "POST" }); selectedId = null;
  $("agentApp").classList.add("hidden"); $("agentLoginPanel").classList.remove("hidden"); $("agentLogout").classList.add("hidden");
  msg("Signed out.");
});
enter();
