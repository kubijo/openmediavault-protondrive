import type { Page } from 'playwright-core';
import { screenshot } from './artifacts.ts';
import { requireCondition } from './layout.ts';
import type { ProbeOptions } from './options.ts';
import { exerciseOwnedEdits } from './owned-ui-edits.ts';
import { exerciseOwnedRestore } from './owned-ui-restore.ts';

/** Read-only checks against the real nginx/session/controller chain. */
export async function checkOwnedUi(
    page: Page,
    options: ProbeOptions,
    width: number,
    screenshots: string[],
    signal: AbortSignal,
): Promise<string> {
    await page.goto(`${options.url}/protondrive/`);
    await page.getByRole('heading', { name: 'Backup', exact: true }).waitFor();
    if (options.expectSignedIn) await page.getByText('Signed in', { exact: true }).waitFor();
    await screenshot(page, options.output, `owned-overview-${width}.png`, screenshots);
    await page.getByRole('link', { name: 'Settings', exact: true }).click();
    const root = page.getByRole('textbox', { name: 'Proton Drive root directory' });
    await root.waitFor();
    const remoteFolder = await root.inputValue();
    requireCondition(remoteFolder === options.expectedRoot, `Unexpected remote root: ${remoteFolder}`);
    await screenshot(page, options.output, `owned-settings-${width}.png`, screenshots);
    await page.getByRole('link', { name: 'Backup sets', exact: true }).click();
    await page.getByText('appData', { exact: true }).waitFor();
    await page.getByText('system', { exact: true }).waitFor();
    await screenshot(page, options.output, `owned-sets-${width}.png`, screenshots);
    const geometry = await page.evaluate(() => ({
        content: document.documentElement.scrollWidth,
        viewport: innerWidth,
    }));
    requireCondition(geometry.content <= geometry.viewport, `Owned UI overflows at ${width}px`);
    if (options.ownedUiChanges) await exerciseOwnedEdits(page, options, width, screenshots, signal);
    if (options.ownedUiRestore) await exerciseOwnedRestore(page, options, width, screenshots, signal);
    if (options.expectSignedIn) {
        await page.goto(`${options.url}/protondrive/`);
        const response = page.waitForResponse(item => item.url().endsWith('/BrowseBackups'));
        await page.getByRole('link', { name: 'Remote backups', exact: true }).click();
        requireCondition((await response).ok(), 'Live remote backup browsing failed');
        await page.getByRole('button', { name: 'This NAS', exact: true }).waitFor();
        await screenshot(page, options.output, `owned-remote-${width}.png`, screenshots);
    }
    const endpoint = `${options.url}/protondrive/rpc/protondrive_api.v1.ControlService/GetStatus`;
    const noHeader = await page.request.post(endpoint, { data: {} });
    requireCondition(noHeader.status() === 403, 'RPC accepted a request without its CSRF header');
    await page.context().clearCookies();
    const forged = await page.request.post(endpoint, {
        data: {},
        headers: { 'X-Protondrive-Request': '1', 'X-Protondrive-Authenticated': '1', 'X-Protondrive-User': 'admin' },
    });
    requireCondition(forged.status() === 401, 'RPC trusted forged identity headers without an OMV session');
    return remoteFolder;
}
