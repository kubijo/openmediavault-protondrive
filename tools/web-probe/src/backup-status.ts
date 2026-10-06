import type { Page } from 'playwright-core';

export type BackupStatus = {
    phase: string;
    item: string;
    success: string;
    transfer?: { phase: string; file: string; elapsed: string };
};

/** One DOM snapshot, including optional nodes that may disappear on the next OMV refresh. */
export async function readBackupStatus(page: Page): Promise<BackupStatus> {
    return page.locator('p').evaluateAll((elements): BackupStatus => {
        const texts = elements.map(element => element.textContent?.trim() ?? '');
        const field = (prefix: string) =>
            texts
                .find(text => text.startsWith(prefix))
                ?.slice(prefix.length)
                .trim() ?? '';
        const transfer = elements.find(element => element.classList.contains('protondrive-transfer'));
        const part = (name: string) =>
            transfer?.querySelector(`.protondrive-transfer-${name}`)?.textContent?.trim() ?? '';
        return {
            phase: texts.includes('Recovery required') ? 'recovery-required' : field('Last reported phase:'),
            item: field('Current item:'),
            success: field('Last successful backup:'),
            ...(transfer ? { transfer: { phase: part('phase'), file: part('file'), elapsed: part('elapsed') } } : {}),
        };
    });
}
