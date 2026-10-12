import hashlib
import io
import json
import xml.etree.ElementTree as ET

import jsonschema
import pytest
from distribute_support import NOW, PANCAKES, PANEER, MemStore
from PIL import Image, ImageDraw, features
from tsp.distribute import (
    GeneratedImage,
    Item,
    OpenAIImageGenerator,
    Variant,
    ai_figure,
    ai_image_labelled,
    card_key,
    generate_image,
    get_or_render,
    jsonld_article,
    meta_html,
    news_sitemap,
    render_card,
    robots_txt,
    sitemap,
    sitemap_index,
    social_copy,
    social_meta,
    unlabelled_ai_images,
)
from tsp.distribute.cards import FONTS, _font
from tsp.i18n import Catalog

BASE = "https://food.example.com"
CAT = Catalog()
SCHEMA = json.loads(
    (__import__("pathlib").Path(__file__).parent / "fixtures/article.schema.json").read_text()
)


# -- structured data ----------------------------------------------------------------------------


def test_jsonld_matches_the_schema_in_both_languages():
    for lang in ("en", "hi"):
        data = jsonld_article(
            PANCAKES, lang, BASE, publisher="Food Co", publisher_logo="https://x/logo.png"
        )
        jsonschema.validate(data, SCHEMA)
        assert (
            data["inLanguage"] == lang
            and data["isAccessibleForFree"] is True
            and "hasPart" not in data
        )
    hi = jsonld_article(PANCAKES, "hi", BASE, publisher="Food Co")
    assert hi["mainEntityOfPage"]["@id"] == f"{BASE}/hi/pancakes" and hi["headline"].startswith(
        "दस मिनट"
    )


def test_gated_articles_carry_the_paywall_markup_and_the_schema_insists_on_it():
    data = jsonld_article(PANEER, "en", BASE, publisher="Food Co")
    jsonschema.validate(data, SCHEMA)
    assert data["isAccessibleForFree"] is False and data["hasPart"]["cssSelector"] == ".paywalled"
    broken = {k: v for k, v in data.items() if k != "hasPart"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, SCHEMA)
    assert jsonld_article(PANEER, "en", BASE, publisher="P")["author"] == [
        {"@type": "Organization", "name": "P"}
    ]


def test_a_missing_language_falls_back_to_the_default():
    data = jsonld_article(PANEER, "hi", BASE, publisher="P")
    assert data["inLanguage"] == "en"


# -- sitemaps and robots ----------------------------------------------------------------------


NS = {
    "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "x": "http://www.w3.org/1999/xhtml",
    "n": "http://www.google.com/schemas/sitemap-news/0.9",
}


def test_sitemaps_list_languages_with_alternates_and_escape_text():
    root = ET.fromstring(sitemap([PANCAKES, PANEER], BASE, ["en", "hi"], "hi"))
    locs = [u.find("s:loc", NS).text for u in root.findall("s:url", NS)]
    assert locs == [f"{BASE}/hi/pancakes"]
    alts = {a.get("hreflang"): a.get("href") for a in root.find("s:url", NS).findall("x:link", NS)}
    assert alts == {"en": f"{BASE}/en/pancakes", "hi": f"{BASE}/hi/pancakes"}
    full = ET.fromstring(sitemap([PANCAKES, PANEER], BASE, ["en", "hi"]))
    assert len(full.findall("s:url", NS)) == 3  # 2 pancakes + 1 paneer
    idx = ET.fromstring(sitemap_index(BASE, ["en", "hi"]))
    assert [e.find("s:loc", NS).text for e in idx.findall("s:sitemap", NS)] == [
        f"{BASE}/sitemap-en.xml",
        f"{BASE}/sitemap-hi.xml",
        f"{BASE}/sitemap-news.xml",
    ]


def test_news_sitemap_has_only_the_last_two_days():
    root = ET.fromstring(news_sitemap([PANCAKES, PANEER], BASE, ["en", "hi"], "Food & Co", now=NOW))
    urls = root.findall("s:url", NS)
    assert len(urls) == 2 and all("pancakes" in u.find("s:loc", NS).text for u in urls)
    langs = {u.find("n:news/n:publication/n:language", NS).text for u in urls}
    assert langs == {"en", "hi"}
    assert urls[0].find("n:news/n:publication/n:name", NS).text == "Food & Co"


