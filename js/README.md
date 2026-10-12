# JavaScript packages

npm workspace. Each package lives in `js/packages/<name>`.

| Package | What it is |
|---|---|
| [`landing`](packages/landing) | Astro marketing site: home, pricing, blog, legal pages, SEO, waitlist, consent banner, `security.txt`. See [docs/modules/growth.md](../docs/modules/growth.md). |
| [`web-app`](packages/web-app) | React + Vite single-page app under `/app`: PKCE sign-in, organizations, Razorpay billing and invoices, API keys, consents, data export and deletion. Sign-in settings are read from `/app/config.json` at run time. `npm run dev -w @tsp/web-app` proxies `/api` to localhost:8000. |
| [`mobile`](packages/mobile) | Expo starter (source only, not built here) |

```bash
cd js && npm ci
make -C .. landing        # from here: build + verify the landing site
```

`mobile` is deliberately not an npm workspace: it has its own dependencies and `npm ci` here should not pull in React Native. Run it with `cd packages/mobile && npm install`.
