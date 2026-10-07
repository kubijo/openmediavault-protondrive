import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chromium } from 'playwright-core';
import { prepareBrowserReboot, submitLogin, waitForOverview } from '../../src/session.ts';

test('overview readiness waits for the account response, not the loading placeholder', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        await page.setContent(
            '<style>h2,p{min-height:20px;min-width:100px}</style><h2>Account</h2><p>Account status unavailable</p><p>Not running</p>',
        );
        let ready = false;
        const waiting = waitForOverview(page, true).then(() => {
            ready = true;
        });
        await page.waitForTimeout(30);
        assert.equal(ready, false);
        await page.evaluate(() => {
            const account = document.querySelector('p');
            if (!account) throw new Error('Missing account fixture');
            account.textContent = 'Signed in';
        });
        await waiting;
        assert.equal(ready, true);
    } finally {
        await browser.close();
    }
});

test('reboot preparation unloads the app and discards its invalidated browser login', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const context = await browser.newContext();
        await context.route('http://vm.test/**', route =>
            route.fulfill({ contentType: 'text/html', body: '<p>VM</p>' }),
        );
        const page = await context.newPage();
        await page.goto('http://vm.test/');
        await context.addCookies([{ name: 'session', value: 'old-login', url: 'http://vm.test/' }]);
        await page.evaluate(() => {
            localStorage.setItem('login', 'old-login');
            sessionStorage.setItem('login', 'old-login');
        });
        await prepareBrowserReboot(page);
        assert.equal(page.url(), 'about:blank');
        assert.deepEqual(await context.cookies(), []);
        await page.goto('http://vm.test/');
        assert.deepEqual(await page.evaluate(() => [localStorage.length, sessionStorage.length]), [0, 0]);
    } finally {
        await browser.close();
    }
});

test('login repairs an autofocus redirect before submitting credentials', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        await page.route('http://vm.test/**', route =>
            route.fulfill({
                contentType: 'text/html',
                body: '<input type="text"><input type="password"><button>Log in</button>',
            }),
        );
        await page.goto('http://vm.test/');
        await page.evaluate(() => {
            const username = document.querySelector<HTMLInputElement>('input[type="text"]');
            const password = document.querySelector<HTMLInputElement>('input[type="password"]');
            const button = document.querySelector('button');
            if (!username || !password || !button) throw new Error('Missing login fixture');
            password.addEventListener('focus', () => username.focus(), { once: true });
            button.addEventListener('click', () => {
                button.dataset.login = `${username.value}:${password.value}`;
            });
        });
        await submitLogin(page, { username: 'admin', password: 'test-password' });
        assert.equal(await page.getByRole('button').getAttribute('data-login'), 'admin:test-password');
    } finally {
        await browser.close();
    }
});

test('login never submits fields that keep losing their values', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        await page.route('http://vm.test/**', route =>
            route.fulfill({
                contentType: 'text/html',
                body: '<input type="text"><input type="password"><button>Log in</button>',
            }),
        );
        await page.goto('http://vm.test/');
        await page.evaluate(() => {
            const password = document.querySelector<HTMLInputElement>('input[type="password"]');
            const button = document.querySelector('button');
            if (!password || !button) throw new Error('Missing login fixture');
            password.addEventListener('input', () => {
                password.value = '';
            });
            button.addEventListener('click', () => {
                button.dataset.submitted = 'true';
            });
        });
        await assert.rejects(submitLogin(page, { username: 'admin', password: 'test-password' }), /did not retain/);
        assert.equal(await page.getByRole('button').getAttribute('data-submitted'), null);
    } finally {
        await browser.close();
    }
});