def test_robots():
    r = robots_txt(BASE)
    assert "Disallow: /api/" in r and f"Sitemap: {BASE}/sitemap.xml" in r and "GPTBot" not in r
    blocked = robots_txt(BASE, allow_ai_crawlers=False)
    assert "User-agent: GPTBot\nDisallow: /" in blocked


# -- social ----------------------------------------------------------------------------------------


def test_social_copy_respects_the_x_limit_and_the_meta_tags_are_escaped():
    long = Item("r9", "x", NOW, {"en": Variant("A" * 400)}, keywords=("one pot",))
    c = social_copy(long, "en", f"{BASE}/en/x", CAT)
    assert (
        len(c["x"].replace(f"{BASE}/en/x", "u" * 23)) <= 280
        and c["x"].endswith(f"{BASE}/en/x")
        and "#onepot" in c["x"]
    )
    hi = social_copy(PANCAKES, "hi", f"{BASE}/hi/pancakes", CAT)
    assert "Asha Rao की रेसिपी" in hi["linkedin"] and hi["whatsapp"].startswith("दस मिनट")
    tags = social_meta(
        PANEER, "en", BASE, image_url=f"{BASE}/c.png", site_name="Food Co", twitter_handle="@food"
    )
    assert {"name": "twitter:card", "content": "summary_large_image"} in tags
    html = meta_html(tags)
    assert "&amp; fast" in html and "&lt;b&gt;grilled&lt;/b&gt;" in html and "<b>" not in html
    assert 'content="1200"' in html


# -- share cards ---------------------------------------------------------------------------------


def ink(img, box=None):
    g = img.convert("L").crop(box) if box else img.convert("L")
    return sum(1 for p in g.getdata() if p < 128)


def test_card_is_a_1200_by_630_png_in_both_scripts():
    for lang in ("en", "hi"):
        im = Image.open(io.BytesIO(render_card(PANCAKES, lang, site_name="Food Co")))
        assert im.size == (1200, 630) and im.format == "PNG"
        assert ink(im, (80, 100, 1120, 400)) > 3000  # the title is on the card


@pytest.mark.skipif(not features.check("raqm"), reason="Devanagari shaping needs libraqm")
def test_devanagari_is_drawn_with_real_glyphs_not_tofu_boxes():
    word = "हिन्दी रेसिपी"
    deva = _font(word, 72, True)
    tofu = _font(
        "Pancakes", 72, True
    )  # the Latin font has no Devanagari glyphs: this is what tofu looks like

    def draw(font, text):
        im = Image.new("L", (900, 140), 255)
        ImageDraw.Draw(im).text((10, 10), text, font=font, fill=0)
        return im

    good, bad = draw(deva, word), draw(tofu, word)
    assert good.tobytes() != bad.tobytes()
    assert (
        abs(deva.getlength(word) - tofu.getlength(word)) > 20
    )  # different glyphs, different widths
    # shaping happened: with the plain layout engine the vowel sign "ि" lands after the consonant;
    # with libraqm it is moved before it, so the two drawings differ
    from PIL import ImageFont

    plain = ImageFont.truetype(
        str(FONTS / "NotoSansDevanagari-Bold.ttf"), 72, layout_engine=ImageFont.Layout.BASIC
    )
    assert draw(deva, "कि").tobytes() != draw(plain, "कि").tobytes()
    # and the card's Devanagari title has no boxes: no glyph in it is the font's missing-glyph shape
    notdef = draw(deva, "")
    assert good.tobytes() != notdef.tobytes()


def test_the_fonts_and_their_license_ship_with_the_package():
    names = {p.name for p in FONTS.iterdir()}
    assert {"NotoSans-Regular.ttf", "NotoSansDevanagari-Regular.ttf", "OFL.txt"} <= names
    assert "SIL Open Font License" in (FONTS / "OFL.txt").read_text()


