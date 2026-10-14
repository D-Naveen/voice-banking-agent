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
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger("voicebank.spoof")

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
    """Wraps a Hugging Face audio anti-spoofing model."""

    name = "huggingface"
    _pipe = None
    _loaded_model_id = None

    def __init__(self, model_id: str, spoof_labels: set[str]):
        self.model_id = model_id
        self.spoof_labels = {
            label.casefold() for label in spoof_labels
        }

        if not self.spoof_labels:
            raise ValueError(
                "At least one spoof label must be configured."
            )

    def _load(self):
        if (
            HuggingFaceDetector._pipe is None
            or HuggingFaceDetector._loaded_model_id != self.model_id
        ):
            from transformers import pipeline

            HuggingFaceDetector._pipe = pipeline(
                "audio-classification",
                model=self.model_id,
            )
            HuggingFaceDetector._loaded_model_id = self.model_id

        return HuggingFaceDetector._pipe

    def _run(self, audio: bytes, filename: str) -> SpoofResult:
        if not audio:
            raise ValueError("No audio data was provided.")

        suffix = os.path.splitext(filename)[1] or ".webm"
        fd, path = tempfile.mkstemp(suffix=suffix)

        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(audio)

            results = self._load()(path, top_k=None)

            if not results:
                raise ValueError(
                    "The model returned no predictions."
                )

            fake_scores = [
                float(item["score"])
                for item in results
                if item["label"].casefold() in self.spoof_labels
            ]

            if not fake_scores:
                labels = [item["label"] for item in results]
                raise ValueError(
                    f"No configured spoof label found. "
                    f"Received labels: {labels}"
                )

            spoof_score = sum(fake_scores)

            if not 0.0 <= spoof_score <= 1.0:
                raise ValueError(
                    "The model returned an invalid spoof score."
                )

            return SpoofResult(
                float(spoof_score),
                False,
                self.name,
            )

        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    async def score(
        self,
        audio: bytes,
        filename: str,
    ) -> SpoofResult:
        return await asyncio.to_thread(
            self._run,
            audio,
            filename,
        )


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
    except Exception as exc:
        log.exception("anti-spoof detector failed")      # traceback goes to server logs, never the audio
        return {"status": "step_up", "score": None, "provider": detector.name,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                "message": "The anti-spoof detector failed; PIN step-up is required."}
    status = classify_score(res.score, res.is_spoof, pass_below, block_at)
    return {"status": status, "score": round(res.score, 4), "provider": detector.name,
            "message": {"pass": "Audio check passed.",
                        "step_up": "Audio risk is uncertain; PIN step-up is required.",
                        "blocked": "Audio check flagged a possible spoof."}[status]}
