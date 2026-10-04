import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { parseArgs } from 'node:util';
import { chromium } from 'playwright-core';
import type { Browser, Page } from 'playwright-core';
import { checkOverviewLayout, checkSetsStart, requireCondition, scrollSetsRight } from './layout.ts';

export type ProbeOptions = {
    url: string;
    username: string;
    password: string;
    output: string;
    widths: number[];
    expectedHour?: number;
    changedHour?: number;
    headed: boolean;
    json: boolean;
};

export type ProbeResult = {
    ok: boolean;
    remote_folder?: string;
    screenshots?: string[];
    error?: string;
};

type ViewportResult = { screenshots: string[]; remoteFolder: string };

function optionsFromArgs(): ProbeOptions | null {
    const { values } = parseArgs({
        options: {
            url: { type: 'string', default: 'http://127.0.0.1:8080' },
            username: { type: 'string', default: 'admin' },
            password: { type: 'string', default: process.env.OMV_TEST_PASSWORD ?? 'admin' },
            output: { type: 'string', default: '.tmp/web-probe' },
            width: { type: 'string', multiple: true },
            'expect-hour': { type: 'string' },
            'change-hour': { type: 'string' },
            headed: { type: 'boolean', default: false },
            json: { type: 'boolean', default: false },
            help: { type: 'boolean', default: false },
        },
    });
    if (values.help) {
        console.log(`Usage: protondrive-web-probe [options]

  --url URL          OMV web URL (default: http://127.0.0.1:8080)
  --username NAME    OMV login (default: admin)
  --password VALUE   OMV password (or OMV_TEST_PASSWORD)
  --output DIR       Screenshot directory (default: .tmp/web-probe)
  --width PIXELS     Viewport width; repeatable (default: 1440, 420, 320)
  --expect-hour HOUR Assert the configured backup hour
  --change-hour HOUR Change and verify the backup hour (one width only)
  --headed           Show the browser window
  --json             Print a JSON result
  --help             Show this help`);
        return null;
    }
    const widths = values.width?.map(Number) ?? [1440, 420, 320];
    requireCondition(
        widths.every(width => Number.isInteger(width) && width >= 240),
        'Widths must be integers >= 240',
    );
    const expectedHour = values['expect-hour'] === undefined ? undefined : Number(values['expect-hour']);
    const changedHour = values['change-hour'] === undefined ? undefined : Number(values['change-hour']);
    for (const hour of [expectedHour, changedHour]) {
        requireCondition(
            hour === undefined || (Number.isInteger(hour) && hour >= 0 && hour <= 23),
            'Hours must be 0–23',
        );
    }
    requireCondition(changedHour === undefined || widths.length === 1, '--change-hour requires exactly one --width');
    return {
        url: values.url.replace(/\/+$/, ''),
        username: values.username,
        password: values.password,
        output: values.output,
        widths,
        expectedHour,
        changedHour,
        headed: values.headed,
        json: values.json,
    };
}

async function login(page: Page, options: ProbeOptions): Promise<void> {
    await page.goto(`${options.url}/#/services/protondrive/overview`, { waitUntil: 'domcontentloaded' });
    await page.locator('input[type="password"], .protondrive-status-heading').first().waitFor({ timeout: 30_000 });
    const password = page.locator('input[type="password"]');
    if (await password.count()) {
        await page.locator('input[type="text"]').first().fill(options.username);
        await password.first().fill(options.password);
        await page.getByRole('button', { name: 'Log in' }).click();
    }
    await page.getByRole('heading', { name: 'Account', exact: true }).waitFor({ timeout: 30_000 });
    requireCondition(page.url().includes('services/protondrive/overview'), `Overview did not open: ${page.url()}`);
    await page.getByText('Loading ...', { exact: true }).waitFor({ state: 'hidden', timeout: 15_000 });
}

async function screenshot(page: Page, output: string, name: string, screenshots: string[]): Promise<void> {
    const path = join(output, name);
    await page.screenshot({ path, fullPage: true });
    screenshots.push(path);
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

async function runViewport(browser: Browser, options: ProbeOptions, width: number): Promise<ViewportResult> {
    const context = await browser.newContext({ viewport: { width, height: 900 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    const errors: string[] = [];
    const screenshots: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
        if (message.type() === 'error') errors.push(message.text());
    });
    try {
        await login(page, options);
        requireCondition(await page.getByText('TEST VM', { exact: true }).isVisible(), 'Test VM banner is missing');
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

        await page.getByRole('button', { name: 'Settings', exact: true }).click();
        await page.getByText('Enable scheduled backups').waitFor();
        const remoteFolder = await page.getByRole('textbox', { name: 'Proton Drive folder' }).inputValue();
        requireCondition(remoteFolder.startsWith('/my-files/OMV Integration '), 'Fixture remote folder is missing');
        if (options.expectedHour !== undefined) {
            const hour = await page.getByRole('spinbutton', { name: 'Hour (NAS timezone)' }).inputValue();
            requireCondition(
                hour === String(options.expectedHour),
                `Expected backup hour ${options.expectedHour}, found ${hour}`,
            );
        }
        await screenshot(page, options.output, `settings-${width}.png`, screenshots);

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
        if (options.changedHour !== undefined) await changeHour(page, options, options.changedHour, screenshots);
        requireCondition(errors.length === 0, `Browser errors at ${width}px: ${errors.join('; ')}`);
        return { screenshots, remoteFolder };
    } catch (error) {
        try {
            await page.screenshot({
                path: join(options.output, `failure-${width}.png`),
                fullPage: true,
                timeout: 5_000,
            });
        } catch {
            // Preserve the original browser error.
        }
        throw error;
    } finally {
        await context.close();
    }
}

async function main(): Promise<number> {
    const options = optionsFromArgs();
    if (!options) return 0;
    await mkdir(options.output, { recursive: true });
    let browser: Browser | undefined;
    try {
        browser = await chromium.launch({ headless: !options.headed });
        const results: ViewportResult[] = [];
        for (const width of options.widths) results.push(await runViewport(browser, options, width));
        const remoteFolders = new Set(results.map(result => result.remoteFolder));
        requireCondition(remoteFolders.size === 1, 'The configured remote folder changed between viewport checks');
        const result: ProbeResult = {
            ok: true,
            remote_folder: [...remoteFolders][0],
            screenshots: results.flatMap(item => item.screenshots),
        };
        if (options.json) console.log(JSON.stringify(result));
        else console.log(`Web probe passed; screenshots: ${options.output}`);
        return 0;
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (options.json) console.log(JSON.stringify({ ok: false, error: message } satisfies ProbeResult));
        else console.error(`Web probe failed: ${message}`);
        return 1;
    } finally {
        await browser?.close();
    }
}

process.exitCode = await main().catch(error => {
    console.error(`Web probe arguments failed: ${error instanceof Error ? error.message : String(error)}`);
    return 2;
});
