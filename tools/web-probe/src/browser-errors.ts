import type { Page, Response } from 'playwright-core';

/** Keep HTTP failures distinct from Chromium's duplicate console diagnostics. */
export class BrowserErrors {
    #page: Page;
    #exceptions: string[] = [];
    #console: { text: string; url: string }[] = [];
    #responses: Response[] = [];
    #expected = new Set<Response>();

    constructor(page: Page) {
        this.#page = page;
        page.on('pageerror', error => this.#exceptions.push(error.message));
        page.on('console', message => {
            if (message.type() === 'error') this.#console.push({ text: message.text(), url: message.location().url });
        });
        page.on('response', response => {
            if (response.status() >= 400) this.#responses.push(response);
        });
    }

    async expectTaskFailure(filename: string, message: string): Promise<void> {
        const matches = (response: Response): boolean => {
            if (response.status() !== 500) return false;
            try {
                const request: unknown = JSON.parse(response.request().postData() ?? 'null');
                return (
                    request !== null &&
                    typeof request === 'object' &&
                    'service' in request &&
                    request.service === 'Exec' &&
                    'method' in request &&
                    request.method === 'getOutput' &&
                    'params' in request &&
                    request.params !== null &&
                    typeof request.params === 'object' &&
                    'filename' in request.params &&
                    request.params.filename === filename
                );
            } catch {
                return false;
            }
        };
        const response = this.#responses.find(matches) ?? (await this.#page.waitForResponse(matches));
        const body: unknown = await response.json();
        if (
            body === null ||
            typeof body !== 'object' ||
            !('error' in body) ||
            body.error === null ||
            typeof body.error !== 'object' ||
            !('message' in body.error) ||
            typeof body.error.message !== 'string' ||
            !body.error.message.includes(message)
        ) {
            throw new Error('Task failed with an unexpected error');
        }
        this.#expected.add(response);
    }

    messages(): string[] {
        const consoleErrors = this.#console.filter(entry => {
            const match = /^Failed to load resource: the server responded with a status of (\d{3})\b/.exec(entry.text);
            // Only deduplicate a resource diagnostic when its actual HTTP response
            // is also recorded. Unexpected responses are still errors below.
            return (
                !match ||
                !this.#responses.some(
                    response => response.url() === entry.url && response.status() === Number(match[1]),
                )
            );
        });
        return [
            ...this.#exceptions,
            ...consoleErrors.map(entry => entry.text),
            ...this.#responses
                .filter(response => !this.#expected.has(response))
                .map(response => `HTTP ${response.status()} ${response.url()}`),
        ];
    }
}
