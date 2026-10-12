"""Distribution: newsletters, share cards, structured data, sitemaps, labelled AI illustrations."""

from .api import build_router
from .cards import card_key, get_or_render, render_card
from .images import (
    GeneratedImage,
    ImageGenerator,
    OpenAIImageGenerator,
    Provenance,
    ai_figure,
    ai_image_labelled,
    generate_image,
    unlabelled_ai_images,
)
from .items import Item, Variant
from .newsletter import Recipient, digest_context, in_segment, send_digest, templates_dir
from .seo import jsonld_article, news_sitemap, robots_txt, sitemap, sitemap_index
from .social import meta_html, social_copy, social_meta

__all__ = [
    "GeneratedImage",
    "ImageGenerator",
    "Item",
    "OpenAIImageGenerator",
    "Provenance",
    "Recipient",
    "Variant",
    "ai_figure",
    "ai_image_labelled",
    "build_router",
    "card_key",
    "digest_context",
    "generate_image",
    "get_or_render",
    "in_segment",
    "jsonld_article",
    "meta_html",
    "news_sitemap",
    "render_card",
    "robots_txt",
    "send_digest",
    "sitemap",
    "sitemap_index",
    "social_copy",
    "social_meta",
    "templates_dir",
    "unlabelled_ai_images",
]
