from tsp.ai import translate

from .stubs import StubProvider, stub_client


class Scripted(StubProvider):
    def __init__(self, *replies):
        super().__init__("script")
        self.replies, self.seen = list(replies), []

    async def chat_completion(self, api_key, messages, model_id, options=None):
        self.seen.append(messages)
        text = self.replies.pop(0)
        return {
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
            "usage": {},
        }


GLOSSARY = {"sourdough": "खट्टा आटा", "oven": "ओवन"}


async def test_no_retry_when_glossary_terms_are_used():
    p = Scripted("ओवन में खट्टा आटा रखें")
    t = await translate(
        stub_client(p), "Put the sourdough in the oven", "Hindi", GLOSSARY, model="stub-1"
    )
    assert t.text == "ओवन में खट्टा आटा रखें" and not t.flagged and not t.retried
    assert "खट्टा आटा" in p.seen[0][0]["content"] and "Hindi" in p.seen[0][0]["content"]


async def test_a_missing_term_triggers_one_retry_naming_the_term():
    p = Scripted("ओवन में डबल रोटी रखें", "ओवन में खट्टा आटा रखें")
    t = await translate(
        stub_client(p), "Put the sourdough in the oven", "Hindi", GLOSSARY, model="stub-1"
    )
    assert t.retried and not t.flagged and t.text.endswith("खट्टा आटा रखें")
    assert '"sourdough" as "खट्टा आटा"' in p.seen[1][-1]["content"]


async def test_still_missing_after_the_retry_is_flagged_not_hidden():
    p = Scripted("बेकिंग", "फिर बेकिंग")
    t = await translate(
        stub_client(p), "Put the sourdough in the oven", "Hindi", GLOSSARY, model="stub-1"
    )
    assert t.flagged and t.missing == ["sourdough", "oven"] and len(p.seen) == 2


async def test_terms_absent_from_the_source_are_not_required():
    p = Scripted("नमस्ते")
    t = await translate(stub_client(p), "Hello", "Hindi", GLOSSARY, model="stub-1")
    assert not t.flagged and len(p.seen) == 1
