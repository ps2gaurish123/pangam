"""Scoring and status logic for story verification."""

from dataclasses import dataclass


@dataclass
class ScoreInputs:
    source_reliability: float
    independent_sources: float
    official_confirmation: float
    consistency: float
    recency_clarity: float
    historical_accuracy: float


WEIGHTS = {
    "source_reliability": 0.25,
    "independent_sources": 0.20,
    "official_confirmation": 0.25,
    "consistency": 0.15,
    "recency_clarity": 0.10,
    "historical_accuracy": 0.05,
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_confidence_score(inputs: ScoreInputs) -> int:
    weighted = (
        _clamp(inputs.source_reliability) * WEIGHTS["source_reliability"]
        + _clamp(inputs.independent_sources) * WEIGHTS["independent_sources"]
        + _clamp(inputs.official_confirmation) * WEIGHTS["official_confirmation"]
        + _clamp(inputs.consistency) * WEIGHTS["consistency"]
        + _clamp(inputs.recency_clarity) * WEIGHTS["recency_clarity"]
        + _clamp(inputs.historical_accuracy) * WEIGHTS["historical_accuracy"]
    )
    return int(round(weighted * 100))


def derive_status(score: int, has_conflict: bool, source_count: int, has_official: bool) -> str:
    if has_conflict:
        return "Conflicting"
    if score >= 85 and (has_official or source_count >= 3):
        return "Verified"
    if score >= 70 and source_count >= 2:
        return "Likely True"
    if source_count <= 1 and score < 60:
        return "Unverified"
    return "Developing"
