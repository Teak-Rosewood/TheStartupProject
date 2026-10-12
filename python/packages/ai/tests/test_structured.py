import pytest
from pydantic import BaseModel
from tsp.ai.models import ChatCompletionResponse, Choice, ChoiceMessage
from tsp.ai.structured import StructuredOutputError, parse_json_object, structured


class Verdict(BaseModel):
    label: str
    score: float


def reply(text):
    return ChatCompletionResponse(model="m", choices=[Choice(message=ChoiceMessage(content=text))])


def scripted(*texts):
    seen = []

    async def call(messages):
        seen.append(messages)
        return reply(texts[len(seen) - 1])

    return call, seen


def test_parse_json_object_tolerates_fences_and_surrounding_prose():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! {"a": 1} Hope that helps.') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_object("no json here")


async def test_valid_output_needs_one_call_and_carries_the_schema():
    call, seen = scripted('{"label": "good", "score": 0.9}')
    got, responses = await structured(call, [{"role": "user", "content": "rate it"}], Verdict)
    assert got == Verdict(label="good", score=0.9) and len(responses) == 1
    assert "JSON Schema" in seen[0][-1]["content"] and "score" in seen[0][-1]["content"]


async def test_one_repair_round_with_the_error_shown_to_the_model():
    call, seen = scripted('{"label": "good"}', '{"label": "good", "score": 1}')
    got, responses = await structured(call, [{"role": "user", "content": "x"}], Verdict)
    assert got.score == 1.0 and len(responses) == 2
    assert seen[1][-2] == {"role": "assistant", "content": '{"label": "good"}'}
    assert "score" in seen[1][-1]["content"]


async def test_gives_up_after_the_repair_attempt():
    call, seen = scripted("nope", "still nope")
    with pytest.raises(StructuredOutputError, match="Verdict"):
        await structured(call, [{"role": "user", "content": "x"}], Verdict)
    assert len(seen) == 2
