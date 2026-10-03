"""Browser checks and screenshots for a running OMV Proton Drive test VM.

This probe only needs a URL and login. VM lifecycle stays in interactive_vm.py.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


class ProbeFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def login(page, url: str, username: str, password: str) -> None:
    page.goto(f'{url}/#/services/protondrive/overview', wait_until='domcontentloaded')
    page.locator('input[type="password"], .protondrive-status-heading').first.wait_for(timeout=30_000)
    password_field = page.locator('input[type="password"]')
    if password_field.count():
        page.locator('input[type="text"]').first.fill(username)
        password_field.first.fill(password)
        page.get_by_role('button', name='Log in').click()
    page.get_by_role('heading', name='Account', exact=True).wait_for(timeout=30_000)
    require('services/protondrive/overview' in page.url, f'Overview did not open: {page.url}')
    page.wait_for_timeout(300)
    page.get_by_text('Loading ...', exact=True).wait_for(state='hidden', timeout=15_000)


def check_layout(page, width: int) -> None:
    geometry = page.evaluate(
        """() => {
            const actions = document.querySelector('omv-intuition-form-page > mat-card:has(.protondrive-status-heading) > mat-card-actions');
            if (!actions) return null;
            const bounds = actions.getBoundingClientRect();
            return {
                viewport: innerWidth,
                document: document.documentElement.scrollWidth,
                actions: { left: bounds.left, right: bounds.right },
                buttons: [...actions.querySelectorAll('button')].map(button => {
                    const box = button.getBoundingClientRect();
                    return { name: button.textContent.trim(), left: box.left, right: box.right,
                        top: box.top, bottom: box.bottom, disabled: button.disabled,
                        visible: getComputedStyle(button).display !== 'none' };
                })
            };
        }"""
    )
    require(geometry is not None, 'Overview action footer is missing')
    require(geometry['document'] <= geometry['viewport'], f'{width}px page has horizontal overflow: {geometry}')
    all_buttons = geometry['buttons']
    buttons = [button for button in all_buttons if button['visible']]
    require(len(all_buttons) == 7, f'Expected seven overview actions, found {len(all_buttons)}')
    require(
        all(button['disabled'] or button['visible'] for button in all_buttons),
        f'{width}px hides an enabled overview action',
    )
    require(len(buttons) >= 2, 'Overview has no visible navigation actions')
    if width <= 600:
        require(
            all(not button['disabled'] or not button['visible'] for button in all_buttons),
            f'{width}px shows a disabled overview action',
        )
        settings = next((button for button in buttons if button['name'] == 'Settings'), None)
        sets = next((button for button in buttons if button['name'] == 'Backup sets'), None)
        require(settings is not None and sets is not None, 'Mobile overview navigation actions are missing')
        require(abs(settings['top'] - sets['top']) <= 1, f'{width}px navigation actions did not share a row')
    for button in buttons:
        require(
            button['left'] >= geometry['actions']['left'] - 1 and button['right'] <= geometry['actions']['right'] + 1,
            f'{width}px action {button["name"]!r} escapes its footer',
        )
    for index, first in enumerate(buttons):
        for second in buttons[index + 1 :]:
            overlap = min(first['right'], second['right']) - max(first['left'], second['left'])
            vertical = min(first['bottom'], second['bottom']) - max(first['top'], second['top'])
            require(overlap <= 1 or vertical <= 1, f'{width}px actions overlap: {first["name"]}, {second["name"]}')


def run_viewport(
    browser,
    url: str,
    username: str,
    password: str,
    output: Path,
    width: int,
    expected_hour: int | None,
    changed_hour: int | None,
) -> tuple[list[str], str]:
    context = browser.new_context(viewport={'width': width, 'height': 900}, device_scale_factor=1)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
    screenshots = []
    try:
        login(page, url, username, password)
        require(page.get_by_text('TEST VM', exact=True).is_visible(), 'Test VM banner is missing')
        require(
            page.locator('link[href="/protondrive.css"]').count() == 1,
            'Plugin stylesheet was not loaded',
        )
        require(page.get_by_role('heading', name='Backup', exact=True).is_visible(), 'Backup card is missing')
        check_layout(page, width)
        image = output / f'overview-{width}.png'
        page.screenshot(path=image, full_page=True)
        screenshots.append(str(image))

        page.get_by_role('button', name='Settings', exact=True).click()
        page.get_by_text('Enable scheduled backups').wait_for()
        remote_folder = page.get_by_role('textbox', name='Proton Drive folder').input_value()
        require(remote_folder.startswith('/my-files/OMV Integration '), 'Fixture remote folder is missing')
        if expected_hour is not None:
            hour = page.get_by_role('spinbutton', name='Hour (NAS timezone)').input_value()
            require(hour == str(expected_hour), f'Expected backup hour {expected_hour}, found {hour}')
        image = output / f'settings-{width}.png'
        page.screenshot(path=image, full_page=True)
        screenshots.append(str(image))

        page.goto(f'{url}/#/services/protondrive/overview', wait_until='domcontentloaded')
        page.get_by_role('button', name='Backup sets', exact=True).click()
        page.get_by_text('appData', exact=True).wait_for()
        page.get_by_text('system', exact=True).wait_for()
        image = output / f'backup-sets-{width}.png'
        page.screenshot(path=image, full_page=True)
        screenshots.append(str(image))
        if changed_hour is not None:
            screenshots.append(change_hour(page, url, output, changed_hour))
        require(not errors, f'Browser errors at {width}px: {errors}')
        return screenshots, remote_folder
    except Exception:
        try:
            page.screenshot(path=output / f'failure-{width}.png', full_page=True, timeout=5_000)
        except PlaywrightError:
            pass
        raise
    finally:
        context.close()


def change_hour(page, url: str, output: Path, hour: int) -> str:
    page.goto(f'{url}/#/services/protondrive/settings', wait_until='domcontentloaded')
    field = page.get_by_role('spinbutton', name='Hour (NAS timezone)')
    field.wait_for()
    require(field.input_value() != str(hour), f'Backup hour is already {hour}; no change was exercised')
    field.fill(str(hour))
    save = page.get_by_role('button', name='Save', exact=True)
    require(save.is_enabled(), 'Settings change did not enable Save')
    save.click()
    page.get_by_text('Updated Proton Drive settings.', exact=True).wait_for(timeout=15_000)
    page.goto(f'{url}/#/services/protondrive/settings', wait_until='domcontentloaded')
    field = page.get_by_role('spinbutton', name='Hour (NAS timezone)')
    field.wait_for()
    require(field.input_value() == str(hour), f'Backup hour {hour} did not persist after reload')
    image = output / 'settings-changed.png'
    page.screenshot(path=image, full_page=True)
    return str(image)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--username', default='admin')
    parser.add_argument('--password', default=os.environ.get('OMV_TEST_PASSWORD', 'admin'))
    parser.add_argument('--output', type=Path, default=Path('.tmp/web-probe'))
    parser.add_argument('--width', type=int, action='append')
    parser.add_argument('--expect-hour', type=int)
    parser.add_argument('--change-hour', type=int)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--json', action='store_true')
    options = parser.parse_args()
    widths = options.width or [1440, 420, 320]
    if options.change_hour is not None and not 0 <= options.change_hour <= 23:
        parser.error('--change-hour must be between 0 and 23')
    if options.change_hour is not None and len(widths) != 1:
        parser.error('--change-hour requires exactly one --width')
    options.output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not options.headed)
            try:
                results = [
                    run_viewport(
                        browser,
                        options.url.rstrip('/'),
                        options.username,
                        options.password,
                        options.output,
                        width,
                        options.expect_hour,
                        options.change_hour,
                    )
                    for width in widths
                ]
                screenshots = [screenshot for images, _ in results for screenshot in images]
                remote_folders = {remote for _, remote in results}
                require(len(remote_folders) == 1, 'The configured remote folder changed between viewport checks')
                remote_folder = remote_folders.pop()
            finally:
                browser.close()
    except (OSError, PlaywrightError, ProbeFailure) as error:
        if options.json:
            print(json.dumps({'ok': False, 'error': str(error)}))
        else:
            print(f'Web probe failed: {error}', file=sys.stderr)
        return 1
    if options.json:
        print(json.dumps({'ok': True, 'remote_folder': remote_folder, 'screenshots': screenshots}))
    else:
        print(f'Web probe passed; screenshots: {options.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
