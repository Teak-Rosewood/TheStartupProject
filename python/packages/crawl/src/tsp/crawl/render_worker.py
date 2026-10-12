"""The browser render worker: asks the API for a page, renders it, posts the text back.

It has no database access and no secrets beyond ``CRAWL_RENDER_TOKEN``. It exits after it has been
idle for a while (``CRAWL_IDLE_EXIT_MINUTES``), so a Fargate Spot task costs nothing between crawls.

    CRAWL_API_URL=https://api.example.com CRAWL_RENDER_TOKEN=... python -m tsp.crawl.render_worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

from .errors import CrawlError, FetchError, RenderUnavailable, UnsafeURL
from .fetch_browser import BrowserFetcher, Renderer
from .page import NotModified
from .safe_url import Resolver

log = logging.getLogger("tsp.crawl.render")
CLAIM, RESULT = "/internal/crawl/render/claim", "/internal/crawl/render/{job_id}/result"


async def render_one(browser: BrowserFetcher, url: str) -> dict[str, object]:
    """Render ``url`` and return the body for the result endpoint. Never raises for page problems."""
    try:
        page = await browser.fetch(url)
    except UnsafeURL as e:
        return {"error": f"blocked: {e}"}
    except RenderUnavailable as e:
        return {
            "error": str(e),
            "transient": True,
        }  # a misconfigured image; the job retries and then dies loudly
    except FetchError as e:
        s = e.status
        return {"error": str(e), "status": s, "transient": s is None or s == 429 or s >= 500}
    except CrawlError as e:
        return {"error": str(e)}
    except Exception as e:  # the browser crashed or timed out
        log.exception("render failed for %s", url)
        return {"error": f"{type(e).__name__}: {e}", "transient": True}
    assert not isinstance(page, NotModified)
    return {
        "final_url": page.final_url,
        "status": page.status,
        "title": page.title,
        "markdown": page.markdown,
        "links": page.links,
    }


async def run(
    api_url: str,
    token: str,
    *,
    renderer: Renderer | None = None,
    resolver: Resolver | None = None,
    client: httpx.AsyncClient | None = None,
    poll_seconds: float = 5.0,
    idle_exit_seconds: float = 600.0,
) -> int:
    """Work until the queue has been empty for ``idle_exit_seconds``. Returns the pages handled."""
    browser = BrowserFetcher(renderer, resolver=resolver)
    http = client or httpx.AsyncClient(
        base_url=api_url, headers={"Authorization": f"Bearer {token}"}, timeout=60
    )
    handled, idle_since = 0, time.monotonic()
    try:
        while time.monotonic() - idle_since < idle_exit_seconds:
            try:
                r = await http.post(CLAIM)
                r.raise_for_status()
            except httpx.HTTPError as e:
                log.warning("claim failed: %s", e)
                await asyncio.sleep(poll_seconds)
                continue
            if r.status_code == 204:
                await asyncio.sleep(poll_seconds)
                continue
            work = r.json()
            body = await render_one(browser, work["url"])
            try:
                (
                    await http.post(RESULT.format(job_id=work["job_id"]), json=body)
                ).raise_for_status()
            except httpx.HTTPError as e:
                log.warning(
                    "could not report job %s: %s", work["job_id"], e
                )  # the lease will expire
            handled += 1
            idle_since = time.monotonic()
    finally:
        if client is None:
            await http.aclose()
    return handled


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    api, token = os.environ.get("CRAWL_API_URL"), os.environ.get("CRAWL_RENDER_TOKEN")
    if not api or not token:
        print("CRAWL_API_URL and CRAWL_RENDER_TOKEN are required")
        return 2
    idle = float(os.environ.get("CRAWL_IDLE_EXIT_MINUTES", "10")) * 60
    log.info("render worker started; exits after %.0f idle seconds", idle)
    asyncio.run(run(api, token, idle_exit_seconds=idle))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
