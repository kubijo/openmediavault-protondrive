import assert from 'node:assert/strict';
import { test } from 'node:test';
import { confirmRecovery, GuestCommandError, guestArguments, interruptedUploadFinished } from '../src/live.ts';

test('interruption polling tolerates stale completion but rejects a new success or recovery failure', () => {
    const baseline = { phase: 'completed', success: 'previous', item: '' };
    assert.equal(interruptedUploadFinished(baseline, 'previous'), false);
    assert.equal(interruptedUploadFinished({ ...baseline, phase: '' }, 'previous'), false);
    assert.equal(interruptedUploadFinished({ ...baseline, phase: 'uploading' }, 'previous'), false);
    assert.equal(interruptedUploadFinished({ ...baseline, phase: 'failed' }, 'previous'), true);
    assert.throws(() => interruptedUploadFinished({ ...baseline, success: 'new' }, 'previous'), /completed instead/);
    assert.throws(
        () => interruptedUploadFinished({ ...baseline, phase: 'recovery-failed' }, 'previous'),
        /Container recovery failed/,
    );
});

test('recovery defaults to abort and requires explicit consent in unattended runs', async () => {
    for (const answer of ['', 'abort', 'yes', 'unexpected']) {
        assert.equal(await confirmRecovery({ recover: false }, async () => answer, true), false);
    }
    assert.equal(await confirmRecovery({ recover: false }, async () => 'recover', true), true);
    const neverAsk = async () => {
        throw new Error('must not prompt');
    };
    assert.equal(await confirmRecovery({ recover: true }, neverAsk, false), true);
    await assert.rejects(confirmRecovery({ recover: false }, neverAsk, false), /pass --recover/);
});

test('guest crash preserves the rendered report separately from plain JSON diagnostics', () => {
    const cause = new Error('Command failed: /nix/store/long-command');
    const error = new GuestCommandError('begin', '\u001b[31mFileExistsError: fixture exists\u001b[0m\n', cause);
    assert.equal(error.message, 'Guest begin failed');
    assert.equal(error.cause, cause);
    assert.equal(error.diagnostic, '\u001b[31mFileExistsError: fixture exists\u001b[0m');
    assert.equal(error.plainDiagnostic, 'FileExistsError: fixture exists');
});

test('guest Rich color follows the parent terminal and NO_COLOR', context => {
    const { NO_COLOR: _noColor, ...env } = process.env;
    context.mock.property(process, 'env', { ...env, TERM: 'xterm-256color' });
    for (const [name, value] of [
        ['isTTY', true],
        ['columns', 120],
    ] as const) {
        const original = Object.getOwnPropertyDescriptor(process.stderr, name);
        Object.defineProperty(process.stderr, name, { configurable: true, value });
        context.after(() => {
            if (original) Object.defineProperty(process.stderr, name, original);
            else Reflect.deleteProperty(process.stderr, name);
        });
    }
    assert.ok(guestArguments('state with spaces', 'begin').includes('FORCE_COLOR=1'));
    assert.ok(guestArguments('state with spaces', 'begin').includes('COLUMNS=120'));
    assert.equal(guestArguments('state with spaces', 'begin')[1], 'state with spaces');
    process.env.NO_COLOR = '1';
    assert.ok(guestArguments('state', 'begin').includes('FORCE_COLOR=0'));
});
