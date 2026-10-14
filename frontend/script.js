"use strict";
/* VoiceBank customer app.
 * The browser only relays what was heard and renders what the server decides.
 * It never parses amounts, never decides confirmation, never moves money. */

const $ = id => document.getElementById(id);
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

const S = {
  user: null, sessionId: null, terminal: false, busy: false, muted: false,
  speaking: false, listening: null, challenge: null, timer: null, poll: null, lastSpeak: "",
};

/* ───────────── helpers ───────────── */
function notice(msg, kind = "") {
  const n = $("notice");
  n.textContent = msg;
  n.className = `notice ${kind}`.trim();
}
function clearNotice() { $("notice").className = "notice hidden"; }

async function api(path, body, method = "POST") {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail : `Request failed (${r.status})`);
    err.status = r.status;
    throw err;
  }
  return data;
}

function setBusy(v) {
  S.busy = v;
  ["typedSend", "confirmButton", "cancelButton", "sendButton", "livenessMic", "voiceConfirmButton", "pinCancel"]
    .forEach(id => { $(id).disabled = v; });
  $("pttButton").disabled = v;
}

/* ───────────── speech out (TTS) ───────────── */
function speak(text, force = false) {
  S.lastSpeak = text || "";
  if (!text || (S.muted && !force) || !("speechSynthesis" in window)) return;
  try {
    stopListening();
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-IN"; u.rate = 0.95;
    u.onstart = () => { S.speaking = true; };
    u.onend = u.onerror = () => { S.speaking = false; };
    window.speechSynthesis.speak(u);
  } catch { /* TTS is a convenience; the screen always shows the same information */ }
}

$("muteButton").addEventListener("click", () => {
  S.muted = !S.muted;
  $("muteButton").textContent = S.muted ? "🔇 Voice off" : "🔊 Voice on";
  $("muteButton").setAttribute("aria-pressed", String(S.muted));
  if (S.muted && "speechSynthesis" in window) window.speechSynthesis.cancel();
});

/* ───────────── speech in (STT) ───────────── */
function stopListening() {
  if (S.listening) { try { S.listening.abort(); } catch { /* already stopped */ } S.listening = null; }
}

/** Listen once; onFinal gets {text, alternatives, confidence}. */
function listen({ lang, onInterim, onFinal, onEnd, onFail }) {
  if (!SpeechRecognition) { onFail && onFail("unsupported"); return null; }
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();   // never listen to ourselves
  stopListening();
  const rec = new SpeechRecognition();
  rec.lang = lang; rec.continuous = false; rec.interimResults = true; rec.maxAlternatives = 3;
  let finalDone = false;
  rec.onresult = ev => {
    const res = ev.results[ev.results.length - 1];
    if (!res.isFinal) { onInterim && onInterim(res[0].transcript); return; }
    finalDone = true;
    const alts = [], confs = [];
    for (let i = 0; i < res.length; i++) { alts.push(res[i].transcript); confs.push(res[i].confidence); }
    onFinal({ text: alts[0], alternatives: alts.slice(1), confidence: confs[0] > 0 ? confs[0] : null });
  };
  rec.onerror = ev => { if (!finalDone) onFail && onFail(ev.error); finalDone = true; };
  rec.onend = () => { if (S.listening === rec) S.listening = null; onEnd && onEnd(finalDone); };
  S.listening = rec;
  try { rec.start(); } catch { S.listening = null; onFail && onFail("busy"); return null; }
  return rec;
}

/* ───────────── rendering ───────────── */
function showCard(name) {
  document.querySelectorAll("[data-card]").forEach(el => el.classList.toggle("hidden", el.dataset.card !== name));
}

const PIN_REASON_TEXT = {
  amount_over_threshold: "Larger payments need your PIN.",
  new_payee: "This is a new contact, so your PIN is needed.",
  audio_check_uncertain: "The voice check was uncertain.",
  audio_not_verified: "The voice could not be fully verified.",
  typed_liveness: "Typed digits can't be voice-checked.",
  single_source_voice: "I couldn't double-check what I heard.",
};

function clearTimers() {
  if (S.timer) { clearInterval(S.timer); S.timer = null; }
  if (S.poll) { clearInterval(S.poll); S.poll = null; }
}

