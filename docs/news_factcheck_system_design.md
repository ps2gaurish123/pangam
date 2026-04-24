# Web Scraping and Fact-Checking System for Latest News

## 1) Objective
Build an automated platform that continuously collects recent news (default: last 12 hours), clusters duplicate stories, extracts claims, compares claims across sources, and outputs a verification label plus confidence score.

## 2) MVP Boundaries
### In scope
- 25–50 trusted sources (RSS/API first, scraping only where allowed).
- Last-12-hour ingestion window.
- English language only.
- Core categories: AI, Business, Real Estate, Finance, Policy, Global, India.
- Duplicate clustering at story level.
- Basic claim extraction and cross-source comparison.
- Confidence scoring (0–100).
- Dashboard/API output + CSV/PDF export.
- Email alerts for selected triggers.

### Out of scope (MVP)
- Paywalled/private content extraction.
- CAPTCHA/login bypass.
- Multilingual NLP.
- Deepfake/media-forensics.
- Legal guarantee of truth.

## 3) High-Level Architecture
1. **Collector Layer**
   - RSS/Atom readers.
   - Official API connectors.
   - Website extractors (robots.txt and ToS compliant).
2. **Processing Layer**
   - Boilerplate/content extraction.
   - NER (people/org/place/date/amount).
   - Claim extraction and canonicalization.
   - Story deduplication/clustering.
3. **Verification Layer**
   - Cross-source claim matcher.
   - Contradiction/consistency detector.
   - Confidence score engine.
4. **Serving Layer**
   - FastAPI endpoints.
   - Dashboard read models.
   - Export and alert services.

## 4) Data Model (Core Entities)
- `Source` (name, domain, category, region, trust_level, fetch_method, active)
- `Article` (source_id, title, url, published_at, updated_at, author, summary, text_hash)
- `StoryCluster` (cluster_id, canonical_headline, first_seen_at, last_seen_at)
- `Claim` (cluster_id, normalized_claim, claim_type, value_json)
- `ClaimEvidence` (claim_id, article_id, stance, extracted_value_json)
- `VerificationResult` (cluster_id, status, confidence_score, explanation_json)

## 5) Verification Status Taxonomy
- **Verified**: confirmed by multiple reliable/official sources.
- **Likely True**: reported by reliable independent sources; no direct official confirmation yet.
- **Developing**: very recent story, evidence still sparse.
- **Conflicting**: material contradictions across credible sources.
- **Unverified**: only weak/single-source evidence.
- **False/Incorrect**: contradicted by stronger official or highly credible evidence.

## 6) Confidence Scoring Formula (Initial)
Weighted factors:
- Source reliability: 25%
- Independent source count: 20%
- Official confirmation: 25%
- Cross-source consistency: 15%
- Recency/timestamp clarity: 10%
- Historical source accuracy: 5%

`score = Σ(weight_i × factor_i)` where each factor is normalized to `[0.0, 1.0]`.

## 7) Pipeline Workflow
1. Fetch feeds/sources every 15–30 minutes.
2. Keep only records with `published_at >= now() - 12h`.
3. Extract article body/summary + entities.
4. Generate claim candidates.
5. Cluster similar articles into story events.
6. Build claim-evidence matrix across sources.
7. Compute status + confidence score.
8. Persist and publish via API/dashboard.
9. Trigger alerts (breaking/high-confidence/conflicting/keyword).

## 8) Compliance and Safety Controls
- Respect `robots.txt` and source ToS.
- Prefer feeds/APIs over scraping.
- Never bypass authentication or paywalls.
- Store metadata/summary if full-text rights are unclear.
- Preserve original links + timestamps for audit.
- Add domain blacklist + manual source moderation.

## 9) Non-Functional Requirements
- **Freshness**: new items visible within 5 minutes after fetch cycle.
- **Reliability**: idempotent ingest and retry queue.
- **Traceability**: all scores explainable with evidence links.
- **Observability**: ingestion latency, source health, extraction failures.
- **Security**: role-based admin actions and audit trail.

## 10) Suggested Tech Stack
- Backend: Python, FastAPI, Celery, Redis.
- Storage: PostgreSQL (+ optional Elasticsearch for search).
- NLP/AI: LLM + NER + contradiction classifier.
- Frontend: React/Next.js.
- Deployment: Docker + managed Postgres + Redis.

## 11) Acceptance Criteria (MVP)
- Can ingest from approved sources and maintain source attribution.
- Shows last-12-hour news only (configurable).
- Clusters duplicates at story level.
- Extracts claims and compares across sources.
- Assigns status + confidence score with reasons.
- Supports filter/search/export and basic alerts.
