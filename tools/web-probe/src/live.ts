import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { createInterface as promptInterface } from 'node:readline/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { stripVTControlCharacters } from 'node:util';
import type { Page } from 'playwright-core';
import { $ } from 'zx';
import type { ProbeArtifacts } from './artifacts.ts';
import { screenshot } from './artifacts.ts';
import type { BackupStatus } from './backup-status.ts';
import { readBackupStatus } from './backup-status.ts';
import { collectGuestOutput } from './guest-output.ts';
import { requireCondition } from './layout.ts';
import type { ProbeOptions } from './options.ts';
import type { Progress, StageProgress } from './progress.ts';

const execute = $({ quiet: true, detached: true });
const STEP_UNIT = 'omv-protondrive-ui-step.service';
export type GuestAction =
    | 'lease'
    | 'inspect'
    | 'begin'
    | 'verify'
    | 'prepare-cancel'
    | 'held'
    | 'recovered'
    | 'prepare-retry'
    | 'retry-failed'
    | 'verify-retry'
    | 'cleanup';

export class GuestCommandError extends Error {
    readonly diagnostic: string;
    readonly plainDiagnostic: string;

    constructor(action: GuestAction, diagnostic: string, cause: unknown) {
        super(`Guest ${action} failed`, { cause });
        this.diagnostic = diagnostic.trimEnd();
        this.plainDiagnostic = stripVTControlCharacters(this.diagnostic);
    }
}

export function guestArguments(stateDir: string, action: GuestAction): string[] {
    return [
        '--state-dir',
        stateDir,
        'exec',
        ...(action === 'lease' ? ['--stdin'] : []),
        '--',
        ...(action === 'lease'
            ? []
            : [
                  'systemd-run',
                  '--quiet',
                  '--wait',
                  '--pipe',
                  '--collect',
                  '--service-type=exec',
                  `--unit=${STEP_UNIT}`,
                  '--property=KillMode=control-group',
                  '--property=TimeoutStopSec=15s',
                  '--property=RuntimeMaxSec=900s',
              ]),
        'env',
        'PYTHONPATH=/usr/share/openmediavault-protondrive',
        `FORCE_COLOR=${process.stderr.isTTY && process.env.NO_COLOR === undefined && process.env.TERM !== 'dumb' ? '1' : '0'}`,
        `COLUMNS=${process.stderr.columns || 100}`,
        'python3',
        '/usr/local/lib/omv-protondrive-vm/live_ui_guest.py',
        action,
    ];
}

async function guest(
    options: ProbeOptions,
    action: GuestAction,
    signal?: AbortSignal,
    progress?: StageProgress,
): Promise<Record<string, unknown>> {
    const command = process.env.PROTONDRIVE_VM_COMMAND;
    requireCondition(command, 'Use the Nix web-probe app for the live VM flow');
    signal?.throwIfAborted();
    let finished = false;
    let stopping: Promise<void> | undefined;
    const cancel = () => {
        stopping = (async () => {
            // A signal can arrive before SSH has created the transient unit.
            while (!finished) {
                try {
                    const args = ['--state-dir', options.stateDir, 'exec', '--', 'systemctl', 'stop', STEP_UNIT];
                    await execute({ timeout: 30_000 })`${command} ${args}`;
                } catch {
                    /* Retry until the step exits or the unit becomes visible. */
                }
                if (!finished) await delay(100);
            }
        })();
    };
    signal?.addEventListener('abort', cancel, { once: true });
    let result: Record<string, unknown>;
    try {
        const child = execute({ timeout: 900_000 })`${command} ${guestArguments(options.stateDir, action)}`;
        result = await collectGuestOutput(child, activity => progress?.update(activity.phase, activity.file));
    } catch (error) {
        signal?.throwIfAborted();
        if (error instanceof Error && 'stderr' in error && typeof error.stderr === 'string' && error.stderr.trim()) {
            throw new GuestCommandError(action, error.stderr, error);
        }
        throw error;
    } finally {
        finished = true;
        signal?.removeEventListener('abort', cancel);
        await stopping;
    }
    signal?.throwIfAborted();
    return result;
}

