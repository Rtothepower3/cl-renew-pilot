"""Phase 1 Craigslist renew pilot Actor.

Reads only the fields defined in .actor/input_schema.json, logs into Craigslist,
loads the manage postings page, prints visible rows, stores debug artifacts, and
writes a summary to the key-value store.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List

import re

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
    '''Log in to Craigslist using provided credentials.'''
    await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=timeout_ms)

    await page.wait_for_selector('#inputEmailHandle', timeout=timeout_ms)
    await page.fill('#inputEmailHandle', email)
    await page.fill('#inputPassword', password)

    Actor.log.info(f"Debug login email={email}, password_length={len(password)}")

    async def _capture_after_click() -> None:
        """Store artifacts immediately after the login submission attempt."""
        await Actor.set_value("after_click_html.html", await page.content())
        await Actor.set_value("after_click_screenshot.png", await page.screenshot(full_page=True))

    try:
        login_by_name = page.get_by_role("button", name=re.compile(r"^\s*Log in\s*$", re.IGNORECASE))
        name_count = await login_by_name.count()
        if name_count >= 2:
            await login_by_name.nth(1).click(timeout=timeout_ms)
            await _capture_after_click()
        elif name_count == 1:
            await login_by_name.first.click(timeout=timeout_ms)
            await _capture_after_click()
        else:
            fallback_selector = (
                'form[action*="login"] button[type="submit"]:not([disabled]), '
                'form[action*="login"] input[type="submit"]:not([disabled])'
            )
            submit_buttons = page.locator(fallback_selector)
            submit_count = await submit_buttons.count()
            if submit_count >= 2:
                await submit_buttons.nth(1).click(timeout=timeout_ms)
                await _capture_after_click()
            elif submit_count == 1:
                await submit_buttons.first.click(timeout=timeout_ms)
                await _capture_after_click()
            else:
                # Last resort: press Enter on the password field to submit.
                await page.press('#inputPassword', 'Enter')
                await _capture_after_click()
    except PlaywrightTimeoutError:
        await page.press('#inputPassword', 'Enter')
        await _capture_after_click()

    # Limit login wait to avoid hanging the whole run on challenges or bad creds.
    await page.wait_for_load_state('networkidle')
    login_check_timeout = min(timeout_ms, 60_000)

    confirmation_selector = "h2.account-tab-header:has-text('postings')"
    try:
        await page.wait_for_selector(confirmation_selector, timeout=login_check_timeout)
        return
    except PlaywrightTimeoutError as exc:
        async def _is_visible(selector: str) -> bool:
            try:
                return await page.locator(selector).first.is_visible(timeout=2_000)
            except PlaywrightTimeoutError:
                return False

        current_url = page.url
        page_title = await page.title()
        login_form_visible = await _is_visible('#inputEmailHandle')
        captcha_visible = await _is_visible('iframe[src*="captcha"], .g-recaptcha')
        # error_visible checks generic warning/alert boxes (.warning, .alertbox) and account-level errors (.account-error).
        # This will catch banner-style errors Craigslist sometimes shows after failed login, but will miss inline field hints
        # or silent reloads with no visible error message if Craigslist uses other selectors for failures.
        error_visible = await _is_visible('.warning, .alertbox, .account-error')

        screenshot = await page.screenshot(full_page=True)
        html_content = await page.content()

        await Actor.set_value('login_failed_screenshot.png', screenshot, content_type='image/png')
        await Actor.set_value('login_failed_html.html', html_content, content_type='text/html')
        await Actor.set_value(
            'login_failed_meta.json',
            {
                'url': current_url,
                'title': page_title,
                'login_form_visible': login_form_visible,
                'captcha_visible': captcha_visible,
                'error_visible': error_visible,
            },
            content_type='application/json',
        )

        raise RuntimeError(
            f"Login confirmation failed. url={current_url} login_form_visible={login_form_visible} "
            f"captcha_visible={captcha_visible} error_visible={error_visible}"
        ) from exc


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
    await Actor.set_value(
        'page_meta.json',
        {'url': page.url, 'title': await page.title()},
        content_type='application/json',
    )


async def main() -> None:
    # Initialize the Actor runtime.
    async with Actor:
        # Test artefacts can be stored using Actor.set_value and retrieved later.
        await Actor.set_value("debug_hello.txt", "hello from this run")
        
        # Load configuration and environment variables.
        config = await load_input()

        # Retrieve credentials from environment variables.
        email = os.getenv('CL_EMAIL')
        password = os.getenv('CL_PASSWORD')
        
        # Initialize summary with default error status.
        summary = {'status': 'error', 'postings_found': 0, 'mode': config.mode}

        # Validate that both email and password are provided; exit early if not.
        if not email or not password:
            message = 'Environment variables CL_EMAIL and CL_PASSWORD must be set.'
            Actor.log.error(message)
            await Actor.set_value('summary.json', {**summary, 'message': message})
            return

        # Convert timeout from seconds to milliseconds for Playwright API.
        timeout_ms = config.timeout_sec * 1000

        # Launch browser and create a new context and page.
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=config.headless)
            context = await browser.new_context()
            context.set_default_navigation_timeout(timeout_ms)
            context.set_default_timeout(timeout_ms)
            page = await context.new_page()

            # Initialize postings list for extracted data.
            postings: List[str] = []
            
            # Main workflow with error handling.
            try:
                # Log in to Craigslist with provided credentials.
                await login_craigslist(page, email, password, timeout_ms)
                
                # Navigate to the manage postings page.
                await load_postings(page, timeout_ms)
                
                # Extract all visible postings from the page.
                postings = await extract_postings(page)

                # Log and print each extracted posting.
                for idx, posting in enumerate(postings, start=1):
                    Actor.log.info(f'Posting {idx}: {posting}')
                    print(f'Posting {idx}: {posting}')

                # Update summary to success status with posting count.
                summary = {'status': 'ok', 'postings_found': len(postings), 'mode': config.mode}
            
            # Handle any exceptions during the workflow.
            except Exception as exc:  # noqa: BLE001
                Actor.log.exception('Phase 1 flow failed.')
                summary = {**summary, 'message': str(exc), 'postings_found': len(postings)}
            
            # Always clean up: save debug artifacts, write summary, and close browser.
            finally:
                await save_debug(context)
                await Actor.set_value('summary.json', summary)
                await browser.close()
