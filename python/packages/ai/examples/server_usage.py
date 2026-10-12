"""Call the tsp-ai server with the official OpenAI SDK.

Start the server first:   tsp-ai serve --env-file .env
(set TSP_AI_SERVER_KEY to require a bearer token; pass the same value as api_key below)
Needs:                    pip install openai
"""

import os

from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key=os.environ.get("TSP_AI_SERVER_KEY", "unused"),
)

reply = client.chat.completions.create(
    model="auto", messages=[{"role": "user", "content": "Say hi in five words."}]
)
print(reply.choices[0].message.content)

for chunk in client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Count to 5."}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="", flush=True)
print()
