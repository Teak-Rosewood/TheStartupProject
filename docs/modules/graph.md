# Knowledge graph (`tsp.graph`)

A Postgres-only graph for products that need to remember **who or what is related to what, when, and on what evidence**: entities with names in several languages, claims that point at the sentence they came from, links that are valid between two dates, positions people take over time, and numeric time series that keep every revision. Examples below use products, companies and reviews; nothing in the package is specific to one domain.

Install `tsp-graph`, add `tsp.graph` to `TSP_MIGRATE_MODULES` (the migration also runs `CREATE EXTENSION pg_trgm`, which RDS and Aurora allow), and build a `GraphService(session)` per request. Every method takes an `org_id` and filters on it; tables carry `org_id` and an index on it. Row-level security is not part of this module yet (the `tsp.vector` package defines the shared pattern).

## Data model

| Table | Holds |
|---|---|
| `g_entities`, `g_aliases` | things and the names they go by (`lang` per alias; matching ignores case and Latin accents) |
| `g_documents` | sources: `source_ref`, optional `vector_document_id` (from `tsp.vector`), kind, tier, language |
| `g_claims` | a statement about an entity, with the document, `chunk_id`, quoted character range, confidence, `extracted_by` (`model@prompt-version`) and `status` (`auto`, `verified`, `rejected`) |
| `g_links` | typed edges between entities, documents or claims, valid from a date up to (not including) another; optional `evidence_claim_id` |
| `g_stances` | where a holder stood on a topic on a date (`position` from -1 to 1, or a `label`) |
| `g_series`, `g_observations` | time series; an observation is keyed by period, estimate type and vintage, so revisions never overwrite |
| `g_enrichments` | which enricher (and version) already ran on which node |

## Entities and name matching

```python
g = GraphService(session)
widget = await g.upsert_entity(org, "product", "Widget Pro", aliases=["WidgetPro", ("विजेट प्रो", "hi")])
await g.resolve(org, "widget pro max")      # exact alias match first, then trigram look-alikes
await g.resolve(org, "विजेट प्रो", lang="hi")
```

`upsert_entity` is idempotent: it finds the entity by an external id, else by an alias equal to the name, merges attributes and adds aliases. `resolve` returns candidates with a score (1.0 for an exact alias, otherwise trigram similarity above `min_score`); picking between several is up to you. Accents are removed from Latin, Greek and Cyrillic letters only; in Devanagari and similar scripts the combining marks are vowel signs and stay.

## Links that change over time

```python
g = GraphService(session, exclusive={"holds_role": "dst"})   # a role has one holder at a time
await g.link(org, ann, "holds_role", ceo, valid_from=date(2018, 1, 1))
await g.link(org, bo, "holds_role", ceo, valid_from=date(2021, 6, 1))   # closes Ann's link on that date

await g.holders(org, ceo, date(2020, 1, 1))          # [Ann]
await g.valid_at(org, ann, "holds_role", date(2021, 6, 1))   # []  (valid_to is exclusive)
```

"Latest wins" is opt-in per relation through `exclusive`: `"dst"` keeps one open link per target (one holder per role), `"src"` one per source (one group per member). Other relations overlap freely. `neighbours(org, node, rels=None, depth=1, at=None)` returns edges within `depth` hops in either direction, nearest first, each edge once; `paths(org, a, b, max_hops=3)` returns the shortest routes without revisiting a node, so cycles end. Both run as recursive queries; keep `depth` and `max_hops` small (the read API caps depth at 3).

## Claims and stances

`add_claim(org, subject_id, predicate, text, document_id=, chunk_id=, quote=(start, end), confidence=, extracted_by=)` stores the statement with its provenance. A claim has an object entity or an object value, not both. `set_stance` appends; `stances(org, topic_key, at=None)` returns each holder's latest stance as of a date.

## Time series

```python
sv = SeriesService(session)
s = await sv.upsert_series(org, "rent.city", "Average rent", unit="INR", frequency="yearly")
await sv.add_observations(org, s.id, [{"period_start": date(2020, 1, 1), "period_end": date(2020, 12, 31),
                                       "value": 100, "estimate_type": "estimate", "vintage": date(2021, 1, 1)}])
await sv.get(org, "rent.city", estimate_type="latest", as_of=date(2021, 6, 1))   # what was known then
await sv.compare(org, "rent.city", [(date(2020, 1, 1), date(2020, 12, 31)), (date(2024, 1, 1), date(2024, 12, 31))],
                 deflator_key="cpi")
```

Posting the same period, estimate type and vintage again keeps the stored value; a revision is a new vintage. `compare` takes the last observation inside each period, and returns absolute change, percent change and compound annual growth (per year, using the period end dates) from the first period to the last. With `deflator_key` the values are first restated in the money of the first period.

## On-demand enrichment

An `Enricher` has `name`, `version` and `async run(ctx, target)`. `enrich_on_demand(session, org, node, enricher)` runs it once per node, enricher and version and returns `True` if it ran. The claim on the work is a unique row, so two workers never both run it; a run that raises is marked `failed` and runs again on the next call. Bump `version` when the logic changes.

## API

`build_router(org_of, session)` mounts read-only routes under `/graph`: `GET /entities?q=`, `GET /entities/{id}/neighbours?depth=&at=&rel=` and `GET /series/{key}?start=&end=`. `org_of` is your dependency that returns the caller's organization id.

## Not built yet

Row-level security, staff console views and the data export/erasure hooks (graph data is per organization, so `GraphService.delete_org` is provided for organization deletion), embedding-based `resolve`, and a "supersedes" document helper. Add them when a product needs them.
