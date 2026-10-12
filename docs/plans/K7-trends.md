# K7 Trends (`tsp.trends`)

**Goal:** a signal framework that gathers interest signals for a set of topics, scores them, and produces a daily candidate list for an editor. Generic example: food topics.

**Depends on:** K1 (connectors and the scrape-first ladder). **Branch:** `feat/k7-trends`. **Size:** small.

## Data model
| Table | Columns |
|---|---|
| `tr_topics` | id, org_id, key, label, keywords text[], langs text[], regions text[], active |
| `tr_signals` | id, org_id, topic_id nullable, signal (plug-in name), region, at, value numeric, baseline numeric, attrs jsonb (e.g. top URLs) |
| `tr_candidates` | id, org_id, day, topic_id nullable, label, score, components jsonb (per-signal z-scores), status (`new`/`picked`/`dismissed`), picked_by, note |

## Interfaces
```python
class Signal(Protocol):
    name: str
    async def collect(self, ctx, topics: list[Topic], since: datetime) -> list[SignalPoint]

Built-ins:
  SearchTrendsSignal    # scrape public trends pages via tsp.crawl first; API adapter when configured (K1 ladder)
  FeedVolumeSignal      # RSS/Atom feeds from config: counts matching items per topic per hour
  GdeltVolumeSignal     # GDELT DOC API counts per keyword (free)
  CustomFeedSignal      # any K1 source stream mapped to topics by keyword or classifier hook
  EmergingTermsSignal   # new frequent n-grams in feeds not matching any topic -> new candidate labels

Scorer: weights per signal (config); z-score vs 28-day baseline; recency decay; dedupe near-identical labels (embedding similarity via tsp.vector, threshold 0.9)
TrendsService.scan(org_id, day) -> list[Candidate]; pick(candidate_id, user, note); dismiss(...)
```
Jobs: `trends.collect` (hourly per signal), `trends.scan` (daily, writes candidates, notifies editors via `tsp.notify`). API: `GET /api/trends/candidates?day=`, `POST /api/trends/candidates/{id}/pick|dismiss`. Admin view.

## Tests
Scorer maths on fixtures; spike detection; emerging term creates an unmatched candidate; dedupe; collect falls back from scrape to API via K1.

## Done when
Candidates appear daily for a sample topic set; `docs/modules/trends.md` written.

## Prompt
```
Repository: Teak-Rosewood/TheStartupProject. Branch: feat/k7-trends.
Read docs/plans/K7-trends.md, docs/plans/README.md, tsp.sources and tsp.crawl. Implement
the brief with food-topic fixtures. No live network in tests. Run checks; open a PR.
```