function render(resp) {
  const { state, message, speak: say, data } = resp;
  S.sessionId = resp.session_id;
  S.terminal = ["receipt", "handoff", "cancelled", "expired"].includes(state);
  if (data && data.echo_ignored) return;           // heard our own voice: change nothing
  clearTimers();

  if (state === "capture" || state === "clarify") {
    showCard("capture");
    $("agentMessage").textContent = message || "";
    renderCandidates(data.candidates || []);
    if (state === "clarify") $("transcript").textContent = "…";
  } else if (state === "read_back") {
    showCard("read_back");
    $("rbAmount").textContent = data.amount_display;
    $("rbWords").textContent = `${data.amount_words} rupees`;
    $("rbPayee").textContent = data.payee_name;
    $("rbAccount").textContent = `Account ${data.account_masked}`;
    $("rbNew").classList.toggle("hidden", !data.new_payee);
  } else if (state === "liveness") {
    showCard("liveness");
    if (data.challenge) setChallenge(data.challenge);
    $("livenessMsg").textContent = data.liveness_error ? `${data.liveness_error} Try again.` : "";
  } else if (state === "step_up" || state === "ready") {
    showCard("pin");
    const needs = !!data.pin_required;
    $("pinAmount").textContent = data.amount_display;
    $("pinPayee").textContent = data.payee_name;
    $("pinGroup").classList.toggle("hidden", !needs);
    $("pinTitle").textContent = needs ? "Enter your PIN" : "Ready to send";
    $("pinEyebrow").textContent = needs ? "Extra security" : "Voice check passed";
    const ul = $("pinReasons");
    ul.replaceChildren();
    (data.pin_reasons || []).forEach(r => {
      const li = document.createElement("li");
      li.textContent = PIN_REASON_TEXT[r] || r;
      ul.appendChild(li);
    });
    $("pinInput").value = ""; $("pinError").textContent = "";
    if (needs) $("pinInput").focus();
  } else if (state === "receipt") {
    showResult(true, "Payment sent", message, data.receipt);
  } else if (state === "cancelled" || state === "expired") {
    showResult(false, state === "expired" ? "Request expired" : "Not sent", message || "Nothing was sent.");
  } else if (state === "handoff") {
    showCard("handoff");
    $("handoffMessage").textContent = (message || "").replace(/ Reference: .*$/, "") || "Nothing has been sent.";
    $("handoffRef").textContent = data.case_ref ? `Reference: ${data.case_ref}` : "";
    $("handoffStatus").textContent = "Waiting for a person to pick this up…";
    startHandoffPolling();
  }
  if (say) speak(say);
  refreshAccount();
}

function renderCandidates(list) {
  const box = $("candidates");
  box.replaceChildren();
  list.forEach(c => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip candidate"; b.textContent = c.name;
    b.addEventListener("click", () => sendTurn({ text: "", source: "text", choice_payee_id: c.id }));
    box.appendChild(b);
  });
}

function showResult(ok, title, message, receipt) {
  showCard("result");
  const icon = $("resultIcon");
  icon.textContent = ok ? "✓" : "✕";
  icon.className = `result-icon ${ok ? "" : "fail"}`.trim();
  $("resultTitle").textContent = title;
  $("resultMessage").textContent = message || "";
  $("resultReference").textContent = receipt
    ? `Ref ${receipt.reference} · New balance ₹${receipt.balance_after}` : "";
}

function setChallenge(ch) {
  S.challenge = ch;
  $("challengeDigits").textContent = ch.digits;
  let left = ch.expires_in_seconds;
  const tick = () => {
    $("challengeTimer").textContent = left > 0
      ? `Code valid for ${left}s · single use`
      : "Code expired — your next try will get a fresh code.";
    left -= 1;
  };
  tick();
  S.timer = setInterval(() => { if (left >= -1) tick(); }, 1000);
}

function startHandoffPolling() {
  const check = async () => {
    try {
      const r = await api(`/dialogue/${S.sessionId}`, undefined, "GET");
      const c = r.data.case;
      if (!c) return;
      if (c.status === "waiting_customer") {
        $("handoffStatus").textContent = `A person asks: ${c.message || "please repeat your request."}`;
      } else if (c.status === "resolved") {
        $("handoffStatus").textContent = "A person has reviewed this case. You can start a new request.";
        clearInterval(S.poll); S.poll = null;
      } else {
        $("handoffStatus").textContent = "Waiting for a person to pick this up…";
      }
    } catch { /* keep polling quietly */ }
  };
  S.poll = setInterval(check, 4000);
}

async function refreshAccount() {
  try {
    const a = await api("/account", undefined, "GET");
    $("balance").textContent = a.balance_display;
    $("acctMasked").textContent = a.account_masked;
    $("limitsLine").textContent =
      `Per payment ₹${a.per_payment_limit.toLocaleString("en-IN")} · Daily ₹${a.daily_limit.toLocaleString("en-IN")} · PIN from ₹${a.pin_threshold.toLocaleString("en-IN")}`;
    const ul = $("recentList");
    ul.replaceChildren();
    if (!a.recent.length) { const li = document.createElement("li"); li.className = "muted"; li.textContent = "None yet"; ul.appendChild(li); }
    a.recent.forEach(t => {
      const li = document.createElement("li");
      const l = document.createElement("span"); l.textContent = t.payee;
      const r = document.createElement("strong"); r.textContent = `₹${t.amount}`;
      li.append(l, r); ul.appendChild(li);
    });
  } catch { /* only fails when logged out */ }
}

