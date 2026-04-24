# news_factcheck module

This folder contains a minimal Python module for confidence scoring/status assignment.

## Usage

```python
from news_factcheck.scoring import ScoreInputs, compute_confidence_score, derive_status

inputs = ScoreInputs(
    source_reliability=0.9,
    independent_sources=0.8,
    official_confirmation=1.0,
    consistency=0.85,
    recency_clarity=0.9,
    historical_accuracy=0.8,
)

score = compute_confidence_score(inputs)
status = derive_status(score, has_conflict=False, source_count=4, has_official=True)
print(score, status)
```
