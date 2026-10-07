import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { CommanderError } from '@commander-js/extra-typings';
import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ProbeArtifacts } from './artifacts.ts';
import { screenshot } from './artifacts.ts';
import { BrowserErrors } from './browser-errors.ts';
import { Cancellation, ProbeCancelled } from './cancellation.ts';
import { checkOverviewLayout, checkSetsStart, requireCondition, scrollSetsRight } from './layout.ts';
import { GuestCommandError, runLiveFlow } from './live.ts';
import type { ProbeOptions } from './options.ts';
import { optionsFromArgs } from './options.ts';
import { checkOwnedUi } from './owned-ui.ts';
import { Progress } from './progress.ts';
import { prepareBrowserReboot, submitLogin } from './session.ts';

export type { ProbeOptions } from './options.ts';

export type ProbeResult = ProbeArtifacts & {
    ok: boolean;
    remote_folder?: string;
    error?: string;
    diagnostic?: string;
};

type ViewportResult = { remoteFolder: string };

async function login(page: Page, options: ProbeOptions): Promise<void> {
    await page.goto(`${options.url}/#/services/protondrive/overview`, { waitUntil: 'domcontentloaded' });
    await page.locator('input[type="password"], .protondrive-status-heading').first().waitFor({ timeout: 30_000 });
    const password = page.locator('input[type="password"]');
    if (await password.count()) {
        await submitLogin(page, options);
    }
    await page.getByRole('heading', { name: 'Account', exact: true }).waitFor({ timeout: 30_000 });
    requireCondition(page.url().includes('services/protondrive/overview'), `Overview did not open: ${page.url()}`);
    await page.getByText('Loading ...', { exact: true }).waitFor({ state: 'hidden', timeout: 15_000 });
}

async function changeHour(page: Page, options: ProbeOptions, hour: number, screenshots: string[]): Promise<void> {
    await page.goto(`${options.url}/#/services/protondrive/settings`, { waitUntil: 'domcontentloaded' });
    const field = page.getByRole('spinbutton', { name: 'Hour (NAS timezone)' });
    await field.waitFor();
    requireCondition(
        (await field.inputValue()) !== String(hour),
        `Backup hour is already ${hour}; no change was exercised`,
    );
    await field.fill(String(hour));
    const save = page.getByRole('button', { name: 'Save', exact: true });
    requireCondition(await save.isEnabled(), 'Settings change did not enable Save');
    await save.click();
    await page.getByText('Updated Proton Drive settings.', { exact: true }).waitFor({ timeout: 15_000 });
    await page.goto(`${options.url}/#/services/protondrive/settings`, { waitUntil: 'domcontentloaded' });
    const restored = page.getByRole('spinbutton', { name: 'Hour (NAS timezone)' });
    await restored.waitFor();
    requireCondition(
        (await restored.inputValue()) === String(hour),
        `Backup hour ${hour} did not persist after reload`,
    );
    await screenshot(page, options.output, 'settings-changed.png', screenshots);
}

async function changeRoot(page: Page, options: ProbeOptions, root: string, screenshots: string[]): Promise<void> {
    await page.goto(`${options.url}/#/services/protondrive/settings`, { waitUntil: 'domcontentloaded' });
    const field = page.getByRole('textbox', { name: 'Proton Drive root folder' });
    await field.waitFor();
    requireCondition((await field.inputValue()) !== root, `Root folder is already ${root}; no change was exercised`);
    await field.fill(root);
    const save = page.getByRole('button', { name: 'Save', exact: true });
    requireCondition(await save.isEnabled(), 'Root folder change did not enable Save');
    await save.click();
    await page.getByText('Updated Proton Drive settings.', { exact: true }).waitFor({ timeout: 15_000 });
    await page.goto(`${options.url}/#/services/protondrive/settings`, { waitUntil: 'domcontentloaded' });
    const restored = page.getByRole('textbox', { name: 'Proton Drive root folder' });
    await restored.waitFor();
    requireCondition((await restored.inputValue()) === root, `Root folder ${root} did not persist after reload`);
    await screenshot(page, options.output, 'settings-root-changed.png', screenshots);
}

