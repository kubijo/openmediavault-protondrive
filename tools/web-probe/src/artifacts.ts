import { join } from 'node:path';
import type { Page } from 'playwright-core';

export type FlowStep = { name: string; result: Record<string, unknown> };

export type ProbeArtifacts = {
    screenshots: string[];
    videos: string[];
    steps: FlowStep[];
    warnings: string[];
};

export async function screenshot(page: Page, output: string, name: string, screenshots: string[]): Promise<void> {
    const path = join(output, name);
    await page.screenshot({ path, fullPage: true, animations: 'disabled', timeout: 10_000 });
    screenshots.push(path);
}
