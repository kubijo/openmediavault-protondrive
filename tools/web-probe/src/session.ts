import type { Page } from 'playwright-core';

export async function waitForOverview(page: Page, signedIn: boolean): Promise<void> {
    await page.getByRole('heading', { name: 'Account', exact: true }).waitFor();
    if (signedIn) await page.getByText('Signed in', { exact: true }).waitFor({ timeout: 60_000 });
}

/** Stop OMV polling and discard the login that a reboot will invalidate. */
export async function prepareBrowserReboot(page: Page): Promise<void> {
    await page.evaluate(() => {
        localStorage.clear();
        sessionStorage.clear();
    });
    await page.goto('about:blank');
    await page.context().clearCookies();
}
