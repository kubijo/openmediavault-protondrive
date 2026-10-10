import type { Page } from 'playwright-core';
import { requireCondition } from './layout.ts';

export type JobOutcome = 'SUCCEEDED' | 'FAILED' | 'CANCELLED' | 'INTERRUPTED' | 'MISSING';
export type JobWaitOptions = { cancel?: boolean; allowMissing?: boolean; timeout?: number };

/** Observe the rendered job; recovery may cancel work and tolerate unadmitted request IDs. */
export async function waitForOwnedJob(page: Page, options: JobWaitOptions = {}): Promise<JobOutcome> {
    const terminal = page.getByText(/^(SUCCEEDED|FAILED|CANCELLED|INTERRUPTED)$/);
    const error = page.getByRole('alert');
    const cancel = page.getByRole('button', { name: 'Cancel operation', exact: true });
    const timeout = options.timeout ?? 60_000;
    if (options.cancel) {
        await terminal.or(cancel).or(error).first().waitFor({ timeout });
        if (!(await terminal.count()) && (await cancel.count())) await cancel.click();
    }
    await terminal.or(error).first().waitFor({ timeout });
    // Interrupted jobs also show a warning. Their terminal status takes precedence.
    if (await terminal.count()) {
        const outcome = (await terminal.textContent())?.trim();
        requireCondition(
            outcome === 'SUCCEEDED' || outcome === 'FAILED' || outcome === 'CANCELLED' || outcome === 'INTERRUPTED',
            'Unrecognized terminal job status',
        );
        return outcome;
    }
    const message = (await error.first().textContent())?.trim() ?? '';
    requireCondition(
        options.allowMissing && message.includes('Job not found'),
        `Cannot determine job state: ${message}`,
    );
    return 'MISSING';
}
