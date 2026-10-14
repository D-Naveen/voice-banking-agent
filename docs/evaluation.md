# Evaluation results

## 1. Automated tests (actually run)
| Suite | Result |
|---|---|
| `pytest` (parser, intent, payee matching, liveness, spoof policy, ledger, dialogue API, agent API, auth, audit) | 149 passed |
| `scripts/e2e_ui.py` (headless Chrome, mobile viewport, every button and form, fake mic/speech engine) | 70/70 checks passed |

Demo scenarios covered by automated tests: Hinglish payment · `dedh hazaar` / `sawa do lakh` · cross-check disagreement → clarify → handoff · replayed/wrong/expired/reused liveness digits · low-confidence → human with context · spoken prompt injection (no effect) · silence/echo/background speech never confirm · new-payee, large-amount, grey-zone and clone-that-passes-detector → PIN · PIN lockout → handoff · daily/per-payment limits · access control · no PIN/audio in logs.
**Limits of these tests:** the browser speech engine and microphone are *faked*, and the spoof detector is a *fake with chosen scores*. They prove the decision logic, not recognition or detection quality.

## 2. Cloned-voice / replay / genuine test (detector layer) — **NOT YET RUN**
This requires real recordings of your own (or a consenting volunteer's) voice and a real detector, which cannot be produced in code. To fill this in:
1. Configure a detector (`SPOOF_PROVIDER`, see README).
2. Record 3 genuine, 3 loudspeaker-replayed, 3 TTS/clone clips into `samples/{genuine,replay,clone}/`.
3. `python scripts/eval_spoof.py` → writes `docs/evaluation-results.md` (sample, expected, actual, layer that decided).
4. State plainly that n = 9 is too small to call the detector reliable.

Until then, the honest statement is: *acoustic spoof detection has not been evaluated; with no detector configured every voice payment requires the PIN.*
