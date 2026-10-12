# JavaScript packages

npm workspace. Each package lives in `js/packages/<name>`.

| Package | What it is |
|---|---|
| [`landing`](packages/landing) | Astro marketing site: home, pricing, blog, legal pages, SEO, waitlist, consent banner, `security.txt`. See [docs/modules/growth.md](../docs/modules/growth.md). |
| `web-app` | planned (phase 7): React single-page app under `/app` |
| `mobile` | planned (phase 7): Expo starter |

```bash
cd js && npm ci
make -C .. landing        # from here: build + verify the landing site
```
