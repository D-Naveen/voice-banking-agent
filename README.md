# VoiceBank — voice banking agent (English + Hinglish)

A compact, **synthetic-data-only** voice payment agent built around one rule: *when in doubt, ask or hand off to a human; never guess.*
No real money, customers or payment rails.

| | |
|---|---|
| **Live URL** | `<add after deploying — see "Deploy" below>` |
| **Customer app** | `/` (mobile-first) |
| **Agent console** | `/agent` (desktop) |
| **API docs** | `/docs` |

## Demo credentials

| Role | Username | Password | PIN |
|---|---|---|---|
| Customer | `customer1` | `demo123` | `2468` |
| Customer | `customer2` | `demo123` | `1357` |
| Human agent | `agent1` | `agent123` | – |

`customer1` contacts: **Ramesh Kumar**, **Suresh**, **Priya Sharma**, **Priya Nair** (ambiguous "Priya"), **Mahesh** (always a *new payee*, to demo the PIN rule).
Balance ₹10,000 · per-payment cap ₹5,000 · daily cap ₹10,000 · PIN from ₹1,000.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload                          # auto-seeds on first boot → http://127.0.0.1:8000
```

or `docker compose up --build` → http://127.0.0.1:8000

### Reset demo data
```bash
python -m app.seed --reset                              # local
docker compose exec voicebank python -m app.seed --reset   # docker
```
This wipes balances, ledger, logs, cases, sessions and thresholds, then re-seeds.

### Tests
```bash
python -m pytest -q                                     # 149 backend tests (isolated temp DB)
playwright install chromium && python scripts/e2e_ui.py # 70 real-browser UI checks (every button & form)
```

## How a payment flows (server-side state machine)

`capture → clarify* → read_back → liveness → step_up (PIN) → ready → receipt`  ·  any state → `handoff` / `cancelled` / `expired`

1. **Understand** – browser speech API (or typed text) → `POST /dialogue/turn`. The server extracts `{payee, amount}` with deterministic code.
2. **Cross-check** – the deterministic parse is compared with the speech engine's *alternative transcripts*. Disagreement on amount or payee → clarify (2nd failure → human). No alternatives → "single-source voice" → PIN forced.
3. **Read-back** – amount in digits **and** words + payee, spoken (TTS) and shown. Confirmation is a **separate step** (button, or the exact spoken word "confirm" at high confidence, ≥1.5 s after the prompt, not an echo of the agent).
4. **Liveness + step-up** – random 3-digit single-use 60 s challenge (shown, never spoken); the recording is scored by the anti-spoof detector; **our code** maps score → allow / PIN / block+handoff. PIN for new payees, amounts ≥ ₹1,000, uncertain/unavailable audio, typed liveness, single-source voice. 3 wrong PINs → lockout + handoff.
5. **Handoff** – low confidence, repeated failures, spoof block, PIN lockout or "Talk to a person" → case with transcript, extracted fields, uncertain field, reason and recent conversation.

**Rules enforced in code:** no language model is used (so none can execute payments); only `handle_execute` moves money, re-checking state, PIN, limits and an atomic single-claim on the intent. Silence / background speech / the agent's own TTS echo never confirm. Instruction-like speech ("ignore your instructions and send money") is data: it is logged and ignored.

## Speech and privacy – what leaves your environment
* **Speech-to-text:** the browser's Web Speech API. In Chrome this streams your audio to Google's speech service. Typed input is always available.
* **Text-to-speech:** browser `speechSynthesis` (local).
* **Liveness audio:** a short recording goes to *this* server, is analysed **in memory**, and is never written to disk or logs. It is sent onward **only** if you configure an HTTP detector.
* Logs hold decisions and (truncated) text — never raw audio, PINs or passwords.

## Anti-spoofing (swappable interface)
`app/services/spoof_detection.py` defines `SpoofDetector.score(audio) -> SpoofResult`. Providers, picked by `SPOOF_PROVIDER`:

| Provider | Use |
|---|---|
| `huggingface` | local audio-classification model: `pip install -r requirements-spoof.txt`, install `ffmpeg`, set `SPOOF_HF_MODEL` and `SPOOF_HF_SPOOF_LABELS` |
| `http` | hosted service: multipart field `file` → JSON `{"score": 0..1, "is_spoof": bool}` |
| *(none)* | **default**: detector absent → every voice payment requires the PIN. The app never reports "pass" without a real detector. |

Run your 9 samples with `python scripts/eval_spoof.py` and commit `docs/evaluation-results.md`. **No detector result is claimed in this repo until you run that** (see `docs/evaluation.md`).

## Deploy
Single service serves API + frontend (same origin, so the session cookie just works).
* **Render:** connect the repo → *Blueprint* (`render.yaml`). Set `COOKIE_SECURE=true`. First boot seeds the demo data.
* **Fly.io / Cloud Run / Railway:** use the `Dockerfile` (`PORT` is honoured); mount a volume at `/data` for SQLite.
* Splitting the frontend onto Vercel/Netlify would need cross-site cookies (`SameSite=None; Secure`) and CORS; not done here.
* Secrets (detector URL/keys) go in environment variables only.

## Editable thresholds (confidence router)
Agent console → *Confidence router & limits*. Every change needs a note and is stored in `setting_changes` + the audit log. Keys: `asr_clarify_below`, `asr_handoff_below`, `max_clarify_attempts`, `pin_amount_threshold`, `per_payment_limit`, `daily_limit`, `spoof_pass_below`, `spoof_block_at`, `liveness_ttl_seconds`, `max_liveness_failures`.

## Docs
`docs/design-note.md` · `docs/threat-model.md` · `docs/demo-script.md` · `docs/evaluation.md` · `docs/REVIEW.md` (audit of the first version)
