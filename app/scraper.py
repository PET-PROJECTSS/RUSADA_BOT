import argparse
import asyncio
import logging

from playwright.async_api import async_playwright

from app.config import Settings
from app.db import Database
from app.logging_setup import setup_logger

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

            try:
                if not await self._login(page, email, password):
                    logger.error("Login failed")
                    return
                await self._scrape(page, max_iterations)
            finally:
                await browser.close()
                self.db.close()

    async def _login(self, page, email: str, password: str) -> bool:
        await page.goto(self.settings.rusada_url, wait_until="domcontentloaded")
        if await page.locator('a[href*="logout"]').count():
            return True
        try:
            await page.locator('text=/Я согласен|Принять/i').click(timeout=2000)
        except Exception:
            pass
        if not await page.locator("#loginform-login").is_visible():
            if await page.locator("text=Вход").first.is_visible():
                await page.locator("text=Вход").first.click()
        await page.locator("#loginform-login").fill(email)
        await page.locator("#loginform-password").fill(password)
        await page.keyboard.press("Enter")
        try:
            await page.wait_for_selector('a[href*="logout"]', state="attached", timeout=10000)
            return True
        except Exception:
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
