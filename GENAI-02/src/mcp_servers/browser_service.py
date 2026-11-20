"""Playwright browser fetcher with strict destination allowlisting."""
from urllib.parse import urlparse
from typing import Any

from src.config import Settings
from src.telemetry.otel import observe_tool, span


class BrowserPolicyError(ValueError):
    """Raised when a browser URL violates the destination allowlist."""


class BrowserService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise BrowserPolicyError("Browser only accepts absolute HTTPS URLs")
        if parsed.port not in (None, 443):
            raise BrowserPolicyError("Browser navigation is restricted to the standard HTTPS port")
        hostname = parsed.hostname.lower().rstrip(".")
        allowed = any(hostname == domain or hostname.endswith(f".{domain}") for domain in self.settings.browser_allowed_domains)
        if not allowed:
            raise BrowserPolicyError(f"Domain '{hostname}' is not allowlisted for browser automation")
        if parsed.username or parsed.password:
            raise BrowserPolicyError("URLs containing embedded credentials are not permitted")
        return url

    @observe_tool("mcp.playwright.browser_fetch")
    async def fetch_text(self, url: str) -> dict[str, Any]:
        safe_url = self.validate_url(url)
        with span("mcp.playwright.fetch", {"mcp.tool": "browser_fetch", "url.host": urlparse(safe_url).hostname or ""}):
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError("Playwright is not installed; install the browser extra to use browser tools") from exc
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(java_script_enabled=False)

                    async def route_allowlisted_request(route: Any) -> None:
                        request_url = route.request.url
                        if request_url.startswith(("data:", "blob:")):
                            await route.continue_()
                            return
                        try:
                            self.validate_url(request_url)
                        except BrowserPolicyError:
                            await route.abort()
                            return
                        await route.continue_()

                    await page.route("**/*", route_allowlisted_request)
                    response = await page.goto(
                        safe_url,
                        wait_until="domcontentloaded",
                        timeout=self.settings.browser_navigation_timeout_ms,
                    )
                    self.validate_url(page.url)
                    if response and response.status >= 400:
                        raise RuntimeError(f"Navigation returned HTTP {response.status}")
                    title = await page.title()
                    text = await page.locator("body").inner_text(timeout=5000)
                    return {"title": title[:300], "url": page.url, "status": response.status if response else None, "text": text[:12_000]}
                finally:
                    await browser.close()
