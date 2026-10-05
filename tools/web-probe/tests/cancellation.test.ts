import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Cancellation, ProbeCancelled } from '../src/cancellation.ts';

test('Ctrl+C requests cleanup once and preserves exit 130 until handlers are removed', () => {
    const messages: string[] = [];
    const previous = process.listenerCount('SIGINT');
    const cancellation = new Cancellation(message => messages.push(message));
    try {
        process.emit('SIGINT');
        process.emit('SIGTERM');
        assert.equal(messages.length, 1);
        assert.equal(cancellation.signal.aborted, true);
        assert.ok(cancellation.signal.reason instanceof ProbeCancelled);
        assert.equal(cancellation.signal.reason.exitCode, 130);
        assert.throws(() => cancellation.signal.throwIfAborted(), /Cancelled by SIGINT/);
    } finally {
        cancellation.dispose();
    }
    assert.equal(process.listenerCount('SIGINT'), previous);
});
