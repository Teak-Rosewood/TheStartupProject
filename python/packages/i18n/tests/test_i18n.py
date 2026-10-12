import httpx
from fastapi import Depends, FastAPI
from tsp.i18n import Catalog, hreflang_links, make_get_lang, missing_languages, negotiate

SUP = ["en", "hi"]


def test_url_prefix_beats_cookie_beats_header_beats_default():
    kw = dict(cookie="hi", accept_language="en-US,en;q=0.9")
    assert negotiate("/en/recipes/x", SUP, "en", **kw).source == "url"
    assert (
        negotiate("/en/recipes/x", SUP, "en", **kw).lang == "en"
        and negotiate("/en/recipes/x", SUP, "en", **kw).path == "/recipes/x"
    )
    c = negotiate("/recipes/x", SUP, "en", **kw)
    assert (c.lang, c.source, c.path) == ("hi", "cookie", "/recipes/x")
    h = negotiate("/recipes/x", SUP, "en", cookie="fr", accept_language="fr,hi-IN;q=0.8,en;q=0.5")
    assert (h.lang, h.source) == ("hi", "header")
    d = negotiate("/", SUP, "en", accept_language="de,fr")
    assert (d.lang, d.source) == ("en", "default")


def test_accept_language_respects_q_values_and_ignores_garbage():
    assert negotiate("/", SUP, "en", accept_language="en;q=0.2, hi;q=0.9").lang == "hi"
    assert negotiate("/", SUP, "en", accept_language="hi;q=0, en;q=0.1").lang == "en"
    assert negotiate("/", SUP, "en", accept_language="hi;q=abc,*;q=1").lang == "en"
    assert negotiate("/hi", SUP, "en").path == "/"  # bare prefix
    assert negotiate("/hindi/x", SUP, "en").source == "default"  # a prefix must match exactly


def test_hreflang_links():
    links = hreflang_links("https://x.example/", "recipes/pancakes", SUP, "en")
    assert links == [
        {"hreflang": "en", "href": "https://x.example/en/recipes/pancakes"},
        {"hreflang": "hi", "href": "https://x.example/hi/recipes/pancakes"},
        {"hreflang": "x-default", "href": "https://x.example/en/recipes/pancakes"},
    ]
    only_en = hreflang_links("https://x.example", "/r", SUP, "en", available=["en"])
    assert [x["hreflang"] for x in only_en] == ["en", "x-default"]


def test_missing_languages():
    assert missing_languages(["en", "hi", "ta"], ["en"]) == ["hi", "ta"]
    assert missing_languages(["en"], ["en", "hi"]) == []


def test_catalog_translates_falls_back_and_never_raises(tmp_path, caplog):
    c = Catalog()
    assert c.t("newsletter.subject", "hi", count=3) == "इस सप्ताह: 3 नई रेसिपी"
    assert c.t("newsletter.subject", "hi-IN", count=3).startswith("इस सप्ताह")  # region tags work
    assert c.t("share.recipe_by", "en", author="Asha") == "A recipe by Asha"
    assert (
        c.t("newsletter.subject", "en") == "This week: {count} new recipes"
    )  # missing variable: left as is
    assert c.t("no.such.key", "hi") == "no.such.key"
    assert c.t("newsletter.intro", "de").startswith("Here is")  # unknown language: default
    d = tmp_path / "hi"
    d.mkdir()
    (d / "messages.po").write_text('msgid "newsletter.intro"\nmsgstr "ओवरराइड"\n', encoding="utf-8")
    assert Catalog(tmp_path).t("newsletter.intro", "hi") == "ओवरराइड"
    (tmp_path / "ta").mkdir()
    (tmp_path / "ta" / "messages.po").write_text(
        'msgid "newsletter.intro"\nmsgstr "x"\n', encoding="utf-8"
    )
    assert "newsletter.subject" in Catalog(tmp_path).missing("ta")


def test_the_bundled_catalogs_are_complete():
    c = Catalog()
    assert c.languages == ["en", "hi"] and c.missing("hi") == []


async def test_dependency_in_a_route():
    app = FastAPI()
    get_lang = make_get_lang(SUP, "en")

    @app.get("/{path:path}")
    async def page(lang: str = Depends(get_lang)):
        return {"lang": lang}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/hi/recipes")).json() == {"lang": "hi"}
        assert (await c.get("/recipes", headers={"accept-language": "hi"})).json() == {"lang": "hi"}
        assert (
            await c.get("/recipes", cookies={"lang": "hi"}, headers={"accept-language": "en"})
        ).json() == {"lang": "hi"}
        assert (await c.get("/recipes")).json() == {"lang": "en"}