/* ───────────── dialogue calls ───────────── */
async function sendTurn(payload) {
  if (S.busy) return;
  clearNotice();
  setBusy(true);
  try {
    const body = { ...payload, session_id: S.terminal ? null : S.sessionId };
    render(await api("/dialogue/turn", body));
  } catch (e) {
    notice(e.message, "error");
  } finally { setBusy(false); }
}

$("typedForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  const text = $("typedInput").value.trim();
  if (!text) { notice("Please type your request first.", "error"); return; }
  $("transcript").textContent = text;
  await sendTurn({ text, source: "text" });
  $("typedInput").value = "";
});

document.querySelectorAll("[data-example]").forEach(b =>
  b.addEventListener("click", () => { $("typedInput").value = b.dataset.example; $("typedInput").focus(); }));

/* push-to-talk */
$("pttButton").addEventListener("click", () => {
  if (S.busy) return;
  if (S.listening) { stopListening(); return; }
  if (!SpeechRecognition) {
    notice("Speech recognition isn't supported in this browser. Please type your request (Chrome supports voice).", "error");
    return;
  }
  clearNotice();
  const btn = $("pttButton");
  const started = listen({
    lang: $("language").value,
    onInterim: t => { $("transcript").textContent = t; },
    onFinal: r => { $("transcript").textContent = r.text; sendTurn({ ...r, source: "voice" }); },
    onFail: err => {
      if (err === "no-speech") sendTurn({ text: "", source: "voice" });   // silence is reported, never "confirmed"
      else if (err !== "aborted") notice(`Microphone problem (${err}). You can type instead.`, "error");
    },
    onEnd: () => { btn.classList.remove("listening"); $("pttLabel").textContent = "Tap to talk"; },
  });
  if (started) { btn.classList.add("listening"); $("pttLabel").textContent = "Listening… tap to stop"; $("transcript").textContent = "Listening…"; }
});

/* read-back buttons */
async function decide(decision) {
  if (S.busy) return;
  setBusy(true);
  try { render(await api("/dialogue/confirm", { session_id: S.sessionId, decision })); }
  catch (e) { notice(e.message, "error"); } finally { setBusy(false); }
}
$("confirmButton").addEventListener("click", () => decide("confirm"));
$("cancelButton").addEventListener("click", () => decide("cancel"));
$("replayButton").addEventListener("click", () =>
  speak(`Amount: ${$("rbAmount").textContent} rupees. In words: ${$("rbWords").textContent}. Payee: ${$("rbPayee").textContent}.`, true));
$("voiceConfirmButton").addEventListener("click", () => {
  if (S.busy) return;
  if (!SpeechRecognition) { notice("Voice isn't supported here. Tap Confirm instead.", "error"); return; }
  setTimeout(() => listen({
    lang: "en-IN",
    onFinal: r => sendTurn({ ...r, source: "voice" }),
    onFail: err => { if (err === "no-speech") sendTurn({ text: "", source: "voice" }); else if (err !== "aborted") notice(`Microphone problem (${err}).`, "error"); },
  }), 400);
});

/* liveness: record + recognise the digits */
async function submitLiveness({ transcript, blob, typed }) {
  const fd = new FormData();
  fd.append("session_id", S.sessionId);
  fd.append("challenge_id", S.challenge ? S.challenge.id : "");
  fd.append("transcript", transcript);
  fd.append("typed", String(!!typed));
  if (blob) fd.append("audio", blob, "challenge.webm");
  setBusy(true);
  try { render(await api("/dialogue/liveness", fd)); }
  catch (e) { notice(e.message, "error"); $("livenessMsg").textContent = e.message; }
  finally { setBusy(false); }
}

$("livenessMic").addEventListener("click", async () => {
  if (S.busy) return;
  if (!SpeechRecognition) { notice("Voice check needs browser speech recognition. Use the typed option below.", "error"); return; }
  let stream = null, recorder = null; const chunks = [];
  try {
    if (navigator.mediaDevices && window.MediaRecorder) {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
      recorder.start();
    }
  } catch { recorder = null; }                      // no recording -> server requires PIN
  const stopRec = () => new Promise(res => {
    const done = () => { if (stream) stream.getTracks().forEach(t => t.stop()); res(chunks.length ? new Blob(chunks, { type: (recorder && recorder.mimeType) || "audio/webm" }) : null); };
    if (!recorder || recorder.state === "inactive") return done();
    recorder.onstop = done; recorder.stop();
  });
  $("livenessMsg").textContent = "Listening… say the digits.";
  $("livenessMic").disabled = true;
  listen({
    lang: "en-IN",
    onFinal: async r => { const blob = await stopRec(); await submitLiveness({ transcript: r.text, blob, typed: false }); },
    onFail: async err => { await stopRec(); $("livenessMsg").textContent = err === "no-speech" ? "I didn't hear anything. Try again." : `Microphone problem (${err}). Try again or type the digits.`; },
    onEnd: () => { if (!S.busy) $("livenessMic").disabled = false; },
  });
});

