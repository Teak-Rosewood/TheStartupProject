# Sub-processors

> **Template, not legal advice.** This is a starting point written for a software startup based in India that also serves customers abroad. Replace every placeholder written in double curly braces (see `company.example.toml`), delete sections that do not apply, and have a qualified lawyer in your jurisdiction review it before you publish. Laws change; items marked **(verify)** are especially time-sensitive.

**Last updated:** {{effective_date}}. We tell customers by email before adding a sub-processor (see the [DPA](dpa.md)). Remove rows you do not use and add rows for anything else that sees personal data.

| Provider | Purpose | Data involved | Location |
|---|---|---|---|
| Amazon Web Services | hosting, database, storage, CDN, user sign-in (Cognito), email (SES), monitoring | all categories | India (ap-south-1) for the application; global edge network for the CDN; certificates and the website bucket in the US (us-east-1) |
| Razorpay (or your payment provider) | payment processing, subscriptions, refunds | name, email, contact number, payment method details, amount; card data is held by the provider | India |
| Google, Apple | optional social sign-in; they act as independent controllers of their own accounts | email, name, account identifier | global |
| Cloudflare Turnstile / hCaptcha (optional) | bot protection on sign-up and forms | IP address, browser signals | global |
| PostHog / Plausible (optional) | product analytics | pseudonymous event data | region you choose |
| FCM / APNs (optional) | push notifications | device token, message content | global |
| SMS provider (optional) | one-time codes and alerts | phone number, message content | India |
| Embedding providers (the same AI providers, when you index documents with `tsp.vector`) | turn document passages and search queries into vectors | the text of documents users add and their search queries | provider-dependent |
| **AI model providers** (OpenAI, Anthropic, Google, Groq, and any others you enable in `tsp.ai`) | generate AI outputs | prompts and content you send | provider-dependent, usually the US |

> **AI providers need care.** Many free tiers of AI APIs state that prompts may be retained or used to improve their models. Before sending customer content to any provider, read its current terms. Either use only providers whose terms exclude training on API data and offer zero retention, or restrict free-tier providers to non-customer data, and say here which providers see customer content.
