"""Basic usage example for TheStartupProject LLM Library."""

import asyncio

from tsp.ai import LLMClient
from tsp.ai.catalog import DEFAULT_CATALOG_PATH


async def main():
    # Keys come from <PLATFORM>_KEY vars (see .env.example). Run `tsp-ai verify` once first so
    # the router only picks models that actually answered.
    client = LLMClient.from_env(".env", catalog=DEFAULT_CATALOG_PATH)

    # List available models
    print("Available models:")
    for model in client.get_models():
        print(f"  - {model['model_id']} ({model['platform']})")

    # Simple chat completion
    print("\n--- Simple Chat ---")
    response = await client.chat.create(
        model="auto",  # Let the router pick the best model
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"},
        ],
    )
    print(f"Response: {response.choices[0].message.content}")
    print(f"Routed via: {response.routed_via}")

    # Pin a model id taken from client.get_models()
    print("\n--- Specific Model ---")
    response = await client.chat.create(
        model=client.get_models()[0]["model_id"],
        messages=[
            {"role": "user", "content": "Explain quantum computing in one sentence."},
        ],
        temperature=0.7,
        max_tokens=100,
    )
    print(f"Response: {response.choices[0].message.content}")

    # Streaming chat
    print("\n--- Streaming Chat ---")
    print("Response: ", end="", flush=True)
    async for chunk in client.chat.create(
        model="auto:fast",
        messages=[
            {"role": "user", "content": "Count from 1 to 5."},
        ],
        stream=True,
    ):
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)
    print()

    # Chat with tools (function calling)
    print("\n--- Tool Calling ---")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather in a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City name"},
                        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                    },
                    "required": ["location"],
                },
            },
        }
    ]

    response = await client.chat.create(
        model="auto",
        messages=[
            {"role": "user", "content": "What's the weather in Tokyo?"},
        ],
        tools=tools,
        tool_choice="auto",
    )
    print(f"Response: {response.choices[0].message}")

    # Cleanup
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
