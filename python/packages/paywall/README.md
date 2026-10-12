# tsp-paywall

Metered ("3 free articles a week") and hard paywalls that hold without walling many people behind one IP. Guide: [docs/modules/paywall.md](../../docs/modules/paywall.md).

```python
pw = Paywall(secret=settings.paywall_secret, cache=cache, entitled=is_subscriber)
decision = await pw.check(
    session, visitor_from_request(request, user_id), Item("pancakes", org_id, "recipe")
)
await pw.record_read(session, visitor, item, decision)
```