async function runViewport(
    browser: Browser,
    options: ProbeOptions,
    width: number,
    artifacts: ProbeArtifacts,
    progress: Progress,
    signal: AbortSignal,
): Promise<ViewportResult> {
    const viewport = { width, height: 900 };
    const context = await browser.newContext({
        viewport,
        deviceScaleFactor: 1,
        recordVideo: { dir: join(options.output, 'videos'), size: viewport },
    });
    const page = await context.newPage();
    const closePage = () => {
        void page.close().catch(() => undefined);
    };
    signal.addEventListener('abort', closePage, { once: true });
    let failure: unknown;
    let outcome: ViewportResult | undefined;
    const browserErrors = new BrowserErrors(page);
    const screenshots = artifacts.screenshots;
    try {
        signal.throwIfAborted();
        await progress.stage('Log in to OMV', () => login(page, options), { viewport: width });
        if (options.ownedUi) {
            const remoteFolder = await progress.stage(
                'Check owned UI',
                () => checkOwnedUi(page, options, width, screenshots, signal, browserErrors),
                { viewport: width },
            );
            const errors = browserErrors.messages();
            requireCondition(errors.length === 0, `Browser errors at ${width}px: ${errors.join('; ')}`);
            return { remoteFolder };
        }
        await progress.stage(
            'Check overview',
            async () => {
                if (options.expectSignedIn) {
                    await page.getByText('Signed in', { exact: true }).waitFor();
                    const email = page.getByText('Email:', { exact: true }).locator('..');
                    const accountText = (await email.textContent())?.trim() ?? '';
                    requireCondition(accountText.length > 'Email:'.length, 'Signed-in account email is missing');
                }
                requireCondition(
                    await page.getByText('TEST VM', { exact: true }).isVisible(),
                    'Test VM banner is missing',
                );
                requireCondition(
                    (await page.locator('link[href="/protondrive.css"]').count()) === 1,
                    'Plugin stylesheet was not loaded',
                );
                requireCondition(
                    await page.getByRole('heading', { name: 'Backup', exact: true }).isVisible(),
                    'Backup card is missing',
                );
                await checkOverviewLayout(page, width);
                await screenshot(page, options.output, `overview-${width}.png`, screenshots);
            },
            { viewport: width },
        );

        const remoteFolder = await progress.stage(
            'Check settings',
            async () => {
                await page.getByRole('button', { name: 'Settings', exact: true }).click();
                await page.getByText('Enable scheduled backups').waitFor();
                const remoteFolder = await page.getByRole('textbox', { name: 'Proton Drive root folder' }).inputValue();
                requireCondition(
                    remoteFolder === options.expectedRoot,
                    `Expected root ${options.expectedRoot}, found ${remoteFolder}`,
                );
                if (options.expectedHour !== undefined) {
                    const hour = await page.getByRole('spinbutton', { name: 'Hour (NAS timezone)' }).inputValue();
                    requireCondition(
                        hour === String(options.expectedHour),
                        `Expected backup hour ${options.expectedHour}, found ${hour}`,
                    );
                }
                await screenshot(page, options.output, `settings-${width}.png`, screenshots);
                return remoteFolder;
            },
            { viewport: width },
        );

        await progress.stage(
            'Check backup sets',
            async () => {
                await page.goto(`${options.url}/#/services/protondrive/overview`, { waitUntil: 'domcontentloaded' });
                await page.getByRole('button', { name: 'Backup sets', exact: true }).click();
                await page.getByText('appData', { exact: true }).waitFor();
                await page.getByText('system', { exact: true }).waitFor();
                const scrollableSets = await checkSetsStart(page, width);
                await screenshot(page, options.output, `backup-sets-${width}.png`, screenshots);
                if (scrollableSets) {
                    await scrollSetsRight(page, width);
                    await screenshot(page, options.output, `backup-sets-${width}-right.png`, screenshots);
                }
            },
            { viewport: width },
        );
        const hour = options.changedHour;
        if (hour !== undefined)
            await progress.stage('Change backup hour', () => changeHour(page, options, hour, screenshots), {
                viewport: width,
            });
        const root = options.changedRoot;
        if (root !== undefined)
            await progress.stage('Change backup root', () => changeRoot(page, options, root, screenshots), {
                viewport: width,
            });
        if (options.live)
            await runLiveFlow(
                page,
                options,
                artifacts,
                progress,
                signal,
                () => login(page, options),
                () => prepareBrowserReboot(page),
                browserErrors,
            );
        signal.throwIfAborted();
        const errors = browserErrors.messages();
        requireCondition(errors.length === 0, `Browser errors at ${width}px: ${errors.join('; ')}`);
        outcome = { remoteFolder };
    } catch (error) {
        failure = error;
        try {
            await screenshot(page, options.output, `failure-${width}.png`, screenshots);
        } catch {
            // Preserve the original browser error.
        }
    } finally {
        signal.removeEventListener('abort', closePage);
        try {
            await progress.stage(
                'Save video',
                async () => {
                    await context.close();
                    const video = page.video();
                    requireCondition(video, 'Browser did not create a video');
                    const path = join(options.output, `flow-${width}.webm`);
                    await video.saveAs(path);
                    await video.delete();
                    artifacts.videos.push(path);
                },
                { viewport: width },
            );
            progress.artifact('Video', resolve(join(options.output, `flow-${width}.webm`)));
        } catch (error) {
            if (failure === undefined) failure = error;
            else artifacts.warnings.push(`Could not save video: ${String(error)}`);
        }
    }
    if (failure !== undefined) throw failure;
    requireCondition(outcome, 'Viewport check did not finish');
    return outcome;
}

