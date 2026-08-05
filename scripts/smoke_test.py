"""Smoke test for the RUSADA bot: launches headless Chromium, logs in and
opens the course page. Requires real credentials.

Credentials priority:
  1. --email/--password args
  2. RUSADA_EMAIL/RUSADA_PASSWORD from .env (Settings)

On failure it dumps screenshots into /tmp (mounted inside the container)
and exits with a non-zero code, so it can be used in CI / manual checks:

    docker compose exec -T app python scripts/smoke_test.py \
        --email '...' --password '...'
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from app.config import Settings
from app.rusada import RusadaBot, wait_ready

_SHOT_DIR = "/tmp"


async def shot(page, name: str) -> None:
    path = f"{_SHOT_DIR}/smoke_{name}_{datetime.now():%H%M%S}.png"
    try:
        await page.screenshot(path=path, full_page=False)
        print(f"[shot] {path}")
    except Exception as exc:
        print(f"[shot] failed {name}: {exc}")


async def main(email: str, password: str, headless: bool) -> int:
    settings = Settings()
    rusada = RusadaBot(settings, headless=headless)
    print(f"[test] launching chromium (headless={headless})")
    try:
        async with async_playwright() as playwright:
            await rusada._launch(playwright)
            page = rusada._page

            if not await rusada.login(email, password):
                await shot(page, "login_failed")
                print(f"[test] FAIL: login failed (url={page.url})")
                return 3
            print("[test] LOGIN OK")
            await shot(page, "after_login")

            print(f"[test] goto course /course/{settings.default_course_id}")
            await page.goto(
                f"{settings.rusada_url}/course/{settings.default_course_id}",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            await wait_ready(page)
            await asyncio.sleep(3)
            await shot(page, "course")
            print(f"[test] course URL: {page.url}")
            print(f"[test] course title: {await page.title()}")

            if await page.locator("text=СТРАНИЦА НЕ НАЙДЕНА").count():
                print("[test] FAIL: course page returned 404")
                return 4

            if await page.locator('a[href*="certificate"], a[href*=".pdf"]').filter(has_text="Скачать").count():
                print("[test] OK: certificate already available")
            else:
                btn = rusada._start_button(page)
                print(f"[test] start button visible: {await btn.is_visible()}")
                if await btn.is_visible():
                    print(f"[test] start button text: {(await btn.text_content()).strip()}")
                else:
                    print("[test] FAIL: no start button and no certificate link")
                    return 5

            print("[test] PASS")
            return 0
    except Exception as exc:
        print(f"[test] ERROR: {exc!r}")
        if rusada._page is not None:
            await shot(rusada._page, "exception")
        return 1
    finally:
        await rusada._close_browser()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    email = args.email or settings.rusada_email
    password = args.password or settings.rusada_password
    if not email or not password:
        print("[test] missing credentials: pass --email/--password or set RUSADA_EMAIL/RUSADA_PASSWORD")
        sys.exit(2)

    try:
        rc = asyncio.run(asyncio.wait_for(main(email, password, headless=not args.headed), timeout=240))
    except asyncio.TimeoutError:
        print("[test] FAIL: timed out after 240s")
        rc = 6
    sys.exit(rc)
