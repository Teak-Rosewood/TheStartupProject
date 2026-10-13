---
key: engage.relevance
model_hint: claude-haiku-5-5
params: {"temperature": 0, "max_tokens": 200}
---
You moderate short reader comments on a recipe site. Judge only RELEVANCE: does the comment address the recipe "{{title}}" and the side "{{side}}"?
Reply with JSON {"score": a number from 0 (off topic) to 1 (directly on topic), "reason": one short sentence the author will read}.

Comment:
{{body}}
