import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { ProbeCancelled } from '../src/cancellation.ts';
import { Progress } from '../src/progress.ts';
import { TerminalCapture } from './fixtures/terminal.ts';

test('plain progress reports immediately, deduplicates details, and preserves the return value', async context => {
    const output = new TerminalCapture();
    let now = 0;
    const progress = new Progress({ stream: output.stream, now: () => now, interactive: false });
    context.after(() => {
        progress.close();
        output.dispose();
    });
    const result = await progress.stage(
        'Backup',
        async stage => {
            stage.update('uploading:', 'archive.tar.zst');
            stage.update('uploading:', 'archive.tar.zst');
            now = 2300;
            return 'archive';
        },
        { viewport: 420 },
    );
    const lines = await output.lines();
    assert.equal(result, 'archive');
    assert.ok(lines.includes('RUN  Backup  0.0s  420px'));
    assert.equal(lines.filter(line => line.includes('uploading')).length, 1);
    assert.ok(lines.some(line => line.includes('uploading:  0.0s  archive.tar.zst')));
    assert.ok(lines.includes('DONE  2.3s  420px  Backup'));
    assert.ok(!output.writes.some(chunk => chunk.includes('\u001b')));
});

test('heartbeats stop after failure and cancellation keeps its original error', async context => {
    for (const failure of [new Error('transfer failed'), new ProbeCancelled('SIGINT')]) {
        const output = new TerminalCapture();
        const progress = new Progress({ stream: output.stream, heartbeatMs: 5, interactive: false });
        context.after(() => {
            progress.close();
            output.dispose();
        });
        await assert.rejects(
            progress.stage('Restore', async () => {
                await delay(25);
                throw failure;
            }),
            error => error === failure,
        );
        const lines = await output.lines();
        assert.ok(lines.filter(line => line.startsWith('RUN')).length > 1);
        assert.ok(lines.some(line => line.startsWith(failure instanceof ProbeCancelled ? 'CANCEL' : 'FAILED')));
        const count = output.writes.length;
        await delay(20);
        assert.equal(output.writes.length, count);
    }
});

test('activity timing resets per file and stale UI samples cannot rewind it', async context => {
    const output = new TerminalCapture();
    let now = 0;
    const progress = new Progress({ stream: output.stream, interactive: true, color: false, now: () => now });
    context.after(() => {
        progress.close();
        output.dispose();
    });
    await progress.stage('Backup', async stage => {
        stage.update('Verification download', 'first.tar.zst', 10);
        now = 2000;
        stage.update('Verification download', 'first.tar.zst', 10);
        await delay(150);
        assert.ok((await output.lines()).some(line => line.includes('Verification download  12.0s  first.tar.zst')));
        stage.update('Manifest download', 'second.json', 0);
        assert.ok((await output.lines()).some(line => line.includes('Manifest download  0.0s  second.json')));
    });
});

test('stage completion moves its live line into the table; timer updates never add result rows', async context => {
    const output = new TerminalCapture();
    let now = 0;
    const progress = new Progress({ stream: output.stream, interactive: true, color: false, now: () => now });
    context.after(() => {
        progress.close();
        output.dispose();
    });
    await progress.stage('Launch Chromium', async () => undefined);
    const finish = Promise.withResolvers<void>();
    const work = progress.stage(
        'Log in to OMV',
        async stage => {
            stage.update('loading');
            await finish.promise;
        },
        { viewport: 420 },
    );
    now = 1700;
    await delay(150);
    let lines = await output.lines();
    assert.equal(lines.filter(line => line.includes('DONE')).length, 1);
    assert.ok(lines.some(line => line.includes('› Log in to OMV  1.7s  420px  loading')));
    finish.resolve();
    await work;
    lines = await output.lines();
    const rows = lines.filter(line => line.startsWith('│'));
    assert.equal(rows.length, 3);
    assert.ok(rows.at(-1)?.includes('1.7s'));
    assert.ok(!lines.some(line => line.includes('›')));
});