async function overview(page: Page, options: ProbeOptions): Promise<void> {
    await page.goto(`${options.url}/#/services/protondrive/overview`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: 'Account', exact: true }).waitFor();
}

async function start(page: Page, options: ProbeOptions, artifacts: ProbeArtifacts, name: string): Promise<void> {
    await clickOperation(page, 'Run now', 'runNow');
    await page.getByRole('dialog').waitFor();
    await screenshot(page, options.output, `${name}-task.png`, artifacts.screenshots);
    // Leaving the task view must not cancel the supervised backup.
    await overview(page, options);
    await page.getByText('Running', { exact: true }).waitFor({ timeout: 60_000 });
    await screenshot(page, options.output, `${name}-running.png`, artifacts.screenshots);
}

async function clickOperation(page: Page, label: string, method: string): Promise<void> {
    const response = page.waitForResponse(response => {
        const body = response.request().postData();
        if (!body) return false;
        try {
            const request: unknown = JSON.parse(body);
            return (
                request !== null &&
                typeof request === 'object' &&
                'service' in request &&
                request.service === 'ProtonDrive' &&
                'method' in request &&
                request.method === method
            );
        } catch {
            return false;
        }
    });
    const [reply] = await Promise.all([response, page.getByRole('button', { name: label, exact: true }).click()]);
    const body: unknown = await reply.json();
    requireCondition(
        reply.ok() && body !== null && typeof body === 'object' && 'error' in body && body.error === null,
        `UI ${label} request was rejected`,
    );
}

async function waitCompleted(page: Page, previous: string, progress: StageProgress): Promise<void> {
    const deadline = Date.now() + 600_000;
    while (true) {
        const { phase, item: detail, transfer: attributes, success } = await readBackupStatus(page);
        if (attributes?.phase) {
            progress.update(attributes.phase, attributes.file || undefined, Number(attributes.elapsed));
        } else if (phase) progress.update(`${phase}${detail ? ':' : ''}`, detail || undefined);
        requireCondition(phase !== 'failed' && phase !== 'recovery-failed', 'UI backup failed before completion');
        if (phase === 'completed' && success && (!previous || !success.includes(previous))) {
            await page.getByText('Not running', { exact: true }).waitFor();
            return;
        }
        requireCondition(Date.now() < deadline, 'Timed out waiting for UI backup completion');
        await delay(1000);
    }
}

export async function runLiveFlow(
    page: Page,
    options: ProbeOptions,
    artifacts: ProbeArtifacts,
    progress: Progress,
    signal: AbortSignal,
): Promise<void> {
    const command = process.env.PROTONDRIVE_VM_COMMAND;
    requireCondition(command, 'Use the Nix web-probe app for the live VM flow');
    const child = spawn(command, guestArguments(options.stateDir, 'lease'), {
        stdio: ['pipe', 'pipe', 'pipe'],
        detached: true,
    });
    const ready = Promise.withResolvers<void>();
    const abortLock = () => ready.reject(signal.reason);
    signal.addEventListener('abort', abortLock, { once: true });
    let diagnostic = '';
    let released = false;
    let lost = false;
    child.stderr.on('data', (data: Buffer) => {
        diagnostic = (diagnostic + data.toString()).slice(-1024 * 1024);
    });
    const lines = createInterface({ input: child.stdout });
    lines.on('line', line => {
        if (line === '{"locked": true}') ready.resolve();
    });
    child.once('error', error => ready.reject(error));
    child.stdin.on('error', error => ready.reject(error));
    const closed = new Promise<void>(resolve => {
        child.once('close', () => {
            lost = true;
            ready.reject(new GuestCommandError('lease', diagnostic || 'VM flow lock connection closed', undefined));
            if (!released) void page.close().catch(() => undefined);
            resolve();
        });
    });
    const timeout = setTimeout(() => ready.reject(new Error('Timed out acquiring the VM flow lock')), 30_000);
    try {
        await progress.stage('Lock live UI flow', () => ready.promise);
        clearTimeout(timeout);
        signal.throwIfAborted();
        await runLockedFlow(page, options, artifacts, progress, signal, () => {
            requireCondition(!lost, 'VM flow lock connection was lost; recovery is required');
        });
    } finally {
        clearTimeout(timeout);
        signal.removeEventListener('abort', abortLock);
        released = true;
        child.stdin.end();
        const stop = setTimeout(() => child.kill('SIGTERM'), 5000);
        try {
            await closed;
        } finally {
            clearTimeout(stop);
            lines.close();
        }
    }
}

