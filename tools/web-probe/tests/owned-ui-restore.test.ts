import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseRecovery } from '../src/owned-ui-restore.ts';

test('persisted restore recovery validates every identifier and the hold state', () => {
    const token = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
    const record = { token, inspectionId: token, extractionId: token, held: true };
    assert.deepEqual(parseRecovery(JSON.parse(JSON.stringify(record))), record);
    for (const value of [null, {}, { token: '../outside' }, { token, held: 'true' }, { token, extractionId: 'bad' }]) {
        assert.throws(() => parseRecovery(value));
    }
});
