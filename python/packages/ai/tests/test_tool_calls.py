from tsp.ai import LLMClient, ModelCandidate

from .stubs import StubProvider


class Tooling(StubProvider):
    async def chat_completion(self, api_key, messages, model_id, options=None):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "fetch_url", "arguments": '{"url": "https://example.com"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }


async def test_tool_calls_and_finish_reason_survive_the_client():
    client = LLMClient()
    client._router.add_provider("t", Tooling("t"), [ModelCandidate("t", "m", "m")])
    client._router.add_key("t", "k")
    resp = await client.chat.create(
        model="m", messages=[{"role": "user", "content": "go"}], tools=[{"type": "function"}]
    )
    choice = resp.choices[0]
    assert choice.finish_reason == "tool_calls" and choice.message.content is None
    assert choice.message.tool_calls[0].function.name == "fetch_url"
    assert choice.message.tool_calls[0].id == "call_1"
