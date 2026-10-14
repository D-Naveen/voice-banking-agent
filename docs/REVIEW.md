# Review of the first version (what was wrong, what changed)

## Verdict
A tidy, honest foundation (hashed passwords/PINs, double-entry ledger, audit log, no fabricated results) — but it **did not meet the assignment** and had **money-safety bugs**.

## Bugs that mattered
| # | Finding (original) | Fix |
|---|---|---|
| 1 | Parser turned **"1,500" into ₹501** (comma → space → 1 + 500) | normalise commas inside numbers; tested |
| 2 | **"paanch sau paanch sau" → ₹50,500; "five five" → ₹10** | ambiguous constructions refused |
| 3 | The assignment's own demo phrase **"Ramesh ko paanch sau rupaye bhej do" failed** ("ko"/"bhej" unsupported) and, if they had been skipped, **"do" (verb) would have added 2 → ₹502** | command words stripped; verb "do" handled |
| 4 | Missing `dedh sau`, `dhai hazaar`, `pachees`, `sade`, `paune`, Devanagari | full Hindi 1–99, fractions, Devanagari |
| 5 | A blocked spoof result could be **overwritten** by re-sending different audio | block is terminal (handoff state) |
| 6 | Payee list **hard-coded in browser JS**; client decided PIN need and sent `read_back_*` values | all logic server-side |
| 7 | README claimed "29 tests pass"; **1 failed** and tests used the dev database | isolated temp DB, 149 tests |
| 8 | Sessions and liveness challenges in memory (lost on restart / multi-worker) | DB-backed |

## Assignment requirements that were missing
Turn endpoint & dialogue state machine · confidence router with editable, logged thresholds · fuzzy payee match/ambiguity · new-payee PIN · **daily limit (was deliberately disabled)** · liveness *digits* · spoken read-back (digits + words) · separate confirm step · silence/echo rules · automatic handoff on low confidence/disagreement · spoof detector *interface with a real provider option* (there was none) · consent notice · push-to-talk with live transcript · success/failure audio · "Talk to a person" always visible · agent case view with highlighted uncertain field · agent threshold editing · reset command · deployment files · threat model/design/demo/evaluation docs.

## Still open (needs you)
1. **Deploy** and put the URL in the README (I cannot create hosting accounts).
2. **Pick, install and evaluate a real spoof detector**; record the 9 samples; run `scripts/eval_spoof.py`. The HuggingFace provider is written but **untested here** (model weights are not reachable from this sandbox).
3. Docker files were written but **not built here** (no Docker in the sandbox).
4. Test on a real phone with Chrome: the fake speech engine in the E2E test cannot reveal real recognition quirks (e.g. Hindi returning Devanagari, Android mic exclusivity during liveness).
