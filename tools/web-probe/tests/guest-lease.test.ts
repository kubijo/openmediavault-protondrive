import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { acquireGuestLease } from '../src/guest-lease.ts';

const fixture = fileURLToPath(new URL('./fixtures/lease.ts', import.meta.url));

test('planned reboot release and reacquisition do not report an unexpected disconnect', async () => {
    let losses = 0;
    for (let cycle = 0; cycle < 2; cycle++) {
        const lease = await acquireGuestLease(
            process.execPath,
            [fixture],
            new AbortController().signal,
            () => losses++,
        );
        lease.assertHeld();
        await lease.release();
        assert.throws(() => lease.assertHeld(), /lock connection/);
    }
    assert.equal(losses, 0);
});

test('unexpected disconnect invalidates the lease and notifies its owner', async () => {
    const lost = Promise.withResolvers<void>();
    const lease = await acquireGuestLease(
        process.execPath,
        [fixture, '--disconnect'],
        new AbortController().signal,
        lost.resolve,
    );
    await lost.promise;
    assert.throws(() => lease.assertHeld(), /lock connection/);
    await lease.release();
});

test('cancelled acquisition never starts a lease', async () => {
    const controller = new AbortController();
    controller.abort(new Error('cancelled'));
    await assert.rejects(
        acquireGuestLease(process.execPath, [fixture], controller.signal, () => undefined),
        /cancelled/,
    );
});

test('release kills a stubborn descendant that keeps the transport pipes open', { timeout: 15_000 }, async () => {
    let losses = 0;
    const lease = await acquireGuestLease(
        process.execPath,
        [fixture, '--with-child'],
        new AbortController().signal,
        () => losses++,
    );
    await lease.release();
    assert.equal(losses, 0);
    assert.throws(() => lease.assertHeld(), /lock connection/);
});
