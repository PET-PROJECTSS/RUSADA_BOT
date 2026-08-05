import argparse
import asyncio
import logging
import time

from playwright.async_api import async_playwright

from app.config import Settings
from app.db import Database
from app.logging_setup import setup_logger
from app.rusada import block_route, is_logged_in, wait_ready

logger = logging.getLogger("RUSADA.scraper")


class ScraperBot:
    def __init__(self, settings: Settings, headless: bool):
        self.settings = settings
        self.headless = headless
        self.db = Database(settings)

    async def run(self, email: str, password: str, max_iterations: int) -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                viewport={
                    "width": self.settings.viewport_width,
                    "height": self.settings.viewport_height,
                }
            )
            page = await context.new_page()
            page.set_default_timeout(self.settings.default_timeout_ms)
            await page.route("**/*", block_route)

            try:
                if not await self._login(page, email, password):
                    logger.error("Login failed")
                    return
                await self._scrape(page, max_iterations)
            finally:
                await browser.close()
                self.db.close()

    async def _login(self, page, email: str, password: str) -> bool:
        await page.goto(self.settings.rusada_url, wait_until="domcontentloaded", timeout=60000)
        await wait_ready(page)
        if await is_logged_in(page):
            return True
        try:
            await page.locator('text=/Я согласен|Принять/i').click(timeout=2000)
        except Exception:
            pass
        if not await page.locator("#loginform-login").is_visible():
            try:
                await page.locator('a[href="#login-popup"]').first.click(timeout=10000)
            except Exception:
                pass
        await page.locator("#loginform-login").fill(email)
        await page.locator("#loginform-password").fill(password)
        submit = page.locator("#login-form-modal button[type=submit], #login-form-modal input[type=submit]").first
        try:
            async with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/user/login") and r.status == 302,
                timeout=25000,
            ):
                try:
                    await submit.click(timeout=5000)
                except Exception:
                    await page.keyboard.press("Enter")
        except Exception:
            pass
        await asyncio.sleep(2)
        deadline = time.monotonic() + 15000
        while time.monotonic() < deadline:
            if await is_logged_in(page):
                return True
            await asyncio.sleep(2)
        for _ in range(4):
            try:
                await page.goto(self.settings.rusada_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                await asyncio.sleep(3)
                continue
            await wait_ready(page)
            if await is_logged_in(page):
                return True
            await asyncio.sleep(3)
        logger.warning("Login result not detected (url=%s)", page.url)
        return False

    async def _scrape(self, page, max_iterations: int) -> None:
        await page.goto(f"{self.settings.rusada_url}/course/{self.settings.default_course_id}", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        for i in range(1, max_iterations + 1):
            logger.info("Iteration %s/%s", i, max_iterations)
            try:
                question_el = page.locator(".title.title--center, h1.title").first
                await question_el.wait_for(state="visible", timeout=10000)
                question_text = (await question_el.text_content()).strip()

                answers_locator = page.locator(".radio__label, .checkbox__label")
                await answers_locator.first.wait_for(state="visible", timeout=5000)
                answers = [a.strip() for a in await answers_locator.all_text_contents() if a.strip()]

                logger.info("Question: %s", question_text[:50])
                self.db.save_question(question_text, answers)

                await page.reload()
                await asyncio.sleep(0.5)
            except Exception as exc:
                logger.error("Scrape error: %s", exc)
                await page.reload()
                await asyncio.sleep(2)


def parse_args():
    parser = argparse.ArgumentParser(description="RUSADA question scraper")
    parser.add_argument("--headless", action="store_true", help="run browser headless")
    parser.add_argument("--visible", action="store_true", help="run browser with UI")
    parser.add_argument("--iterations", type=int, default=500, help="max scrape iterations")
    return parser.parse_args()


async def main():
    args = parse_args()
    settings = Settings()
    settings.validate()

    scraper = ScraperBot(settings, headless=args.headless and not args.visible)
    await scraper.run(settings.rusada_email, settings.rusada_password, args.iterations)


if __name__ == "__main__":
    asyncio.run(main())
