import type { Page } from 'playwright-core';

export type WebLogin = { username: string; password: string };

/** OMV's initial autofocus can move focus between fill() and text insertion. */
export async function submitLogin(page: Page, credentials: WebLogin): Promise<void> {
    const username = page.locator('input[type="text"]').first();
    const password = page.locator('input[type="password"]').first();
    for (let attempt = 0; attempt < 3; attempt++) {
        await username.fill(credentials.username);
        await password.fill(credentials.password);
        if (
            (await username.inputValue()) === credentials.username &&
            (await password.inputValue()) === credentials.password
        ) {
            await page.getByRole('button', { name: 'Log in', exact: true }).click();
            return;
        }
    }
    throw new Error('OMV login fields did not retain the supplied credentials');
}

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
