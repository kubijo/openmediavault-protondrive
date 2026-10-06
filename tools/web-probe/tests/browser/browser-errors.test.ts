import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import { BrowserErrors } from '../../src/browser-errors.ts';

async function task(page: Page, filename: string): Promise<void> {
    await page.evaluate(async filename => {
        const response = await fetch('/rpc.php', {
            method: 'POST',
            body: JSON.stringify({ service: 'Exec', method: 'getOutput', params: { filename } }),
        });
        await response.text();
    }, filename);
}

test('only the exact verified task failure is expected; other HTTP and console errors still fail', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        await page.route('http://vm.test/**', route => route.fulfill({ contentType: 'text/html', body: '<p>VM</p>' }));
        await page.route('http://vm.test/rpc.php', route =>
            route.fulfill({
                status: 500,
                contentType: 'application/json',
                body: JSON.stringify({ error: { message: 'Container recovery incomplete: fixture' } }),
            }),
        );
        const errors = new BrowserErrors(page);
        await page.goto('http://vm.test/');
        await task(page, 'recovery-task');
        assert.ok(errors.messages().some(message => message.includes('HTTP 500')));
        await errors.expectTaskFailure('recovery-task', 'Container recovery incomplete:');
        assert.deepEqual(errors.messages(), []);

        await task(page, 'unrelated-task');
        assert.ok(errors.messages().some(message => message.includes('HTTP 500')));
        await assert.rejects(errors.expectTaskFailure('unrelated-task', 'different failure'), /unexpected error/);
        assert.ok(errors.messages().some(message => message.includes('HTTP 500')));
        await page.evaluate(() => console.error('application bug'));
        assert.ok(errors.messages().includes('application bug'));
    } finally {
        await browser.close();
    }
});

test('task failure expectation can wait for a future response without accepting unrelated failures', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        await page.route('http://vm.test/**', route => route.fulfill({ contentType: 'text/html', body: '<p>VM</p>' }));
        await page.route('http://vm.test/rpc.php', route =>
            route.fulfill({
                status: 500,
                contentType: 'application/json',
                body: JSON.stringify({ error: { message: 'Container recovery incomplete: fixture' } }),
            }),
        );
        const errors = new BrowserErrors(page);
        await page.goto('http://vm.test/');
        const waiting = errors.expectTaskFailure('expected', 'Container recovery incomplete:');
        await task(page, 'unrelated');
        await task(page, 'expected');
        await waiting;
        assert.equal(errors.messages().filter(message => message.startsWith('HTTP 500')).length, 1);
    } finally {
        await browser.close();
    }
});
