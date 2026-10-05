import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { optionsFromArgs } from '../src/options.ts';

const probe = fileURLToPath(new URL('../src/main.ts', import.meta.url));

function run(...args: string[]) {
    return spawnSync(process.execPath, [probe, ...args], {
        encoding: 'utf8',
        env: { ...process.env, OMV_TEST_PASSWORD: 'private-test-password' },
    });
}

test('saved VM password, defaults, and repeatable typed widths', () => {
    const state = mkdtempSync(join(tmpdir(), 'web-probe-options-'));
    mkdirSync(join(state, 'instance'));
    writeFileSync(join(state, 'instance', 'admin-password'), 'private-test-password\n');
    try {
        const defaults = optionsFromArgs(['--state-dir', state]);
        assert.ok(defaults);
        assert.deepEqual(defaults.widths, [1440, 420, 320]);
        assert.equal(defaults.password, 'private-test-password');
        assert.equal(defaults.expectedRoot, '/my-files/open-media-vault-proton-backup-development');
        const custom = optionsFromArgs([
            '--state-dir',
            state,
            '--width',
            '420',
            '--width',
            '320',
            '--expect-hour',
            '23',
            '--url',
            'http://nas/',
        ]);
        assert.ok(custom);
        assert.deepEqual(custom.widths, [420, 320]);
        assert.equal(custom.expectedHour, 23);
        assert.equal(custom.url, 'http://nas');
    } finally {
        rmSync(state, { recursive: true, force: true });
    }
});

test('a missing saved password fails unless explicitly overridden', () => {
    const state = mkdtempSync(join(tmpdir(), 'web-probe-empty-'));
    try {
        assert.throws(() => optionsFromArgs(['--state-dir', state]), /ENOENT/);
        const override = optionsFromArgs(['--state-dir', state, '--password', 'manual-password']);
        assert.ok(override);
        assert.equal(override.password, 'manual-password');
    } finally {
        rmSync(state, { recursive: true, force: true });
    }
});

test('generated help contains options but never an environment password', () => {
    const result = run('--help');
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /--width <PIXELS>/);
    assert.match(result.stdout, /--expect-root <PATH>/);
    assert.match(result.stdout, /--help/);
    assert.doesNotMatch(result.stdout, /private-test-password/);
});

test('bad typed values and incompatible changes exit with argument error', () => {
    for (const args of [
        ['--width', '239'],
        ['--width', '420.5'],
        ['--expect-hour', '24'],
        ['--expect-root', '/my-files/nested/folder'],
        ['--expect-root', '/my-files/.'],
        ['--expect-root', '/my-files/..'],
        ['--expect-root', '/my-files/-unsafe'],
        ['--change-root', '/my-files/new-root'],
        ['--change-hour', '12'],
        ['--live', '--width', '320', '--width', '420'],
        ['--live', '--change-hour', '12'],
        ['--unknown'],
    ]) {
        const result = run(...args);
        assert.equal(result.status, 2, `${args.join(' ')}: ${result.stderr}`);
        assert.equal(result.stdout, '');
    }
});

test('live flow selects one viewport and requires signed-in assertions', () => {
    const state = mkdtempSync(join(tmpdir(), 'web-probe-live-'));
    mkdirSync(join(state, 'instance'));
    const metadata = join(state, 'instance', 'instance.json');
    writeFileSync(metadata, '{"http_port":8080}');
    const args = ['--password', 'test', '--live', '--state-dir', state];
    try {
        const options = optionsFromArgs(args);
        assert.ok(options);
        assert.deepEqual(options.widths, [420]);
        assert.equal(options.live, true);
        assert.equal(options.recover, false);
        assert.equal(optionsFromArgs([...args, '--recover'])?.recover, true);
        assert.throws(() => optionsFromArgs(['--password', 'test', '--recover']), /requires --live/);
        assert.equal(options.expectSignedIn, true);
        assert.equal(options.stateDir, state);
        for (const url of ['https://production/', 'http://127.0.0.1:9999', 'http://user@127.0.0.1:8080']) {
            assert.throws(() => optionsFromArgs([...args, '--url', url]), /must match/);
        }
        writeFileSync(metadata, '{}');
        assert.throws(() => optionsFromArgs(args), /valid HTTP port/);
        rmSync(metadata);
        assert.throws(() => optionsFromArgs(args), /ENOENT/);
    } finally {
        rmSync(state, { recursive: true, force: true });
    }
});

test('one viewport permits a validated root or hour change', () => {
    const options = optionsFromArgs([
        '--password',
        'manual-password',
        '--width',
        '420',
        '--change-root',
        '/my-files/new-root',
        '--change-hour',
        '12',
        '--expect-signed-in',
        '--json',
    ]);
    assert.ok(options);
    assert.equal(options.changedRoot, '/my-files/new-root');
    assert.equal(options.changedHour, 12);
    assert.equal(options.expectSignedIn, true);
    assert.equal(options.json, true);
});
