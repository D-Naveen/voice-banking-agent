# Threat model (half page)

| Threat | What stops it | What it does *not* stop |
|---|---|---|
| **Recorded replay** | Random 3-digit, single-use, 60 s challenge bound to the session (an old recording says old digits); optional audio detector; PIN rules | A recording that happens to contain the current digits (impossible in practice unless made live → that is a clone/relay attack) |
| **Voice cloning** | Anti-spoof score (allow / PIN / block+handoff). **A clone that passes the spoof check is stopped by the PIN step-up** (new payee, ≥ ₹1,000, uncertain audio, typed or single-source input), **the per-payment/daily limits**, and **human handoff** on any anomaly (PIN lockout after 3 tries opens a case) | A clone + stolen PIN, for amounts < ₹1,000 to existing payees: bounded by limits only |
| **Misheard amount/payee** | Deterministic parser refuses ambiguous constructions ("paanch sau paanch sau"); two-method cross-check; read-back in digits and words; separate confirmation; fuzzy names are suggestions; ambiguity asks | Both methods mishearing identically *and* the customer confirming a wrong read-back |
| **Social engineering of the agent** | Agent actions only update the case (never move money); a note is mandatory; all actions audited; case shows full context | A fooled agent may approve/reject wrongly; real systems need dual control and callback verification |
| **Prompt injection by voice** | No LLM. Speech can only fill `{payee, amount}`; instruction-like text is flagged, logged (`prompt_injection_ignored`) and ignored in every state | – |
| **Echo / background speech / silence** | Echo detection, ≥ 1.5 s delay, strict "confirm" phrase + known high confidence, silence counts as no-confirmation and cancels after repeated attempts | Someone nearby saying "confirm" clearly during the window (then liveness + PIN still follow) |
| **Account/PIN guessing** | PBKDF2 hashes, 3-try PIN lockout + handoff, login throttling (5 fails/5 min) | – |

## What a third-party speech provider sees
Chrome's Web Speech API sends the **audio** of everything you speak into the mic during push-to-talk and liveness to Google's speech service (and receives text). The provider therefore sees amounts, payee names and the liveness digits. If `SPOOF_PROVIDER=http` is configured, the challenge recording is also sent to that service. This app stores no audio; the providers' retention follows their own terms. Typed input avoids all audio egress.

## Known gaps
In-memory-free but still single-node SQLite; no device binding; no formal pen-test; detector accuracy unknown until evaluated.
