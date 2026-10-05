import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Command, CommanderError, InvalidArgumentError, Option } from '@commander-js/extra-typings';

const DEFAULT_ROOT = '/my-files/open-media-vault-proton-backup-development';
const DEFAULT_WIDTHS = [1440, 420, 320];

export type ProbeOptions = {
    url: string;
    username: string;
    password: string;
    output: string;
    stateDir: string;
    live: boolean;
    uploadRetry: boolean;
    recover: boolean;
    widths: number[];
    expectedHour?: number;
    changedHour?: number;
    expectedRoot: string;
    changedRoot?: string;
    expectSignedIn: boolean;
    headed: boolean;
    json: boolean;
};

function width(value: string, previous: number[]): number[] {
    const parsed = Number(value);
    if (!Number.isInteger(parsed) || parsed < 240) throw new InvalidArgumentError('must be an integer >= 240');
    return [...previous, parsed];
}

function hour(value: string): number {
    const parsed = Number(value);
    if (!Number.isInteger(parsed) || parsed < 0 || parsed > 23)
        throw new InvalidArgumentError('must be an hour from 0 to 23');
    return parsed;
}

function root(value: string): string {
    if (!/^\/my-files\/(?!\.{1,2}$)(?!-)[A-Za-z0-9 _.-]+$/.test(value)) {
        throw new InvalidArgumentError('must be a direct child of /my-files');
    }
    return value;
}

export function optionsFromArgs(argv: string[] = process.argv.slice(2)): ProbeOptions | null {
    const command = new Command()
        .name('protondrive-web-probe')
        .description('Check the Proton Drive workbench in Chromium and record screenshots and video')
        .addOption(new Option('--url <URL>', 'OMV web URL').default('http://127.0.0.1:8080'))
        .addOption(
            new Option('--state-dir <DIR>', 'VM state directory containing the saved web login').default(
                '.tmp/interactive-vm',
            ),
        )
        .addOption(new Option('--username <NAME>', 'OMV login').default('admin'))
        .addOption(new Option('--password <VALUE>', 'Override the saved VM web password'))
        .addOption(
            new Option('--output <DIR>', 'Artifact directory (screenshots, video, report)').default('.tmp/web-probe'),
        )
        .addOption(
            new Option('--width <PIXELS>', `Viewport width; repeatable (default: ${DEFAULT_WIDTHS.join(', ')})`)
                .argParser(width)
                .default([] as number[]),
        )
        .addOption(new Option('--expect-hour <HOUR>', 'Assert the configured backup hour').argParser(hour))
        .addOption(
            new Option('--change-hour <HOUR>', 'Change and verify the backup hour (one width only)').argParser(hour),
        )
        .addOption(
            new Option('--expect-root <PATH>', 'Assert the configured Proton Drive root folder')
                .argParser(root)
                .default(DEFAULT_ROOT),
        )
        .addOption(
            new Option('--change-root <PATH>', 'Change and verify the root folder (one width only)').argParser(root),
        )
        .option('--expect-signed-in', 'Assert the account and an email are shown as signed in')
        .option(
            '--live',
            'Run a real UI backup, download/restore verification, and controlled cancellation in the test VM',
        )
        .option('--headed', 'Show the browser window')
        .option('--upload-retry', 'Test real upload interruption and pending retry (requires --live)')
        .option('--recover', 'Recover abandoned live-flow fixtures without prompting (requires --live)')
        .option('--json', 'Print a JSON result')
        .exitOverride();
    try {
        command.parse(argv, { from: 'user' });
    } catch (error) {
        if (error instanceof CommanderError && error.code === 'commander.helpDisplayed') return null;
        throw error;
    }
    const values = command.opts();
    if (values.recover && !values.live) throw new Error('--recover requires --live');
    if (values.uploadRetry && !values.live) throw new Error('--upload-retry requires --live');
    const widths = values.width.length ? values.width : values.live ? [420] : DEFAULT_WIDTHS;
    if (values.live && (widths.length !== 1 || values.changeHour !== undefined || values.changeRoot !== undefined)) {
        throw new Error('--live requires one viewport and cannot be combined with settings changes');
    }
    if (values.live) {
        const metadata: unknown = JSON.parse(readFileSync(join(values.stateDir, 'instance', 'instance.json'), 'utf8'));
        if (
            metadata === null ||
            typeof metadata !== 'object' ||
            !('http_port' in metadata) ||
            typeof metadata.http_port !== 'number' ||
            !Number.isInteger(metadata.http_port) ||
            metadata.http_port < 1 ||
            metadata.http_port > 65535
        ) {
            throw new Error('Saved VM state has no valid HTTP port');
        }
        if (values.url.replace(/\/+$/, '') !== `http://127.0.0.1:${metadata.http_port}`) {
            throw new Error("--live URL must match the selected VM's localhost HTTP port");
        }
    }
    if (values.changeHour !== undefined && widths.length !== 1) {
        throw new Error('--change-hour requires exactly one --width');
    }
    if (values.changeRoot !== undefined && widths.length !== 1) {
        throw new Error('--change-root requires exactly one --width');
    }
    const password =
        values.password ??
        readFileSync(join(values.stateDir, 'instance', 'admin-password'), 'utf8').replace(/\r?\n$/, '');
    if (!password || /[\r\n]/.test(password)) throw new Error('Saved VM web password is empty or malformed');
    return {
        url: values.url.replace(/\/+$/, ''),
        username: values.username,
        password,
        output: values.output,
        stateDir: values.stateDir,
        live: values.live ?? false,
        uploadRetry: values.uploadRetry ?? false,
        recover: values.recover ?? false,
        widths,
        expectedHour: values.expectHour,
        changedHour: values.changeHour,
        expectedRoot: values.expectRoot,
        changedRoot: values.changeRoot,
        expectSignedIn: (values.expectSignedIn || values.live) ?? false,
        headed: values.headed ?? false,
        json: values.json ?? false,
    };
}
