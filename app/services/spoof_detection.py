"""Swappable anti-spoofing interface.

    SpoofDetector.score(audio_bytes) -> SpoofResult(score in 0..1, higher = more likely spoof)

Providers (choose with SPOOF_PROVIDER = none | http | huggingface):
  * none         - nothing configured; the safe result is step-up (PIN), never "pass".
  * http         - POST multipart 'file' to SPOOF_DETECTOR_URL, JSON {"score": .., "is_spoof": ..}
  * huggingface  - local audio-classification model (see requirements-spoof.txt).
                   SPOOF_HF_MODEL = model id, SPOOF_HF_SPOOF_LABELS = labels meaning "spoof".

Our code (not the detector) maps the score to allow / step_up / block using the
editable thresholds, so a provider can be swapped without touching the policy.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from typing import Protocol

import httpx

PASS_SCORE = 0.30
BLOCK_SCORE = 0.70
MAX_AUDIO_BYTES = 5 * 1024 * 1024


@dataclass
class SpoofResult:
    score: float
    is_spoof: bool = False
    provider: str = ""


class SpoofDetector(Protocol):
    name: str

    async def score(self, audio: bytes, filename: str) -> SpoofResult: ...


def classify_score(score: float, is_spoof: bool = False,
                   pass_below: float = PASS_SCORE, block_at: float = BLOCK_SCORE) -> str:
    """Map a detector score to a payment decision: pass | step_up | blocked."""
    if is_spoof or score >= block_at:
        return "blocked"
    if score <= pass_below:
        return "pass"
    return "step_up"


class HttpDetector:
    name = "http"

    def __init__(self, url: str):
        self.url = url

    async def score(self, audio: bytes, filename: str) -> SpoofResult:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(self.url, files={"file": (filename, audio, "application/octet-stream")})
            r.raise_for_status()
            data = r.json()
        return SpoofResult(float(data["score"]), bool(data.get("is_spoof", False)), self.name)


class HuggingFaceDetector:
    """Wraps a Hugging Face audio-classification model. Loaded lazily, cached."""

    name = "huggingface"
    _pipe = None

    def __init__(self, model_id: str, spoof_labels: set[str]):
        self.model_id, self.spoof_labels = model_id, spoof_labels

    def _load(self):
        if HuggingFaceDetector._pipe is None:
            from transformers import pipeline  # optional heavy dependency
            HuggingFaceDetector._pipe = pipeline("audio-classification", model=self.model_id)
        return HuggingFaceDetector._pipe

    def _run(self, audio: bytes, filename: str) -> SpoofResult:
        suffix = os.path.splitext(filename)[1] or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:   # deleted immediately
            tmp.write(audio)
            tmp.flush()
            results = self._load()(tmp.name, top_k=None)
        score = sum(r["score"] for r in results if r["label"].casefold() in self.spoof_labels)
        return SpoofResult(min(1.0, float(score)), False, self.name)

    async def score(self, audio: bytes, filename: str) -> SpoofResult:
        return await asyncio.to_thread(self._run, audio, filename)


def get_detector() -> SpoofDetector | None:
    provider = os.getenv("SPOOF_PROVIDER", "").strip().lower()
    url = os.getenv("SPOOF_DETECTOR_URL", "").strip()
    if provider == "huggingface":
        labels = {x.strip().casefold() for x in os.getenv(
            "SPOOF_HF_SPOOF_LABELS", "fake,spoof,deepfake,ai,synthetic").split(",") if x.strip()}
        model = os.getenv("SPOOF_HF_MODEL", "").strip()
        return HuggingFaceDetector(model, labels) if model else None
    if provider == "http" or (not provider and url):
        return HttpDetector(url) if url else None
    return None


async def check_audio(audio_bytes: bytes | None, filename: str = "challenge.webm",
                      pass_below: float = PASS_SCORE, block_at: float = BLOCK_SCORE,
                      detector: SpoofDetector | None = None) -> dict:
    """Run the detector and map to a decision. Any failure fails SAFE (step-up)."""
    detector = detector if detector is not None else get_detector()
    if detector is None:
        return {"status": "step_up", "score": None, "provider": "none",
                "message": "No anti-spoof detector is configured; PIN step-up is required."}
    if not audio_bytes:
        return {"status": "step_up", "score": None, "provider": detector.name,
                "message": "No audio was available to check; PIN step-up is required."}
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        return {"status": "step_up", "score": None, "provider": detector.name,
                "message": "Audio was too large to check; PIN step-up is required."}
    try:
        res = await detector.score(audio_bytes, filename)
    except Exception:
        return {"status": "step_up", "score": None, "provider": detector.name,
                "message": "The anti-spoof detector failed; PIN step-up is required."}
    status = classify_score(res.score, res.is_spoof, pass_below, block_at)
    return {"status": status, "score": round(res.score, 4), "provider": detector.name,
            "message": {"pass": "Audio check passed.",
                        "step_up": "Audio risk is uncertain; PIN step-up is required.",
                        "blocked": "Audio check flagged a possible spoof."}[status]}
