import asyncio
import logging
import re
from datetime import datetime

from playwright.async_api import async_playwright

from app.config import Settings
from app.db import Database

logger = logging.getLogger("RUSADA.rusada")

_START_BUTTONS = [
    "ПРОЙТИ КУРС",
    "ПРОДОЛЖИТЬ",
    "ПРОЙТИ ТЕСТ",
    "НАЧАТЬ ТЕСТ",
    "Пройти еще раз",
    "НАЧАТЬ",
    "ВПЕРЕД",
]


class RusadaBot:
    def __init__(self, settings: Settings, headless: bool | None = None):
        self.settings = settings
        self.headless = settings.headless if headless is None else headless
        self.db = Database(settings)
        self._browser = None
        self._context = None
        self._page = None

    @property
    def log(self) -> logging.Logger:
        return logger

    async def _launch(self, playwright) -> None:
        self._browser = await playwright.chromium.launch(
            headless=self.headless, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        self._context = await self._browser.new_context(
            viewport={"width": self.settings.viewport_width, "height": self.settings.viewport_height},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.settings.default_timeout_ms)

    async def run(self, email: str, password: str, select_account_cb=None) -> str | None:
        result = None
        async with async_playwright() as playwright:
            await self._launch(playwright)
            page = self._page
            try:
                if await self.login(email, password):
                    await self._check_and_switch_account(page, select_account_cb)
                    result = await self._process_course(page)
                    if result:
                        logger.info("Certificate URL: %s", result)
            except Exception as exc:
                logger.error("Critical error: %s", exc)
                await self._screenshot("error")
            finally:
                await self._close_browser()
        return result

    async def login(self, email: str, password: str) -> bool:
        page = self._page
        logger.info("Login: %s", email)
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
            logger.info("Login successful")
            return True
        except Exception:
            return "login" not in page.url

    async def _check_and_switch_account(self, page, select_account_cb) -> None:
        logger.info("Checking linked accounts")
        if page.url.rstrip("/") != self.settings.rusada_url.rstrip("/"):
            await page.goto(self.settings.rusada_url, wait_until="domcontentloaded")

        try:
            await page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

        await asyncio.sleep(3)

        child_links = page.locator('a[href*="/user/login/child/"]')
        count = await child_links.count()

        if count == 0:
            logger.info("No children found, using current user")
            return

        if select_account_cb is None:
            logger.info("No account callback, staying on parent")
            return

        children = []
        for i in range(count):
            link = child_links.nth(i)
            href = await link.get_attribute("href")
            card = link.locator("xpath=./..")
            name_el = card.locator(".accounts__name, .account__name, h3").first
            name = (await name_el.text_content()).strip() if await name_el.count() else f"Ребенок #{i + 1}"
            children.append({"name": name, "href": href, "type": "child"})

        parent_name = "Родитель"
        try:
            parent_el = page.locator("p.accounts__name--big").first
            if await parent_el.count():
                parent_name = (await parent_el.text_content()).strip()
        except Exception:
            pass

        try:
            choice = await select_account_cb(parent_name, children)
        except Exception as exc:
            logger.error("Account callback error: %s", exc)
            return

        if choice and choice.get("type") == "child":
            target_href = choice.get("href") or ""
            full_url = target_href if target_href.startswith("http") else f"{self.settings.rusada_url}{target_href}"
            logger.info("Switching to: %s", choice.get("name"))
            await page.goto(full_url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass
            await asyncio.sleep(2)
        else:
            logger.info("Selected current/parent account")

    async def _process_course(self, page) -> str | None:
        logger.info("Navigating to course")
        await page.goto(f"{self.settings.rusada_url}/course/{self.settings.default_course_id}", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        if await page.locator('a[href*="certificate"], a[href*=".pdf"]').filter(has_text="Скачать").count():
            logger.info("Course already completed")
            return await self._get_cert(page)

        for stage in range(1, 200):
            logger.info("Stage %s", stage)
            await self._click_start_button(page)

            await self._run_stage(page)

            if await page.locator("text=ЗАВЕРШИТЬ И СКАЧАТЬ").count():
                return await self._get_cert(page)

            if await page.locator('a[href*="certificate"]').count():
                return await self._get_cert(page)

            if not await self._start_button(page).count():
                logger.warning("No button for next stage, stopping")
                break

        return None

    def _start_button(self, page):
        return page.locator("button, a.button").filter(
            has_text=re.compile("|".join(_START_BUTTONS), re.I)
        ).first

    async def _click_start_button(self, page) -> None:
        btn = self._start_button(page)
        if await btn.is_visible():
            text = await btn.text_content()
            logger.info("Clicking: %s", text.strip() if text else "")
            await btn.click(force=True)
            await asyncio.sleep(2)

    async def _run_stage(self, page) -> None:
        question_number = 0
        stale_count = 0

        while True:
            await asyncio.sleep(0.8)

            if await page.locator("text=ЗАВЕРШИТЬ И СКАЧАТЬ").count():
                logger.info("Stage finished")
                return

            inputs = page.locator('.material input[type="radio"], .material input[type="checkbox"]')
            inputs_count = await inputs.count()

            if inputs_count == 0:
                await self._skip_theory(page)
                stale_count += 1
                if stale_count >= 5:
                    logger.info("Theory finished")
                    return
                continue

            stale_count = 0
            question_number += 1

            question_text = await self._get_question_text(page)
            logger.info("Q[%s]: %s", question_number, question_text.strip()[:70])

            found_indices = await self._resolve_answers(page, inputs, inputs_count, question_text)

            await self._select_answers(page, inputs, inputs_count, found_indices)
            await self._submit_answer(page)
            await self._advance(page)

    async def _skip_theory(self, page) -> None:
        next_btn = page.locator(
            'button.js-course-run-submit-button, '
            'button:has-text("Далее"), '
            'button:has-text("Продолжить"), '
            'button:has-text("ВПЕРЕД")'
        ).first

        if not await next_btn.is_visible():
            return

        logger.info("Skipping theory")
        try:
            await next_btn.click(force=True)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            await asyncio.sleep(3)
        except Exception:
            await next_btn.evaluate("e => e.click()")
            await asyncio.sleep(3)

    async def _get_question_text(self, page) -> str:
        for selector in [".material__texts .title", ".material > h1.title", ".material h1"]:
            el = page.locator(selector).first
            if await el.count():
                return (await el.text_content()) or "Вопрос не найден"
        return "Вопрос не найден"

    async def _resolve_answers(self, page, inputs, inputs_count, question_text) -> list[int]:
        found = []

        correct_inputs = page.locator('.material input[data-is-correct="1"]')
        if await correct_inputs.count():
            correct_values = []
            for i in range(await correct_inputs.count()):
                correct_values.append(await correct_inputs.nth(i).get_attribute("value"))

            for i in range(inputs_count):
                value = await inputs.nth(i).get_attribute("value")
                if value in correct_values:
                    found.append(i)
            logger.info("Cheat answers: %s", len(found))
            return found

        labels = page.locator(".radio__label, .checkbox__label")
        if await labels.count():
            texts = [await labels.nth(i).text_content() for i in range(await labels.count())]
            found = self.db.find_answer_indices(question_text.strip(), texts)
            if found:
                logger.info("DB answers: %s", len(found))
            else:
                found = [0]
                logger.info("DB miss, picking first answer")

        return found

    async def _select_answers(self, page, inputs, inputs_count, indices) -> None:
        items = page.locator(".radio__item, .checkbox-item")
        for i in indices:
            if i >= inputs_count:
                continue
            try:
                if not await inputs.nth(i).is_checked():
                    await items.nth(i).scroll_into_view_if_needed()
                    await items.nth(i).click(force=True)
                    await asyncio.sleep(0.3)
            except Exception:
                pass
        await asyncio.sleep(0.5)

    async def _submit_answer(self, page) -> None:
        answer_btn = page.locator("button.js-course-run-answer-button, button.js-popup-button").first
        if await answer_btn.is_visible():
            try:
                await answer_btn.click(force=True)
                try:
                    await page.wait_for_selector(
                        ".js-run-info-correct, .js-run-info-invalid", state="visible", timeout=4000
                    )
                except Exception:
                    pass
            except Exception:
                pass

    async def _advance(self, page) -> None:
        next_btn = page.locator("button.js-course-run-submit-button").first
        if await next_btn.is_visible():
            try:
                await next_btn.click(force=True)
            except Exception:
                pass
        try:
            await page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

    async def _get_cert(self, page) -> str | None:
        logger.info("Fetching certificate")
        if await page.locator("text=ЗАВЕРШИТЬ И СКАЧАТЬ").is_visible():
            await page.locator("text=ЗАВЕРШИТЬ И СКАЧАТЬ").click()
            await asyncio.sleep(1)

        download_link = page.locator('a[href*="certificate"], a[href*=".pdf"]').filter(
            has_text="Скачать"
        ).first
        try:
            await download_link.wait_for(state="visible", timeout=15000)
            url = await download_link.get_attribute("href")
            logger.info("Certificate URL: %s", url)
            return url
        except Exception:
            logger.warning("Certificate not found")
            return None

    async def _screenshot(self, prefix: str) -> None:
        path = self.settings.screenshots_dir / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.png"
        self.settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
        try:
            await self._page.screenshot(path=str(path))
        except Exception:
            pass

    async def _close_browser(self) -> None:
        self.db.close()
        if self._browser is not None:
            await self._browser.close()
        self._browser = None
        self._context = None
        self._page = None