$("liveTypedForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  const v = $("liveTypedInput").value.trim();
  if (!v) { $("livenessMsg").textContent = "Type the digits shown above."; return; }
  await submitLiveness({ transcript: v, blob: null, typed: true });
  $("liveTypedInput").value = "";
});

/* PIN / send */
$("sendButton").addEventListener("click", async () => {
  if (S.busy) return;
  const needsPin = !$("pinGroup").classList.contains("hidden");
  const pin = $("pinInput").value;
  if (needsPin && !/^\d{4,12}$/.test(pin)) { $("pinError").textContent = "Enter your 4–12 digit PIN."; $("pinInput").focus(); return; }
  setBusy(true); $("pinError").textContent = "";
  try {
    render(await api("/dialogue/execute", { session_id: S.sessionId, pin: needsPin ? pin : null }));
  } catch (e) {
    $("pinError").textContent = e.message; $("pinInput").value = "";
    if ([409, 410, 429].includes(e.status)) {   // locked / handed off / expired: show the real state
      try { render(await api(`/dialogue/${S.sessionId}`, undefined, "GET")); } catch { /* ignore */ }
    }
  } finally { setBusy(false); }
});
$("pinInput").addEventListener("keydown", ev => { if (ev.key === "Enter") $("sendButton").click(); });
$("pinCancel").addEventListener("click", async () => {
  if (S.busy) return;
  setBusy(true);
  try { render(await api("/dialogue/turn", { session_id: S.sessionId, text: "cancel", source: "text" })); }
  catch (e) { $("pinError").textContent = e.message; } finally { setBusy(false); }
});

/* result / handoff */
function resetToCapture() {
  clearTimers(); S.sessionId = null; S.terminal = false; S.challenge = null;
  showCard("capture"); clearNotice();
  $("agentMessage").textContent = ""; $("candidates").replaceChildren();
  $("transcript").textContent = "Say: “Ramesh ko paanch sau rupaye bhej do”";
  refreshAccount();
}
$("newPaymentButton").addEventListener("click", resetToCapture);
$("handoffNewButton").addEventListener("click", resetToCapture);

/* talk to a person: visible and working at every step */
$("talkToPersonButton").addEventListener("click", async () => {
  const btn = $("talkToPersonButton");
  btn.disabled = true; stopListening();
  try {
    render(await api("/dialogue/handoff", {
      session_id: S.terminal && !S.sessionId ? null : S.sessionId,
      reason: "Customer asked to talk to a person.",
      text: $("typedInput").value.trim(),
    }));
  } catch (e) { notice(`Could not reach an agent: ${e.message}`, "error"); }
  finally { btn.disabled = false; }
});

/* ───────────── login / session ───────────── */
function enterApp(user) {
  S.user = user;
  $("loginScreen").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("helpBar").classList.remove("hidden");
  $("logoutButton").classList.remove("hidden");
  resetToCapture();
  notice(`Welcome, ${user.username}. Demo mode: no real money moves.`);
}

$("loginForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  const msg = $("loginMessage"); msg.textContent = "";
  const username = $("loginUsername").value.trim(), password = $("loginPassword").value;
  if (!username || !password) { msg.textContent = "Enter your username and password."; return; }
  if (!$("consentCheck").checked) { msg.textContent = "Please accept the voice-processing notice to continue."; $("consentCheck").focus(); return; }
  try {
    const user = await api("/auth/login", { username, password, voice_consent: true });
    $("loginPassword").value = "";
    if (user.role === "agent") { window.location.href = "/agent"; return; }
    enterApp(user);
  } catch (e) { msg.textContent = e.message; }
});

$("logoutButton").addEventListener("click", async () => {
  await fetch("/auth/logout", { method: "POST" });
  stopListening(); clearTimers();
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  S.user = null; S.sessionId = null; S.terminal = false;
  $("app").classList.add("hidden"); $("helpBar").classList.add("hidden");
  $("logoutButton").classList.add("hidden"); $("loginScreen").classList.remove("hidden");
  $("loginMessage").textContent = "You have logged out.";
});

(async function init() {
  try {
    const me = await api("/auth/me", undefined, "GET");
    if (me.role === "agent") { window.location.href = "/agent"; return; }
    enterApp(me);
  } catch { /* show login */ }
})();