export async function confirmRecovery(
    options: Pick<ProbeOptions, 'recover'>,
    ask = async () => {
        const prompt = promptInterface({ input: process.stdin, output: process.stderr });
        try {
            return await prompt.question(
                'Recover abandoned VM test fixtures? This restores saved configuration and removes only test fixtures. Remote backups are retained. [recover/ABORT] ',
                { signal },
            );
        } finally {
            prompt.close();
        }
    },
    interactive = Boolean(process.stdin.isTTY && process.stderr.isTTY),
    signal?: AbortSignal,
): Promise<boolean> {
    if (options.recover) return true;
    requireCondition(
        interactive,
        'Abandoned UI flow state exists. Rerun interactively or pass --recover to restore and remove its fixtures.',
    );
    return (await ask()).trim().toLowerCase() === 'recover';
}

async function runLockedFlow(
    page: Page,
    options: ProbeOptions,
    artifacts: ProbeArtifacts,
    progress: Progress,
    signal: AbortSignal,
    assertHeld: () => void,
): Promise<void> {
    let begun = false;
    let failure: unknown;
    const labels: Partial<Record<GuestAction, string>> = {
        begin: 'Check development VM and signed-in account',
        verify: 'Download and restore both archives',
        'prepare-cancel': 'Prepare cancellation fixtures',
        recovered: 'Verify container recovery and unchanged remote backups',
        cleanup: 'Restore VM configuration and remove fixtures',
        inspect: 'Check for abandoned live-flow state',
        'prepare-retry': 'Prepare scoped upload interruption',
        'retry-failed': 'Verify failed upload preserves local and remote backups',
        'verify-retry': 'Verify retry ordering and restore interrupted archive',
    };
    async function step(action: GuestAction): Promise<Record<string, unknown>> {
        assertHeld();
        const result = await progress.stage(labels[action] ?? action, stage =>
            guest(options, action, action === 'cleanup' ? undefined : signal, stage),
        );
        artifacts.steps.push({ name: action, result });
        return result;
    }
    try {
        const pending = await step('inspect');
        requireCondition(typeof pending.pending === 'boolean', 'Missing guest recovery state');
        if (pending.pending) {
            progress.note(`Abandoned live-flow state: ${String(pending.path)}`);
            requireCondition(
                await confirmRecovery(options, undefined, undefined, signal),
                'Live flow aborted; existing fixtures were left unchanged',
            );
            await step('cleanup');
        }
        begun = true;
        const initial = await step('begin');
        requireCondition(typeof initial.lastsuccess === 'string', 'Missing baseline backup status');
        if (options.uploadRetry) {
            await uploadRetryFlow(page, options, artifacts, progress, step);
        } else {
            await progress.stage('Start backup from web UI', async () => {
                await overview(page, options);
                await start(page, options, artifacts, 'backup');
            });
            const previous = initial.lastsuccess;
            await progress.stage('Wait for backup completion', stage => waitCompleted(page, previous, stage));
            await screenshot(page, options.output, 'backup-completed.png', artifacts.screenshots);
            await step('verify');

            await step('prepare-cancel');
            await progress.stage('Start cancellation test from web UI', async () => {
                await overview(page, options);
                await start(page, options, artifacts, 'cancel');
            });
            await progress.stage('Wait for containers to stop', async stage => {
                const deadline = Date.now() + 180_000;
                while (true) {
                    const result = await guest(options, 'held', signal);
                    if (result.held === true) break;
                    if (typeof result.phase === 'string') stage.update(result.phase);
                    requireCondition(Date.now() < deadline, 'Backup did not reach the controlled archive hold');
                    await delay(1000);
                }
            });
            artifacts.steps.push({ name: 'containers-stopped', result: { held: true } });
            await page.getByText('Last reported phase: archiving', { exact: true }).waitFor();
            await screenshot(page, options.output, 'cancel-before.png', artifacts.screenshots);
            await progress.stage('Cancel backup from web UI', async () => {
                await clickOperation(page, 'Cancel backup', 'cancelRun');
                await page.getByRole('dialog').waitFor();
                await screenshot(page, options.output, 'cancel-requested.png', artifacts.screenshots);
                await overview(page, options);
                await page.getByText('Not running', { exact: true }).waitFor({ timeout: 120_000 });
                await page.getByText(/Backup cancelled by signal/).waitFor();
            });
            await step('recovered');
            await screenshot(page, options.output, 'cancel-recovered.png', artifacts.screenshots);
        }
    } catch (error) {
        failure = error;
    }
    if (begun) {
        try {
            await step('cleanup');
        } catch (error) {
            if (error instanceof GuestCommandError) {
                const original =
                    failure instanceof GuestCommandError
                        ? failure.diagnostic
                        : failure === undefined
                          ? ''
                          : String(failure);
                throw new GuestCommandError(
                    'cleanup',
                    [original, error.diagnostic].filter(Boolean).join('\n\n'),
                    error,
                );
            }
            throw new Error(
                `Flow cleanup failed: ${String(error)}${failure ? `; original failure: ${String(failure)}` : ''}`,
            );
        }
    }
    if (failure !== undefined) throw failure;
}

