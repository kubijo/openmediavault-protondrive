import { unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { Page } from 'playwright-core';
import { screenshot } from './artifacts.ts';
import { acquireGuestLease } from './guest-lease.ts';
import { requireCondition } from './layout.ts';
import { guestArguments } from './live.ts';
import type { ProbeOptions } from './options.ts';

const fixtureName = 'ownedUiProbe';

async function save(page: Page, button: string, method: string): Promise<void> {
    const response = page.waitForResponse(result => result.url().endsWith(`/${method}`));
    await page.getByRole('button', { name: button, exact: true }).click();
    requireCondition((await response).ok(), `${method} failed`);
}

async function setHour(page: Page, options: ProbeOptions, hour: string): Promise<void> {
    await page.goto(`${options.url}/protondrive/settings`);
    const field = page.getByRole('textbox', { name: 'Backup hour (server time)' });
    await field.fill(hour);
    await save(page, 'Save settings', 'SaveConfiguration');
    await page.reload();
    await field.waitFor();
    requireCondition((await field.inputValue()) === hour, 'Saved hour did not survive reload');
}

async function apply(page: Page, options: ProbeOptions): Promise<void> {
    await page.goto(`${options.url}/protondrive/`);
    const button = page.getByRole('button', { name: 'Apply changes', exact: true });
    await button.waitFor();
    await button.click();
    await page.getByRole('heading', { name: 'Operation', exact: true }).waitFor();
    await page.getByText('Completed', { exact: true }).waitFor({ timeout: 120_000 });
    const jobUrl = page.url();
    await page.reload();
    await page.getByText('Completed', { exact: true }).waitFor();
    requireCondition(page.url() === jobUrl, 'Job did not retain its durable URL');
}

/** Mutations are restricted to a disabled fixture and a restored schedule value. */
async function exerciseLockedEdits(
    page: Page,
    options: ProbeOptions,
    width: number,
    screenshots: string[],
): Promise<void> {
    requireCondition(
        options.expectedRoot === '/my-files/open-media-vault-proton-backup-development',
        'UI edits require the development root',
    );
    await page.goto(`${options.url}/protondrive/`);
    await page.getByRole('heading', { name: 'Backup', exact: true }).waitFor();
    requireCondition(
        (await page.getByRole('button', { name: 'Apply changes', exact: true }).count()) === 0,
        'Apply existing pending changes before the UI edit test',
    );
    await page.getByRole('link', { name: 'Settings', exact: true }).click();
    const hourField = page.getByRole('textbox', { name: 'Backup hour (server time)' });
    await hourField.waitFor();
    const originalHour = await hourField.inputValue();
    requireCondition(
        Number.isInteger(Number(originalHour)) && Number(originalHour) >= 0 && Number(originalHour) <= 23,
        'Invalid saved schedule hour',
    );
    const changedHour = String((Number(originalHour) + 1) % 24);
    const recovery = join(options.stateDir, 'instance', 'owned-ui-recovery.json');
    await writeFile(recovery, `${JSON.stringify({ version: 1, originalHour, fixtureName })}\n`, {
        flag: 'wx',
        mode: 0o600,
    });
    let created = false;
    let failure: unknown;
    try {
        await setHour(page, options, changedHour);
        await screenshot(page, options.output, `owned-settings-edited-${width}.png`, screenshots);
        await page.getByRole('link', { name: 'Backup sets', exact: true }).click();
        await page.getByRole('heading', { name: 'system', exact: true }).waitFor();
        requireCondition(
            (await page.getByRole('heading', { name: fixtureName, exact: true }).count()) === 0,
            'A previous test set exists; inspect it before retrying',
        );
        await page.getByRole('button', { name: 'Add backup set', exact: true }).click();
        await page.getByRole('textbox', { name: 'Name', exact: true }).fill(fixtureName);
        await page.getByRole('checkbox', { name: 'Enabled', exact: true }).uncheck();
        await page
            .getByRole('textbox', { name: 'Source directories, one per line' })
            .fill('/data/interactive-fixtures/system');
        await save(page, 'Save backup set', 'SaveSet');
        created = true;
        await page.reload();
        const card = page.getByRole('region', { name: `Backup set ${fixtureName}`, exact: true });
        await card.waitFor();
        await card.getByRole('button', { name: 'Edit', exact: true }).click();
        requireCondition(
            !(await page.getByRole('checkbox', { name: 'Enabled', exact: true }).isChecked()),
            'Disabled fixture became enabled',
        );
        await page.getByRole('textbox', { name: 'Excluded relative paths, one per line' }).fill('cache');
        await save(page, 'Save backup set', 'SaveSet');
        await screenshot(page, options.output, `owned-set-edited-${width}.png`, screenshots);
    } catch (error) {
        failure = error;
    } finally {
        try {
            if (created) {
                await page.goto(`${options.url}/protondrive/sets`);
                const card = page.getByRole('region', { name: `Backup set ${fixtureName}`, exact: true });
                await card.getByRole('button', { name: 'Delete', exact: true }).click();
                await save(page, 'Confirm deletion', 'DeleteSet');
            }
            await setHour(page, options, originalHour);
            await apply(page, options);
            await unlink(recovery);
            await screenshot(page, options.output, `owned-applied-${width}.png`, screenshots);
        } catch (error) {
            failure =
                failure === undefined
                    ? error
                    : new AggregateError([failure, error], 'UI edit test and restoration both failed');
        }
    }
    if (failure !== undefined) throw failure;
}

export async function exerciseOwnedEdits(
    page: Page,
    options: ProbeOptions,
    width: number,
    screenshots: string[],
    signal: AbortSignal,
): Promise<void> {
    const command = process.env.PROTONDRIVE_VM_COMMAND;
    requireCondition(command, 'Use the Nix web-probe app for VM configuration changes');
    const lost = () => {
        void page.close().catch(() => undefined);
    };
    const host = await acquireGuestLease(command, ['--state-dir', options.stateDir, 'flow-lease'], signal, lost);
    try {
        const guest = await acquireGuestLease(command, guestArguments(options.stateDir, 'lease'), signal, lost);
        try {
            await exerciseLockedEdits(page, options, width, screenshots);
            host.assertHeld();
            guest.assertHeld();
        } finally {
            await guest.release();
        }
    } finally {
        await host.release();
    }
}
