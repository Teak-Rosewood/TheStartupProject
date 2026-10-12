# K2 Knowledge graph (`tsp.graph`)

**Goal:** a Postgres-backed graph of entities, claims with provenance, typed links with validity dates, stances, and numeric time series, with queries for neighbours, short paths and "valid at date", plus an on-demand enrichment hook. Generic: examples are products, companies and reviews.

**Depends on:** phase 8 (chunks to cite). Can run alongside K1. **Branch:** `feat/k2-graph`. **Size:** medium.

## Files
`python/packages/graph/` with `src/tsp/graph/{__init__,models,service,queries,resolve,enrich,series,api,admin,compliance}.py`, `migrations/versions/graph_0001_init.py`, `tests/`.

## Data model
All tables have `org_id` (RLS like tsp.vector) and `created_at`.
| Table | Columns |
|---|---|
| `g_entities` | id, kind (free text: `person`, `org`, `product`, `document`...), name, attrs jsonb, external_ids jsonb |
| `g_aliases` | entity_id, alias, lang, normalised (lowercase, unaccented); index on (org_id, normalised) with trigram GIN |
| `g_documents` | id, source_ref (e.g. `src_items.id`), vector_document_id, title, published_at, kind, tier, lang, attrs |
| `g_claims` | id, text, subject_id, predicate, object_id nullable, object_value jsonb nullable, document_id, chunk_id, quote_start, quote_end, confidence, extracted_by (model + prompt version), status (`auto`/`verified`/`rejected`) |
| `g_links` | id, src_kind (`entity`/`document`/`claim`), src_id, rel (free text: `cites`, `amends`, `supersedes`, `member_of`, `holds_role`), dst_kind, dst_id, valid_from date, valid_to date, attrs, evidence_claim_id |
| `g_stances` | id, holder_id (entity), topic_key, position numeric (-1..1) or label, as_of date, evidence_claim_id, confidence |
| `g_series` | id, key (unique per org), name, unit, frequency, source_ref, attrs |
| `g_observations` | series_id, period_start, period_end, value numeric, estimate_type (free text: `estimate`, `revised`, `actual`), vintage date, attrs; pk (series_id, period_start, estimate_type, vintage) |
| `g_enrichments` | target_kind, target_id, enricher, version, status, ran_at; unique (target_kind, target_id, enricher, version) |

## Interfaces
```python
GraphService(session):
    upsert_entity(org_id, kind, name, aliases=[], attrs={}, external_ids={}) -> Entity
    resolve(org_id, mention: str, kind=None, lang=None) -> list[Candidate]   # alias exact → trigram → optional embedding
    add_document(...), add_claim(...), link(org_id, src, rel, dst, valid_from=None, valid_to=None, evidence=None)
    set_stance(org_id, holder_id, topic_key, position, as_of, evidence_claim_id)
    neighbours(org_id, node, rels=None, depth=1, at: date | None = None) -> list[Edge]
    paths(org_id, a, b, max_hops=3, rels=None, at=None) -> list[Path]          # recursive CTE, cycle-safe
    valid_at(org_id, node, rel, at: date) -> list[Edge]                         # valid_from <= at < coalesce(valid_to, inf)
    holders(org_id, role_entity, at: date) -> list[Entity]                      # "who holds which position"
    stances(org_id, topic_key, at=None) -> list[Stance]

SeriesService(session):
    upsert_series(...), add_observations(series_id, rows)                        # keeps vintages, never overwrites
    get(series_key, start, end, estimate_type="latest") -> list[Observation]
    compare(series_key, periods: list[tuple[date,date]]) -> Comparison           # absolute, % change, CAGR; inflation-adjust given a deflator series key

Enricher protocol: name, version, async run(ctx, target) -> None
enrich_on_demand(org_id, target, enricher) -> bool   # runs once per (target, enricher, version), cached in g_enrichments
```
Validity: a link with `rel="supersedes"` closes the superseded link's `valid_to` (generic "latest wins" helper, opt-in per rel).

API: read-only `GET /api/graph/entities?q=`, `/entities/{id}/neighbours?depth=&at=`, `/series/{key}?start=&end=`. Admin views for entities, claims, links, series.

## Tests
- `valid_at` returns the right link before, on and after a change date.
- `holders` returns the correct holder for 3 dates across 2 handovers (sample: product owners or company CEOs).
- `paths` finds a 3-hop path and stops at cycles.
- Revised observation keeps the earlier vintage; `compare` maths on a fixture.
- `resolve` matches aliases in two languages.
- Enrichment runs once, then is cached; bumping version re-runs.

## Done when
"Valid at date" and "who holds which position" pass on the sample dataset; `docs/modules/graph.md` written.

## Prompt
```
Repository: Teak-Rosewood/TheStartupProject. Branch: feat/k2-graph.
Read docs/plans/K2-graph.md, docs/plans/README.md and tsp.vector (for RLS and chunk ids).
Implement the brief with Postgres only (recursive CTEs, pg_trgm). Neutral sample data
(products, companies). Run lint, types and tests; open a PR with the checklist.
```