async function uploadRetryFlow(
    page: Page,
    options: ProbeOptions,
    artifacts: ProbeArtifacts,
    progress: Progress,
    step: (action: GuestAction) => Promise<Record<string, unknown>>,
): Promise<void> {
    await overview(page, options);
    const original = (await readBackupStatus(page)).success;
    await progress.stage('Create confirmed baseline from web UI', () =>
        start(page, options, artifacts, 'retry-baseline'),
    );
    await progress.stage('Wait for confirmed baseline', stage => waitCompleted(page, original, stage));
    await step('verify');
    const previous = (await readBackupStatus(page)).success;
    await step('prepare-retry');
    await progress.stage('Start upload interruption test from web UI', () =>
        start(page, options, artifacts, 'interrupted-upload'),
    );
    await progress.stage('Wait for the interrupted upload to fail', async stage => {
        const deadline = Date.now() + 300_000;
        while (true) {
            const status = await readBackupStatus(page);
            if (status.transfer)
                stage.update(status.transfer.phase, status.transfer.file, Number(status.transfer.elapsed));
            else stage.update(status.phase, status.item || undefined);
            if (interruptedUploadFinished(status, previous)) {
                await page.getByText('Not running', { exact: true }).waitFor();
                await page.getByText('Last backup error:', { exact: true }).waitFor();
                break;
            }
            requireCondition(Date.now() < deadline, 'Timed out waiting for interrupted upload failure');
            await delay(1000);
        }
    });
    await screenshot(page, options.output, 'upload-failed.png', artifacts.screenshots);
    const failure = await step('retry-failed');
    requireCondition(
        failure.failed_safely === true && typeof failure.lastsuccess === 'string',
        'Missing failed-upload evidence',
    );
    await progress.stage('Retry pending upload from web UI', () => start(page, options, artifacts, 'upload-retry'));
    await progress.stage('Wait for successful upload retry', stage =>
        waitCompleted(page, failure.lastsuccess as string, stage),
    );
    await screenshot(page, options.output, 'upload-retry-completed.png', artifacts.screenshots);
    const result = await step('verify-retry');
    requireCondition(
        result.retried_before_container_stop === true && result.restored === true,
        'Missing retry and restore evidence',
    );
}

export function interruptedUploadFinished(status: BackupStatus, previous: string): boolean {
    requireCondition(status.phase !== 'recovery-failed', 'Container recovery failed during upload interruption');
    requireCondition(
        status.phase !== 'completed' || status.success === previous,
        'Upload completed instead of failing under the network fault',
    );
    return status.phase === 'failed';
}