async def test_cards_are_cached_by_content():
    store = MemStore()
    k1 = await get_or_render(store, PANCAKES, "hi", site_name="Food Co")
    k2 = await get_or_render(store, PANCAKES, "hi", site_name="Food Co")
    assert k1 == k2 == card_key(PANCAKES, "hi", "Food Co") and store.writes == 1
    edited = Item(
        **{
            **PANCAKES.__dict__,
            "variants": {"hi": Variant("नया शीर्षक"), "en": PANCAKES.variants["en"]},
        }
    )
    assert card_key(edited, "hi", "Food Co") != k1
    assert store.objs[k1][1] == "image/png"


# -- AI images ----------------------------------------------------------------------------------


class FakeGen:
    async def generate(self, prompt, style, size):
        return GeneratedImage(
            b"\x89PNG-fake", "image/png", "img-model-1", "fakeai", {"claim": "ai"}
        )


async def test_images_are_stored_with_provenance_and_never_the_prompt_itself():
    store = MemStore()
    prov = await generate_image(
        FakeGen(), store, "a steaming bowl of dal", style="watercolour", size="1024x1024"
    )
    assert prov.prompt_sha256 == hashlib.sha256(b"a steaming bowl of dal").hexdigest()
    assert store.objs[prov.key][0] == b"\x89PNG-fake"
    record = json.loads(store.objs[f"images/{prov.id}.json"][0])
    assert (
        record["model"] == "img-model-1"
        and record["ai_generated"] is True
        and record["c2pa"] == {"claim": "ai"}
    )
    assert "dal" not in json.dumps(record)


def test_unlabelled_ai_images_block_publishing():
    good = ai_figure("https://cdn/x.png", "dal", "img1", "hi", CAT)
    assert "एआई द्वारा बनाया गया चित्र" in good and ai_image_labelled(good, ["img1"])
    bare = '<p>text</p><img src="x" data-ai-image="img2">'
    assert unlabelled_ai_images(bare) == ["img2"] and not ai_image_labelled(bare)
    no_caption = '<figure><img src="x" data-ai-image="img3"></figure>'
    assert unlabelled_ai_images(no_caption) == ["img3"]
    empty = '<figure><img data-ai-image="img4"><figcaption class="ai-label"> </figcaption></figure>'
    assert unlabelled_ai_images(empty) == ["img4"]
    other_caption = '<figure><img data-ai-image="img5"><figcaption>Photo: me</figcaption></figure>'
    assert unlabelled_ai_images(other_caption) == ["img5"]
    mixed = good + bare
    assert unlabelled_ai_images(mixed) == ["img2"]
    assert not ai_image_labelled(good, ["img1", "imgX"])  # a required image missing from the markup
    assert ai_image_labelled("<p>no images at all</p>")


async def test_openai_style_adapter(monkeypatch):
    import base64

    import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(b"PNGDATA").decode()}]}
        )

    g = OpenAIImageGenerator("https://img.example.com/v1", "k", model="m")
    g._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await g.generate("a dal", "flat", "512x512")
    assert out.data == b"PNGDATA" and seen["auth"] == "Bearer k"
    assert seen["body"]["prompt"].endswith("Style: flat") and seen["body"]["size"] == "512x512"


async def test_public_routes():
    import httpx
    from fastapi import FastAPI
    from tsp.distribute import build_router

    async def items():
        return [PANCAKES, PANEER]

    store = MemStore()
    app = FastAPI()
    app.include_router(
        build_router(
            items,
            base_url=BASE,
            languages=["en", "hi"],
            publication="Food Co",
            catalog=CAT,
            store=store,
        )
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert "Sitemap:" in (await c.get("/robots.txt")).text
        assert (await c.get("/sitemap.xml")).headers["content-type"].startswith("application/xml")
        assert f"{BASE}/hi/pancakes" in (await c.get("/sitemap-hi.xml")).text
        assert (await c.get("/sitemap-fr.xml")).status_code == 404
        assert "x" in (await c.get("/distribute/social-copy/r1/hi")).json()
        assert (await c.get("/distribute/social-copy/nope/hi")).status_code == 404
        key = (await c.get("/distribute/card/r1/hi")).json()["key"]
        assert key in store.objs
