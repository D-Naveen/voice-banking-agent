import asyncio

import pytest

from app.services import spoof_detection as sd
from app.services.spoof_detection import SpoofResult, check_audio, classify_score


class Fake:
    def __init__(self, score, is_spoof=False, boom=False):
        self.name, self._s, self._i, self._b = "fake", score, is_spoof, boom

    async def score(self, audio, filename):
        if self._b:
            raise RuntimeError("provider down")
        return SpoofResult(self._s, self._i, "fake")


def run(coro):
    return asyncio.run(coro)


def test_score_bands():
    assert classify_score(0.10) == "pass" and classify_score(0.50) == "step_up"
    assert classify_score(0.90) == "blocked" and classify_score(0.10, is_spoof=True) == "blocked"


def test_bands_use_editable_thresholds():
    assert classify_score(0.40, pass_below=0.5, block_at=0.9) == "pass"
    assert classify_score(0.40, pass_below=0.1, block_at=0.3) == "blocked"


def test_no_detector_never_passes(monkeypatch):
    for v in ("SPOOF_DETECTOR_URL", "SPOOF_PROVIDER"):
        monkeypatch.delenv(v, raising=False)
    assert run(check_audio(b"x"))["status"] == "step_up"


@pytest.mark.parametrize("det, expected", [(Fake(0.05), "pass"), (Fake(0.5), "step_up"), (Fake(0.95), "blocked")])
def test_swappable_detector_is_mapped_by_our_policy(det, expected):
    assert run(check_audio(b"audio", detector=det))["status"] == expected


def test_provider_failure_fails_safe():
    assert run(check_audio(b"audio", detector=Fake(0, boom=True)))["status"] == "step_up"


def test_missing_or_huge_audio_fails_safe():
    assert run(check_audio(b"", detector=Fake(0.0)))["status"] == "step_up"
    assert run(check_audio(b"x" * (sd.MAX_AUDIO_BYTES + 1), detector=Fake(0.0)))["status"] == "step_up"


def test_provider_selection(monkeypatch):
    monkeypatch.setenv("SPOOF_PROVIDER", "http"); monkeypatch.setenv("SPOOF_DETECTOR_URL", "http://x/y")
    assert sd.get_detector().name == "http"
    monkeypatch.setenv("SPOOF_PROVIDER", "huggingface"); monkeypatch.setenv("SPOOF_HF_MODEL", "org/model")
    assert sd.get_detector().name == "huggingface"
    monkeypatch.setenv("SPOOF_PROVIDER", "none")
    assert sd.get_detector() is None
