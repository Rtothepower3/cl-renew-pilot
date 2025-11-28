"""Phase 1 Craigslist renew pilot Actor.

Logs into Craigslist, navigates to the manage postings page, prints the visible
posting rows, saves debug artifacts, and stores a summary in the key-value store.
"""

from __future__ import annotations

import os
from typing import List

from apify import Actor
from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

LOGIN_URL = 'https://accounts.craigslist.org/login/home'


async def login_craigslist(page: Page, email: str, password: str) -> None:
    """Log in to Craigslist using provided credentials."""
    await page.goto(LOGIN_URL, wait_until='domcontentloaded')
    await page.wait_for_selector('#inputEmailHandle', timeout=15000)
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
            timeout=10000,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError('Login confirmation failed; logout control not found.') from exc


async def load_postings(page: Page) -> None:
    """Ensure we land on the manage postings page and wait for rows to render."""
    if not page.url.startswith(LOGIN_URL):
        await page.goto(LOGIN_URL, wait_until='networkidle')

    table_selector = 'table.account-table, table[data-event*="manage"], table'
    await page.wait_for_selector(table_selector, timeout=15000)


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
            # Skip header rows.
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
        email = os.getenv('CL_EMAIL')
        password = os.getenv('CL_PASSWORD')

        summary = {'status': 'error', 'postings_found': 0}

        if not email or not password:
            message = 'Environment variables CL_EMAIL and CL_PASSWORD must be set.'
            Actor.log.error(message)
            await Actor.set_value(
                'summary.json',
                {**summary, 'message': message},
            )
            return

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=Actor.configuration.headless)
            context = await browser.new_context()
            page = await context.new_page()

            postings: List[str] = []
            try:
                await login_craigslist(page, email, password)
                await load_postings(page)
                postings = await extract_postings(page)

                for idx, posting in enumerate(postings, start=1):
                    Actor.log.info(f'Posting {idx}: {posting}')
                    print(f'Posting {idx}: {posting}')

                summary = {'status': 'ok', 'postings_found': len(postings)}
            except Exception as exc:  # noqa: BLE001
                Actor.log.exception('Phase 1 flow failed.')
                summary = {
                    **summary,
                    'message': str(exc),
                    'postings_found': len(postings),
                }
            finally:
                await save_debug(context)
                await Actor.set_value('summary.json', summary)
                await browser.close()
