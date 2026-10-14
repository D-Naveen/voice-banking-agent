"""Run recorded samples through the configured anti-spoof detector and print the results table.

Folder layout (you record these yourself; use ONLY your own / a consenting volunteer's voice):
    samples/genuine/*.wav|webm|mp3     3 live recordings of you
    samples/replay/*.wav|webm|mp3      3 recordings of a loudspeaker playing a genuine clip
    samples/clone/*.wav|webm|mp3       3 TTS / voice-clone clips

    python scripts/eval_spoof.py [samples_dir]      # writes docs/evaluation-results.md

This measures the detector layer only. Replay-vs-challenge and PIN/limit behaviour are covered by tests.
"""
import asyncio, os, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from app.services.spoof_detection import BLOCK_SCORE, PASS_SCORE, check_audio, get_detector

EXPECT = {
    "genuine": ("pass or step_up (must not be blocked)", lambda s: s != "blocked"),
    "replay": ("step_up or blocked (must not pass)", lambda s: s != "pass"),
    "clone": ("step_up or blocked (must not pass)", lambda s: s != "pass"),
}


async def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
    det = get_detector()
    provider = det.name if det else "none"
    rows = []
    for kind, (expected, ok) in EXPECT.items():
        for f in sorted((root / kind).glob("*.*")):
            res = await check_audio(f.read_bytes(), f.name, PASS_SCORE, BLOCK_SCORE)
            layer = "anti-spoof score" if res["score"] is not None else "fallback -> PIN step-up (detector unavailable)"
            rows.append((f.name, kind, expected, res["status"], res["score"], layer, ok(res["status"])))
    if not rows:
        sys.exit(f"No samples found under {root}/. See the docstring for the layout.")
    lines = [f"# Spoof-detector evaluation (provider: `{provider}`)", "",
             f"Thresholds: pass <= {PASS_SCORE}, block >= {BLOCK_SCORE}. Sample size: {len(rows)} (very small).", "",
             "| Sample | Kind | Expected | Actual | Score | Layer that decided | Met expectation |", "|---|---|---|---|---|---|---|"]
    for n, k, e, a, sc, layer, good in rows:
        lines.append(f"| {n} | {k} | {e} | {a} | {sc} | {layer} | {'yes' if good else 'NO'} |")
    lines += ["", f"{sum(r[-1] for r in rows)}/{len(rows)} met expectation. This tiny sample cannot establish that the detector is reliable."]
    out = "\n".join(lines)
    print(out)
    pathlib.Path("docs/evaluation-results.md").write_text(out + "\n", encoding="utf-8")


asyncio.run(main())
