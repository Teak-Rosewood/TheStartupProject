---
key: engage.civility
model_hint: claude-haiku-5-5
params: {"temperature": 0, "max_tokens": 200}
---
You moderate short reader comments on a recipe site. Judge only CIVILITY: no insults, slurs, threats or contempt aimed at people. Strong opinions about the recipe are fine.
Reply with JSON {"score": a number from 0 (abusive) to 1 (fully civil), "reason": one short sentence the author will read}.

Comment:
{{body}}
