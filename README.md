# Pangam - News Fact-Checking System Blueprint

This repository now includes a production-oriented blueprint for building a **Web Scraping and Fact-Checking System for Latest News**.

## Added in this iteration

- A structured architecture and implementation design document.
- A source onboarding CSV template for trusted feed setup.
- A starter Python scoring module that computes confidence (0–100) and assigns verification status.

## Quick start (scoring module)

```bash
python - <<'PY'
from news_factcheck.scoring import ScoreInputs, compute_confidence_score, derive_status

inputs = ScoreInputs(
    source_reliability=0.92,
    independent_sources=0.75,
    official_confirmation=1.0,
    consistency=0.80,
    recency_clarity=0.90,
    historical_accuracy=0.85,
)
score = compute_confidence_score(inputs)
print("score=", score)
print("status=", derive_status(score, has_conflict=False, source_count=3, has_official=True))
PY
```

## Project docs

- `docs/news_factcheck_system_design.md`
- `docs/source_onboarding_template.csv`
- `news_factcheck/scoring.py`


## Windows local run

Use the included batch files:

1. `install_requirements.bat`  
   Creates `.venv`, upgrades `pip`, and installs `requirements.txt`.
2. `start.bat`  
   Offers two options:
   - Run directly from the current folder.
   - Download (git clone) and then run locally.

After setup, the starter demo executes:

```bat
.venv\Scripts\python.exe -m news_factcheck.demo
```
