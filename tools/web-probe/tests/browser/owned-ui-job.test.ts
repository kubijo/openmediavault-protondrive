import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import { waitForOwnedJob } from '../../src/owned-ui-job.ts';

async function show(page: Page, content: string): Promise<void> {
    page.setDefaultTimeout(2000);
    await page.setContent(content);
    // Hermetic Chromium has no system fonts; give these minimal fixtures layout.
    await page.addStyleTag({ content: 'span,button,div{display:block;min-height:20px;min-width:100px}' });
}

test('recovery recognizes interrupted jobs despite their accompanying warning', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        await show(page, '<span>INTERRUPTED</span><div role="alert">Inspect backup status before retrying.</div>');
        assert.equal(await waitForOwnedJob(page, { cancel: true, allowMissing: true }), 'INTERRUPTED');
        await show(page, '<span>FAILED</span><p>Checksum verification failed</p>');
        assert.equal(await waitForOwnedJob(page), 'FAILED');
    } finally {
        await browser.close();
    }
});

test('recovery waits for cancellation to finish before returning', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        await show(page, '<span>RUNNING</span><button>Cancel operation</button>');
        await page.evaluate(() => {
            document.querySelector('button')?.addEventListener('click', () => {
                const status = document.querySelector('span');
                if (status) status.textContent = 'CANCELLED';
                document.querySelector('button')?.remove();
            });
        });
        assert.equal(await waitForOwnedJob(page, { cancel: true }), 'CANCELLED');
    } finally {
        await browser.close();
    }
});

test('only recovery accepts missing jobs; transport failures never authorize cleanup', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        await show(page, '<div role="alert">Job not found</div>');
        assert.equal(await waitForOwnedJob(page, { allowMissing: true }), 'MISSING');
        await assert.rejects(waitForOwnedJob(page), /Cannot determine job state/);
        await show(page, '<div role="alert">Controller unavailable</div>');
        await assert.rejects(waitForOwnedJob(page, { allowMissing: true }), /Controller unavailable/);
    } finally {
        await browser.close();
    }
});
