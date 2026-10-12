# `tsp.trends` — interest signals and a daily candidate list

Collects interest signals for a set of topics, scores them against a 28-day baseline, and writes a daily list of candidates for an editor to pick or dismiss. The examples use food topics; nothing in the package is specific to them. Plan: [docs/plans/K7-trends.md](../plans/K7-trends.md). Builds on [`tsp.sources`](sources.md) and `tsp.crawl`.

## Pieces

| Piece | What it does |
|---|---|
| `tr_topics` | What to watch: key, label, keywords, languages, regions, active. Added with `TrendsService.upsert_topic`. |
| `Signal` plug-ins | `collect(ctx, topics, since)` returns points (time, value, topic). Stored in `tr_signals`. |
| Scorer | Turns stored points into a score per topic (maths in `tsp.trends.scorer`, no I/O). |
| `tr_candidates` | The editor's list for a day: label, score, per-signal components, status `new`, `picked` or `dismissed`. |

## Built-in signals

- `FeedVolumeSignal(feeds)`: items per hour in RSS/Atom feeds whose title matches a topic's keywords.
- `GdeltVolumeSignal()`: coverage volume per topic from the free GDELT DOC API (no key). The response parser follows GDELT's documented `timelinevol` format but has not been run against the live service.
- `SearchTrendsSignal(rungs)`: search interest, cheapest method first. Each rung is tried in order and the first that answers wins, like the K1 ladder. `scrape_rung(url_for_region, regions)` reads a public "trending searches" RSS through `tsp.crawl` (robots.txt, limits, SSRF guard); add your own API rung after it for when the page is blocked.
- `CustomFeedSignal(source_key, stream)`: counts items a `tsp.sources` stream stored per hour, matched to topics by keyword in the item title, or by your `classify(title, metadata)` hook.
- `EmergingTermsSignal(feeds)`: phrases (1-3 words) that recur in feed titles and match no topic. They are stored with no topic and become candidates labelled with the phrase. When a shorter phrase only appears inside a longer one, the longer one is kept.

Write your own by implementing `name` and `collect`. A signal that raises is logged and skipped; the others still run.

## Scoring

For each topic and signal, the value for a day is the sum of its points in the 24 hours ending at the end of that UTC day, each halved for every 12 hours of age, so a burst this morning outweighs the same burst last night. The z-score compares that with the same measure for each of the previous 28 days. Details that matter:

- Fewer than 7 days of history gives no score, not a guess. Days before a signal's first point are treated as unknown, not zero, so a new signal does not look like a spike.
- The spread has a floor of 10% of the mean (at least 1), so a flat history does not turn a tiny change into a huge score. Scores are capped at 10.
- A candidate's score is the weighted sum of the **positive** z-scores (`TrendsConfig.weights`, default 1 each). A quiet signal does not cancel a loud one.
- Only candidates at or above `min_score` (default 2) are written, at most `max_candidates` (50).
- Near-identical labels are merged, keeping the highest score and the larger value of each signal. With an `embed` function (texts to vectors, for example the `tsp.vector` embedder) similarity is cosine at 0.9; without one, labels merge when their words are identical.

Re-running a day's scan refreshes the scores of candidates still `new` and never touches picked or dismissed ones. `notify(org_id, day, new_candidates)` is called only for candidates the scan created, so a re-run does not notify twice.

## Jobs, API, admin

- `register_jobs(registry, service)`: `trends.collect` hourly (every signal for every organization with active topics; each collect re-reads the last day and replaces the overlap rather than double counting), `trends.scan` daily at 05:00 UTC for the day just ended, `trends.prune` daily (signals older than `keep_days`, default 60).
- `build_router(service, account, org_member, session)`: `GET /orgs/{org_id}/trends/candidates?day=`, `POST .../candidates/{id}/pick` and `/dismiss` with an optional note. A decision is final (a second one gets 409), scoped to the organization, and written to the audit log.
- `tsp.trends.admin` (extra `admin`): read-only views of topics, signals and candidates.

## Setup

`upgrade(url, app_migrations, modules=["tsp.trends"])`. Build `TrendsService(db, http, signals={...}, config=TrendsConfig(...), embed=..., notify=...)` where `http` is a `tsp.crawl.HttpFetcher`. Email the editors from `notify` with `tsp.notify`; the service does not depend on it.

## Limits

- Signal rows are not partitioned by region in scoring: regions are stored but a topic's score pools them.
- The first scan after setup has little history, so scores appear only once 7 days of points exist.
