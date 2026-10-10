import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chromium } from 'playwright-core';
import { readBackupStatus } from '../../src/backup-status.ts';

test('status snapshots tolerate cards disappearing and being replaced during refresh', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        page.setDefaultTimeout(500);
        assert.deepEqual(await readBackupStatus(page), { phase: '', item: '', success: '' });
        await page.setContent('<div role="alert"><p><strong>Recovery required</strong></p></div>');
        assert.equal((await readBackupStatus(page)).phase, 'recovery-required');
        await page.setContent('');
        await page.evaluate(() => {
            let revision = 0;
            setInterval(() => {
                const value = String(++revision);
                const field = (text: string) => {
                    const paragraph = document.createElement('p');
                    paragraph.textContent = text;
                    return paragraph;
                };
                const transfer = document.createElement('p');
                transfer.className = 'protondrive-transfer';
                for (const name of ['phase', 'file', 'elapsed']) {
                    const span = document.createElement('span');
                    span.className = `protondrive-transfer-${name}`;
                    span.textContent = value;
                    transfer.append(span);
                }
                document.body.replaceChildren(
                    field(`Last reported phase: ${value}`),
                    field(`Last successful backup: ${value}`),
                    ...(revision % 2 ? [field(`Current item: ${value}`)] : [transfer]),
                );
            }, 1);
        });
        for (let sample = 0; sample < 100; sample++) {
            const status = await readBackupStatus(page);
            assert.equal(status.phase, status.success);
            if (status.transfer) {
                assert.equal(status.item, '');
                assert.equal(status.transfer.phase, status.phase);
                assert.equal(status.transfer.file, status.phase);
                assert.equal(status.transfer.elapsed, status.phase);
            } else assert.equal(status.item, status.phase);
        }
    } finally {
        await browser.close();
    }
});
