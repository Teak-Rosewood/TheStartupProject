# K8 Languages and distribution

**Goal:** apps can serve content in several languages (including Hindi) with language-aware URLs and search, and distribute it: newsletters, share-card images, SEO structured data, and labelled AI-generated illustrations.

**Depends on:** phase 8, K4 (`cms_variants`), `tsp.notify`. **Branch:** `feat/k8-<part>`. **Size:** medium; two pull requests (K8-A languages, K8-B distribution).

## K8-A Languages (`tsp.i18n` + search config)
- Package `tsp.i18n`: message catalogs (`locales/<lang>/messages.po` via Babel), `t(key, lang, **vars)`, language negotiation (URL prefix `/<lang>/` > cookie > `Accept-Language`), FastAPI dependency `get_lang`, `hreflang` link builder.
- Hindi-aware search: a migration in `tsp.vector` adding a per-chunk `lang` column and generated `tsv` using `simple` for Hindi (Postgres has no Hindi stemmer) plus Devanagari normalisation (strip nukta variants, normalise ZWJ/ZWNJ) in a SQL function; English keeps `english`. Query side picks the config by detected language. Optional: transliteration of Latin-script Hindi queries ("hinglish") via `tsp.ai` before search.
- Multilingual embeddings: document that the embedding model must be multilingual (e.g. Cohere multilingual or Titan v2) and add a cross-language retrieval test (Hindi query finds the English chunk).
- K4 integration: `all_variants_ready` check uses configured languages; `tsp.ai.translate` (K3) fills a draft variant, marked `author_kind="ai"`.

## K8-B Distribution (`tsp.distribute`)
- Newsletter builder: templates (Jinja) rendering a digest of published items per language; segments (all, subscribers, free); send via `tsp.notify` email; unsubscribe and preference links; open/click tracking off by default (privacy).
- Share cards: `render_card(item, lang) -> PNG` (1200×630) using Pillow with bundled fonts that cover Devanagari (Noto Sans Devanagari) and Latin; cached in `tsp.storage`; `og:image` and `twitter:card` meta helper.
- SEO: `jsonld_article(item, version, lang)` (NewsArticle-compatible `Article` with `isAccessibleForFree` and `hasPart` from K5), sitemap and news-sitemap generators per language, `robots.txt` builder.
- AI images: `tsp.ai.images.generate(prompt, style, size)` with a provider adapter; every image stored with provenance (`model`, `prompt hash`, `created_at`), C2PA-style metadata where the provider supplies it, and a visible label block ("AI-generated illustration") enforced by a K4 publish check `ai_image_labelled`.
- Social posting stays manual (out of scope); provide copy-to-clipboard text per platform in the API.

## Tests
Language negotiation order; hreflang output; Hindi FTS finds a Devanagari word with nukta variants; cross-language retrieval; card renders Devanagari without tofu (pixel check on a known glyph area); JSON-LD validates against a schema fixture; unlabelled AI image blocks publish.

## Done when
A sample item renders in two languages with a share card, structured data and a newsletter; docs `docs/modules/i18n.md` and `docs/modules/distribute.md` written.

## Prompt
```
Repository: Teak-Rosewood/TheStartupProject. Branch: feat/k8-X (X = A then B).
Read docs/plans/K8-i18n-distribution.md section K8-X, docs/plans/README.md, tsp.vector,
tsp.cms, tsp.notify and tsp.ai. Implement the section with recipe examples in English and Hindi.
Run checks; open a PR.
```
