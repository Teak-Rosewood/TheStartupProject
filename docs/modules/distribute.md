# Distribution (`tsp.distribute`)

Newsletters, share cards, structured data, sitemaps and labelled AI illustrations for published content. Package: `python/packages/distribute`. Languages come from [`tsp.i18n`](i18n.md).

Your CMS adapts its rows to `Item(id, slug, published_at, variants={"en": Variant(title, summary), "hi": ...}, authors, free, ...)`. Nothing here depends on a particular CMS.

## Newsletter
`send_digest(session, notifier, catalog, items, recipients, segment=, languages=)` queues one marketing email per reader, in the reader's language (a language you do not publish in gets the default). Segments: `all`, `subscribers`, `free`. Build the notifier with `Templates(templates_dir())` so it finds the `digest` template.

Consent is not decided here: the notifier sends marketing mail only to people who opted in and are not suppressed, adds the unsubscribe link and the one-click header, and records `suppressed` for the rest. **Open and click tracking are not built**: there is no tracking pixel, and links go straight to your site.

## Share cards
`render_card(item, lang, site_name=)` returns a 1200×630 PNG. Fonts are bundled (Noto Sans and Noto Sans Devanagari, SIL Open Font License, text in `fonts/OFL.txt`). Devanagari needs text shaping from libraqm, which Pillow wheels include; without it the renderer refuses rather than draw broken letters. `get_or_render(store, item, lang)` caches the PNG in object storage under a key that changes when the title, summary or authors change. `social_meta` returns the `og:*` and `twitter:*` tags (`meta_html` escapes them for the page head).

## Search engines
- `jsonld_article(item, lang, base_url, publisher=)`: `NewsArticle` with language, dates, authors and, for gated items, `isAccessibleForFree: false` and `hasPart.cssSelector` (the same markup `tsp.paywall` describes).
- `sitemap(items, base_url, languages, lang=)`: one sitemap per language, every entry pointing at its translations with `xhtml:link`. `news_sitemap` lists items from the last two days. `sitemap_index` and `robots_txt` (with an option to ask AI training crawlers to stay out; robots.txt is a request, not enforcement).
- Routes: `build_router(items, ...)` serves `/robots.txt`, `/sitemap.xml`, `/sitemap-<lang>.xml`, `/sitemap-news.xml`, plus social copy and card keys.

## Social posting
Posting stays manual. `social_copy(item, lang, url, catalog)` returns ready-to-paste text for X (within 280 characters counting the link as 23), LinkedIn, Facebook and WhatsApp.

## AI illustrations
`generate_image(generator, store, prompt, style=, size=)` stores the image and a provenance record `images/<id>.json`: model, provider, a hash of the prompt (the prompt itself is not kept), style, size, time, and the provider's content credentials (C2PA) when it sends them. `OpenAIImageGenerator` speaks the common `/images/generations` shape; any object with `async generate(prompt, style, size)` works.

**Disclosure is enforced:** `ai_figure(...)` is the markup that passes (image inside a `<figure>` with a translated "AI-generated illustration" caption), and `ai_image_labelled(html, required_ids)` is the publish check the CMS should run; images without the label block publishing. The image generator lives here, not in `tsp.ai`, to keep that package unchanged; move it there if you prefer.

## Not built
Open and click tracking, editing of generated images, image generation providers beyond the OpenAI shape, posting to social networks, and the CMS publish step that calls `ai_image_labelled` (K4).