async function main(): Promise<number> {
    const options = optionsFromArgs();
    if (!options) return 0;
    await mkdir(options.output, { recursive: true });
    let browser: Browser | undefined;
    let diagnostic: string | undefined;
    const artifacts: ProbeArtifacts = { screenshots: [], videos: [], steps: [], warnings: [] };
    const result: ProbeResult = { ok: false, ...artifacts };
    const progress = new Progress();
    const cancellation = new Cancellation(message => progress.note(message));
    let exitCode = 0;
    progress.artifact('Artifacts', resolve(options.output));
    try {
        browser = await progress.stage('Launch Chromium', () =>
            chromium.launch({
                headless: !options.headed,
                handleSIGINT: false,
                handleSIGTERM: false,
                handleSIGHUP: false,
            }),
        );
        cancellation.signal.throwIfAborted();
        const results: ViewportResult[] = [];
        for (const width of options.widths)
            results.push(await runViewport(browser, options, width, artifacts, progress, cancellation.signal));
        const remoteFolders = new Set(results.map(result => result.remoteFolder));
        requireCondition(remoteFolders.size === 1, 'The configured remote folder changed between viewport checks');
        result.ok = true;
        result.remote_folder = [...remoteFolders][0];
    } catch (error) {
        const reported = cancellation.signal.aborted ? cancellation.signal.reason : error;
        if (reported !== error) artifacts.warnings.push(String(error));
        const message = reported instanceof Error ? reported.message : String(reported);
        result.error = message;
        if (error instanceof GuestCommandError) {
            diagnostic = error.diagnostic;
            result.diagnostic = error.plainDiagnostic;
        }
        exitCode = reported instanceof ProbeCancelled ? reported.exitCode : 1;
    } finally {
        try {
            await browser?.close();
        } catch (error) {
            artifacts.warnings.push(`Could not close browser: ${String(error)}`);
        }
        cancellation.dispose();
        if (cancellation.signal.aborted) {
            result.ok = false;
            const reason: unknown = cancellation.signal.reason;
            result.error = reason instanceof Error ? reason.message : String(reason);
            exitCode = reason instanceof ProbeCancelled ? reason.exitCode : 1;
        }
        progress.close();
        await writeFile(join(options.output, 'report.json'), `${JSON.stringify(result, null, 2)}\n`);
        if (diagnostic) console.error(diagnostic);
        if (options.json) console.log(JSON.stringify(result));
        else console.log(`Web probe ${result.ok ? 'passed' : `failed: ${result.error}`}; artifacts: ${options.output}`);
    }
    return exitCode;
}

process.exitCode = await main().catch(error => {
    if (!(error instanceof CommanderError)) {
        console.error(`Web probe arguments failed: ${error instanceof Error ? error.message : String(error)}`);
    }
    return 2;
});
