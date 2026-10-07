import { open, readFile, rename, unlink } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import type { Page } from 'playwright-core';
import { $ } from 'zx';
import { screenshot } from './artifacts.ts';
import { acquireGuestLease } from './guest-lease.ts';
import { collectGuestOutput } from './guest-output.ts';
import { requireCondition } from './layout.ts';
import { guestArguments } from './live.ts';
import type { ProbeOptions } from './options.ts';
import { waitForOwnedJob } from './owned-ui-job.ts';

const execute = $({ quiet: true, detached: true, timeout: 60_000 });
type FixtureAction = 'prepare' | 'verify' | 'cleanup';
type Recovery = { token: string; inspectionId?: string; extractionId?: string };

function uuid(value: unknown): string {
    requireCondition(
        typeof value === 'string' &&
            /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value),
        'Invalid restore recovery identifier',
    );
    return value;
}

async function saveRecovery(path: string, value: Recovery, initial = false): Promise<void> {
    const temporary = initial ? path : `${path}.${crypto.randomUUID()}.next`;
    const file = await open(temporary, 'wx', 0o600);
    try {
        await file.writeFile(`${JSON.stringify(value)}\n`);
        await file.sync();
    } finally {
        await file.close();
    }
    if (!initial) await rename(temporary, path);
    const directory = await open(dirname(path), 'r');
    try {
        await directory.sync();
    } finally {
        await directory.close();
    }
}

async function recordOperation(
    page: Page,
    path: string,
    state: Recovery,
    field: 'inspectionId' | 'extractionId',
): Promise<void> {
    await page.route(
        '**/protondrive_api.v1.ControlService/StartOperation',
        async route => {
            const value: unknown = JSON.parse(route.request().postData() ?? 'null');
            requireCondition(
                value !== null && typeof value === 'object' && 'requestId' in value,
                'Missing operation request identifier',
            );
            state[field] = uuid(value.requestId);
            // Persist the idempotency key before the server can admit this job.
            await saveRecovery(path, state);
            await route.continue();
        },
        { times: 1 },
    );
}

async function recover(page: Page, command: string, options: ProbeOptions, path: string): Promise<void> {
    const value: unknown = JSON.parse(await readFile(path, 'utf8'));
    requireCondition(
        value !== null && typeof value === 'object' && 'token' in value,
        'Invalid restore recovery record',
    );
    const token = uuid(value.token);
    const inspectionId = 'inspectionId' in value ? uuid(value.inspectionId) : undefined;
    const extractionId = 'extractionId' in value ? uuid(value.extractionId) : undefined;
    for (const id of [extractionId, inspectionId]) {
        if (!id) continue;
        await page.goto(`${options.url}/protondrive/jobs/${id}`);
        await waitForOwnedJob(page, { cancel: true, allowMissing: true });
    }
    if (inspectionId) {
        await page.goto(`${options.url}/protondrive/archives/${inspectionId}`);
        const released = page.waitForResponse(response => response.url().endsWith('/ReleaseArchive'));
        await page.getByRole('button', { name: 'Release local copy', exact: true }).click();
        requireCondition((await released).ok(), 'Could not release the owned inspection cache');
        await page.waitForURL('**/protondrive/backups');
    }
    await fixture(command, options, 'cleanup', token);
    await unlink(path);
}

async function fixture(
    command: string,
    options: ProbeOptions,
    action: FixtureAction,
    token: string,
): Promise<Record<string, unknown>> {
    const args = [
        '--state-dir',
        options.stateDir,
        'exec',
        '--',
        'env',
        'PYTHONPATH=/usr/share/openmediavault-protondrive',
        'python3',
        '/usr/local/lib/omv-protondrive-vm/owned_restore_guest.py',
        action,
        token,
    ];
    return collectGuestOutput(execute`${command} ${args}`, () => undefined);
}

async function completed(page: Page): Promise<void> {
    await page.getByRole('heading', { name: 'Operation', exact: true }).waitFor();
    const outcome = await waitForOwnedJob(page, { timeout: 300_000 });
    requireCondition(outcome === 'SUCCEEDED', `Restore operation finished with ${outcome}`);
}

export async function exerciseOwnedRestore(
    page: Page,
    options: ProbeOptions,
    width: number,
    screenshots: string[],
    signal: AbortSignal,
): Promise<void> {
    const command = process.env.PROTONDRIVE_VM_COMMAND;
    requireCondition(command, 'Use the Nix probe app for VM restore checks');
    const lost = () => {
        void page.close().catch(() => undefined);
    };
    const host = await acquireGuestLease(command, ['--state-dir', options.stateDir, 'flow-lease'], signal, lost);
    try {
        const guest = await acquireGuestLease(command, guestArguments(options.stateDir, 'lease'), signal, lost);
        try {
            const recovery = join(options.stateDir, 'instance', 'owned-restore-recovery.json');
            if (options.recover) {
                await recover(page, command, options, recovery);
                return;
            }
            const token = crypto.randomUUID();
            const state: Recovery = { token };
            await saveRecovery(recovery, state, true);
            const prepared = await fixture(command, options, 'prepare', token);
            requireCondition(typeof prepared.destination === 'string', 'Missing restore fixture destination');
            await page.goto(`${options.url}/protondrive/backups`);
            await page.getByRole('button', { name: 'This NAS', exact: true }).click();
            await page.getByRole('button', { name: 'system', exact: true }).click();
            await recordOperation(page, recovery, state, 'inspectionId');
            await page.getByRole('button', { name: 'Download and inspect', exact: true }).first().click();
            await completed(page);
            await page.getByRole('link', { name: 'Browse verified archive', exact: true }).click();
            const archiveUrl = page.url();
            await page
                .getByRole('checkbox', { name: 'data/interactive-fixtures/system/example.txt', exact: true })
                .check();
            await page
                .getByRole('textbox', { name: 'New extraction directory', exact: true })
                .fill(prepared.destination);
            await page.getByRole('button', { name: 'Preview extraction', exact: true }).click();
            await page.getByRole('heading', { name: 'Extraction preview', exact: true }).waitFor();
            await screenshot(page, options.output, `owned-extraction-preview-${width}.png`, screenshots);
            await recordOperation(page, recovery, state, 'extractionId');
            await page.getByRole('button', { name: 'Extract selected files', exact: true }).click();
            await completed(page);
            await page.reload();
            await page.getByText('Completed', { exact: true }).waitFor();
            const verified = await fixture(command, options, 'verify', token);
            requireCondition(verified.verified === true, 'Restored content verification failed');
            await screenshot(page, options.output, `owned-extraction-complete-${width}.png`, screenshots);
            await page.goto(archiveUrl);
            await page
                .getByRole('checkbox', { name: 'data/interactive-fixtures/system/example.txt', exact: true })
                .check();
            await page
                .getByRole('textbox', { name: 'New extraction directory', exact: true })
                .fill(prepared.destination);
            await page.getByRole('button', { name: 'Preview extraction', exact: true }).click();
            await page.getByText(/Extraction destination already exists/).waitFor();
            requireCondition(
                (await fixture(command, options, 'verify', token)).verified === true,
                'Conflict changed restored data',
            );
            await page.getByRole('button', { name: 'Release local copy', exact: true }).click();
            await page.waitForURL('**/protondrive/backups');
            await fixture(command, options, 'cleanup', token);
            await unlink(recovery);
            host.assertHeld();
            guest.assertHeld();
        } finally {
            await guest.release();
        }
    } finally {
        await host.release();
    }
}
