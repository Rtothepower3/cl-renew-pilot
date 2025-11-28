"""Phase 1 Craigslist renew pilot Actor.

Reads only the fields defined in .actor/input_schema.json, logs into Craigslist,
loads the manage postings page, prints visible rows, stores debug artifacts, and
writes a summary to the key-value store.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List

from apify import Actor
from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

LOGIN_URL = 'https://accounts.craigslist.org/login/home'


@dataclass
class InputConfig:
    mode: str
    listing_filter: Dict[str, object]
    screenshots: str
    delays: Dict[str, int]
    timeout_sec: int
    headless: bool


async def load_input() -> InputConfig:
    """Load Actor input and apply defaults per .actor/input_schema.json."""
    actor_input = await Actor.get_input() or {}

    listing_filter = actor_input.get('listing_filter') or {}
    delays = actor_input.get('delays') or {}

    return InputConfig(
        mode=actor_input.get('mode', 'dry-run'),
        listing_filter={
            'status_in': listing_filter.get('status_in', ['expired', 'redone', 'removed', 'deleted']),
            'title_includes': listing_filter.get('title_includes', []),
            'max_actions': listing_filter.get('max_actions', 5),
        },
        screenshots=actor_input.get('screenshots', 'summary'),
        delays={
            'min': delays.get('min', 300),
            'max': delays.get('max', 1200),
        },
        timeout_sec=actor_input.get('timeout_sec', 180),
        headless=actor_input.get('headless', True),
    )


async def login_craigslist(page: Page, email: str, password: str, timeout_ms: int) -> None:
    """Log in to Craigslist using provided credentials."""
    await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=timeout_ms)
    await page.wait_for_selector('#inputEmailHandle', timeout=timeout_ms)
    await page.fill('#inputEmailHandle', email)
    await page.fill('#inputPassword', password)

    login_button = page.locator('button[type="submit"], input[type="submit"]')
    if await login_button.count():
        await login_button.first.click()
    else:
        await page.press('#inputPassword', 'Enter')

    await page.wait_for_load_state('networkidle')
    try:
        await page.wait_for_selector(
            'a[href*="logout"], a[href*="logoff"], form[action*="logout"]',
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError('Login confirmation failed; logout control not found.') from exc


async def load_postings(page: Page, timeout_ms: int) -> None:
    """Ensure we land on the manage postings page and wait for rows to render."""
    if not page.url.startswith(LOGIN_URL):
        await page.goto(LOGIN_URL, wait_until='networkidle', timeout=timeout_ms)

    table_selector = 'table.account-table, table[data-event*="manage"], table'
    await page.wait_for_selector(table_selector, timeout=timeout_ms)


async def extract_postings(page: Page) -> List[str]:
    """Extract visible posting rows as simple text lines."""
    rows_locator = page.locator('table.account-table tr, table tr')
    count = await rows_locator.count()

    postings: List[str] = []
    for idx in range(count):
        row = rows_locator.nth(idx)

        if not await row.is_visible():
            continue

        if await row.locator('th').count():
            continue

        text = (await row.inner_text()).strip()
        if not text:
            continue

        cleaned = ' '.join(text.split())
        postings.append(cleaned)

    return postings


async def save_debug(context: BrowserContext) -> None:
    """Save a screenshot and HTML snapshot to the key-value store."""
    if not context.pages:
        return

    page = context.pages[-1]
    try:
        screenshot = await page.screenshot(full_page=True)
        html_content = await page.content()
    except Exception as exc:  # noqa: BLE001
        Actor.log.warning(f'Unable to capture debug artifacts: {exc}')
        return

    await Actor.set_value('login.png', screenshot, content_type='image/png')
    await Actor.set_value('page.html', html_content, content_type='text/html')


async def main() -> None:
    async with Actor:
        config = await load_input()

        email = os.getenv('CL_EMAIL')
        password = os.getenv('CL_PASSWORD')
        summary = {'status': 'error', 'postings_found': 0, 'mode': config.mode}

        if not email or not password:
            message = 'Environment variables CL_EMAIL and CL_PASSWORD must be set.'
            Actor.log.error(message)
            await Actor.set_value('summary.json', {**summary, 'message': message})
            return

        timeout_ms = config.timeout_sec * 1000

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=config.headless)
            context = await browser.new_context()
            context.set_default_navigation_timeout(timeout_ms)
            context.set_default_timeout(timeout_ms)
            page = await context.new_page()

            postings: List[str] = []
            try:
                await login_craigslist(page, email, password, timeout_ms)
                await load_postings(page, timeout_ms)
                postings = await extract_postings(page)

                for idx, posting in enumerate(postings, start=1):
                    Actor.log.info(f'Posting {idx}: {posting}')
                    print(f'Posting {idx}: {posting}')

                summary = {'status': 'ok', 'postings_found': len(postings), 'mode': config.mode}
            except Exception as exc:  # noqa: BLE001
                Actor.log.exception('Phase 1 flow failed.')
                summary = {**summary, 'message': str(exc), 'postings_found': len(postings)}
            finally:
                await save_debug(context)
                await Actor.set_value('summary.json', summary)
                await browser.close()
