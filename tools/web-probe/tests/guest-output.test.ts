import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { test } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { collectGuestOutput, GuestOutput } from '../src/guest-output.ts';

test('streamed activities survive chunk boundaries and arrive before command completion', async () => {
    const stdout = new PassThrough();
    const command = Promise.withResolvers<void>();
    const activities: string[] = [];
    const result = collectGuestOutput(Object.assign(command.promise, { stdout }), activity =>
        activities.push(activity.file),
    );
    stdout.write('{"event":"activity","phase":"download","fi');
    stdout.write('le":"archive.tar.zst"}\n');
    await setImmediate();
    assert.deepEqual(activities, ['archive.tar.zst']);
    stdout.end('{"restored":true}');
    command.resolve();
    assert.deepEqual(await result, { restored: true });
});

test('stream failures are handled immediately and cannot mask command failure', async () => {
    for (const commandFails of [false, true]) {
        const stdout = new PassThrough();
        const command = Promise.withResolvers<void>();
        const commandError = new Error('guest failed with diagnostics');
        const streamError = new Error('broken output stream');
        const result = collectGuestOutput(Object.assign(command.promise, { stdout }), () => undefined);
        const checked = assert.rejects(result, error => error === (commandFails ? commandError : streamError));
        stdout.destroy(streamError);
        await setImmediate();
        if (commandFails) command.reject(commandError);
        else command.resolve();
        await checked;
        assert.equal(stdout.listenerCount('error'), 0);
    }
});

test('a success-shaped result cannot hide the command exit failure', async () => {
    const stdout = new PassThrough();
    const failure = new Error('remote command exited 1');
    const command = Promise.withResolvers<void>();
    const result = collectGuestOutput(Object.assign(command.promise, { stdout }), () => undefined);
    const checked = assert.rejects(result, error => error === failure);
    stdout.end('{"restored":true}\n');
    command.reject(failure);
    await checked;
});

test('guest activities arrive before completion and remain separate from the result', () => {
    const activities: string[] = [];
    const output = new GuestOutput(activity => activities.push(`${activity.phase}: ${activity.file}`));
    output.accept('{"event":"activity","phase":"Restore archive download","file":"backup.tar.zst"}');
    assert.deepEqual(activities, ['Restore archive download: backup.tar.zst']);
    assert.throws(() => output.result(), /Missing guest result/);
    output.accept('{"restored":true}');
    assert.deepEqual(output.result(), { restored: true });
});

test('malformed events and incomplete or duplicate results cannot report success', () => {
    for (const lines of [
        [],
        ['not JSON'],
        ['[]'],
        ['null'],
        ['{"event":"unknown"}', '{}'],
        ['{"event":"activity","phase":"download","file":3}', '{}'],
        ['{}', '{}'],
        ['{}', '{"event":"activity","phase":"late","file":"a"}'],
    ]) {
        const output = new GuestOutput(() => undefined);
        for (const line of lines) output.accept(line);
        assert.throws(() => output.result());
    }
});
