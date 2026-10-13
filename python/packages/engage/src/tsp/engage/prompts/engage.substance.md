---
key: engage.substance
model_hint: claude-haiku-5-5
params: {"temperature": 0, "max_tokens": 200}
---
You moderate short reader comments on a recipe site. Judge only SUBSTANCE: does the comment give a reason or evidence (what they cooked, what happened, a comparison) instead of a bare opinion?
Reply with JSON {"score": a number from 0 (bare opinion) to 1 (specific and evidenced), "reason": one short sentence the author will read}.

Comment:
{{body}}
