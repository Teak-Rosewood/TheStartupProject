"""Synchronous usage example for tsp.ai."""

from tsp.ai import SyncLLMClient
from tsp.ai.catalog import DEFAULT_CATALOG_PATH


def main():
    # `with` closes provider connections and the background loop on exit.
    with SyncLLMClient.from_env(".env", catalog=DEFAULT_CATALOG_PATH) as client:
        response = client.chat.create(
            model="auto",
            messages=[{"role": "user", "content": "Hello! How are you?"}],
        )
        print(f"Response: {response.choices[0].message.content}")

        # stream=True returns a plain iterator
        for chunk in client.chat.create(
            model="auto:fast",
            messages=[{"role": "user", "content": "Count from 1 to 5."}],
            stream=True,
        ):
            print(chunk.choices[0].delta.content or "", end="", flush=True)
        print()

        print("\nAvailable models:")
        for model in client.get_models():
            print(f"  - {model['model_id']} ({model['platform']})")


if __name__ == "__main__":
    main()
