"""Simple CLI demo runner for the news_factcheck scoring module."""

from news_factcheck.scoring import ScoreInputs, compute_confidence_score, derive_status


def main() -> None:
    sample = ScoreInputs(
        source_reliability=0.92,
        independent_sources=0.75,
        official_confirmation=1.0,
        consistency=0.80,
        recency_clarity=0.90,
        historical_accuracy=0.85,
    )
    score = compute_confidence_score(sample)
    status = derive_status(
        score=score,
        has_conflict=False,
        source_count=3,
        has_official=True,
    )

    print("=== News Fact-Checking Demo ===")
    print(f"Confidence score: {score}/100")
    print(f"Status: {status}")


if __name__ == "__main__":
    main()
