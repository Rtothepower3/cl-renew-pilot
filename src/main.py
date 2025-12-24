"""Phase 1 Craigslist renew pilot Actor.

Reads only the fields defined in .actor/input_schema.json, logs into Craigslist,
loads the manage postings page, prints visible rows, stores debug artifacts, and
writes a summary to the key-value store.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import random
from time import monotonic
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
    manual_login: bool


async def load_input() -> InputConfig:
    """Load Actor input and apply defaults per .actor/input_schema.json."""
    actor_input = await Actor.get_input() or {}
    Actor.log.info(f"DEBUG: raw_input={actor_input}")
    # LOCAL_MANUAL_LOGIN_DEFAULT=true can be set when running locally
    # to default manual_login to True without touching actor input.
    #
    # In PowerShell:
    #   $env:LOCAL_MANUAL_LOGIN_DEFAULT = "true"
    #
    # In Command Prompt:
    #   set LOCAL_MANUAL_LOGIN_DEFAULT=true

    local_manual_login_default = os.getenv('LOCAL_MANUAL_LOGIN_DEFAULT') == 'true'

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
        manual_login=actor_input.get('manual_login', local_manual_login_default),
    )


async def save_cookies(context: BrowserContext) -> None:
    """Persist cookies to the default key-value store."""
    cookies = await context.cookies()
    await Actor.set_value('craigslist_cookies.json', cookies, content_type='application/json')


async def load_cookies(context: BrowserContext) -> bool:
    """Load cookies from the default key-value store. Returns True if applied."""
    stored = await Actor.get_value('craigslist_cookies.json')
    if not stored:
        Actor.log.warning(
            "No cookies found in key-value store (craigslist_cookies.json). "
            "If you just completed manual login locally, ensure storage was not purged."
        )
        return False

    # Only cookies are restored; storage state is omitted unless we see a need for it.
    await context.add_cookies(stored)
    return True


async def is_on_postings_page(page: Page) -> bool:
    """Detect if the postings header is visible."""
    try:
        return await page.locator("h2.account-tab-header:has-text('postings')").is_visible(timeout=2_000)
    except PlaywrightTimeoutError:
        return False


async def detect_verification_banner(page: Page) -> bool:
    """Best-effort detection for Craigslist verification banner."""
    selector = "text=Further verification required"
    try:
        return await page.locator(selector).first.is_visible(timeout=2_000)
    except PlaywrightTimeoutError:
        return False


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


async def gather_repost_targets(page: Page, listing_filter: Dict[str, object]) -> List[Dict[str, object]]:
    """Collect repostable rows with metadata."""
    targets: List[Dict[str, object]] = []
    rows = page.locator('table.account-table tr.posting-row, table tr.posting-row')
    total = await rows.count()

    title_filters = listing_filter.get('title_includes') or []
    status_filters = listing_filter.get('status_in') or []

    for idx in range(total):
        row = rows.nth(idx)
        if not await row.is_visible():
            continue

        repost_btn = row.locator('form.manage.repost input[type="submit"]')
        if await repost_btn.count() == 0:
            continue

        title_text = ""
        try:
            title_text = (await row.locator('td.title').inner_text()).strip()
        except Exception:
            pass

        status_text = ""
        try:
            status_text = (await row.locator('td.status').inner_text()).strip()
        except Exception:
            pass

        posting_id = None
        try:
            posting_id = await row.locator('td.status').get_attribute('data-postingid')
        except Exception:
            posting_id = None

        if title_filters:
            lower_title = title_text.lower()
            if not any(substr.lower() in lower_title for substr in title_filters):
                continue

        if status_filters and status_text:
            if status_text.lower() not in [s.lower() for s in status_filters]:
                continue

        targets.append(
            {
                'locator': repost_btn.first,
                'posting_id': posting_id,
                'title': title_text,
                'status': status_text,
            }
        )

    return targets


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
        config = await load_input()
        timeout_ms = config.timeout_sec * 1000
        summary = {'status': 'error', 'postings_found': 0, 'mode': config.mode}
        Actor.log.info(f"DEBUG: manual_login={config.manual_login}, headless={config.headless}")
        purge_on_start = os.getenv('APIFY_PURGE_ON_START', '').lower()
        if purge_on_start in ('1', 'true', 'yes'):
            Actor.log.warning(
                'APIFY_PURGE_ON_START is enabled; local storage will be cleared before this run.'
            )

        async with async_playwright() as playwright:
            headless_flag = False if config.manual_login else config.headless
            browser = await playwright.chromium.launch(headless=headless_flag)
            context = await browser.new_context()
            context.set_default_navigation_timeout(timeout_ms)
            context.set_default_timeout(timeout_ms)
            page = await context.new_page()

            postings: List[str] = []

            try:
                if config.manual_login:
                    Actor.log.info(
                        "Manual login enabled. Open Live View, complete the Craigslist login within 3 minutes, "
                        "and wait for the postings page to appear. Cookies will be saved automatically."
                    )
                    await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=timeout_ms)

                    start = monotonic()
                    poll_interval = 5
                    while monotonic() - start < config.timeout_sec:
                        if await is_on_postings_page(page):
                            await save_cookies(context)
                            summary = {
                                'status': 'ok',
                                'postings_found': 0,
                                'mode': config.mode,
                                'message': 'Manual login detected; cookies saved.',
                            }
                            Actor.log.info('Postings page detected; cookies saved. Exiting manual login mode.')
                            break
                        await asyncio.sleep(poll_interval)
                    else:
                        summary = {
                            'status': 'error',
                            'postings_found': 0,
                            'mode': config.mode,
                            'message': (
                                'Manual login not detected before timeout. '
                                'Please rerun with manual_login enabled and complete login via Live View.'
                            ),
                        }
                    return

                cookies_loaded = await load_cookies(context)
                if not cookies_loaded:
                    summary = {
                        'status': 'error',
                        'postings_found': 0,
                        'mode': config.mode,
                        'message': (
                            'Craigslist session expired or verification required. '
                            'Please rerun the Actor with manual_login enabled to refresh session cookies.'
                        ),
                    }
                    return

                await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=timeout_ms)

                if not await is_on_postings_page(page):
                    verification = await detect_verification_banner(page)
                    summary = {
                        'status': 'error',
                        'postings_found': 0,
                        'mode': config.mode,
                        'message': (
                            'Craigslist session expired or verification required. '
                            'Please rerun the Actor with manual_login enabled to refresh session cookies.'
                        ),
                        'verification_banner': verification,
                    }
                    return

                await load_postings(page, timeout_ms)
                repost_targets = await gather_repost_targets(page, config.listing_filter)
                initial_repost_found = len(repost_targets)
                max_actions = config.listing_filter.get('max_actions', 5)

                if config.mode not in ('dry-run', 'repost'):
                    summary = {
                        'status': 'error',
                        'mode': config.mode,
                        'repost_found': initial_repost_found,
                        'repost_clicked': 0,
                        'acted_on': [],
                        'message': f"Unsupported mode '{config.mode}'. Use 'dry-run' or 'repost'.",
                    }
                    return

                if config.mode == 'dry-run':
                    for tgt in repost_targets:
                        Actor.log.info(
                            f"[DRY RUN] Would repost posting_id={tgt.get('posting_id')} title='{tgt.get('title')}' status='{tgt.get('status')}'"
                        )
                    summary = {
                        'status': 'ok',
                        'mode': config.mode,
                        'repost_found': initial_repost_found,
                        'repost_clicked': 0,
                        'acted_on': [],
                        'message': 'Dry run completed.',
                    }
                    return

                repost_clicked = 0
                acted_on: List[Dict[str, object]] = []

                while repost_clicked < max_actions:
                    if not repost_targets:
                        break

                    tgt = repost_targets.pop(0)
                    delay_ms = random.randint(config.delays['min'], config.delays['max'])
                    await asyncio.sleep(delay_ms / 1000)

                    try:
                        Actor.log.info(
                            f"Clicking repost for posting_id={tgt.get('posting_id')} title='{tgt.get('title')}' status='{tgt.get('status')}'"
                        )
                        await tgt['locator'].click(timeout=timeout_ms)
                        repost_clicked += 1
                        acted_on.append({'posting_id': tgt.get('posting_id'), 'title': tgt.get('title')})
                    except Exception as exc:  # noqa: BLE001
                        Actor.log.warning(f'Failed to click repost for posting_id={tgt.get("posting_id")}: {exc}')

                    await asyncio.sleep(1)
                    await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=timeout_ms)

                    if not await is_on_postings_page(page):
                        verification = await detect_verification_banner(page)
                        summary = {
                            'status': 'error',
                            'mode': config.mode,
                            'repost_found': initial_repost_found,
                            'repost_clicked': repost_clicked,
                            'acted_on': acted_on,
                            'verification_banner': verification,
                            'message': (
                                'Lost authenticated session during repost. '
                                'Please rerun with manual_login enabled to refresh session cookies.'
                            ),
                        }
                        return

                    await load_postings(page, timeout_ms)
                    repost_targets = await gather_repost_targets(page, config.listing_filter)

                summary = {
                    'status': 'ok',
                    'mode': config.mode,
                    'repost_found': initial_repost_found,
                    'repost_clicked': repost_clicked,
                    'acted_on': acted_on,
                    'message': f'Repost run completed. Clicked {repost_clicked} postings.',
                }

            except Exception as exc:  # noqa: BLE001
                Actor.log.exception('Phase 1 flow failed.')
                summary = {**summary, 'message': str(exc), 'repost_clicked': 0}

            finally:
                await save_debug(context)
                await Actor.set_value('summary.json', summary)
                await browser.close()
