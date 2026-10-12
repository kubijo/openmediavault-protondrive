import { ProbeCancelled } from './cancellation.ts';
import type { TerminalOptions } from './terminal/table.ts';
import { LiveTable } from './terminal/table.ts';

export type StageProgress = { update: (detail: string, path?: string, elapsedSeconds?: number) => void };
export type StageOptions = { viewport?: number };
export type ProgressOptions = TerminalOptions & { now?: () => number; heartbeatMs?: number };
type Result = { status: 'DONE' | 'FAILED' | 'CANCEL'; time: string; viewport: string; name: string };

/** Probe-specific stage timing; terminal layout belongs to LiveTable. */
export class Progress {
    private readonly view: LiveTable<Result>;
    private readonly now: () => number;
    private readonly heartbeatMs: number;

    constructor(options: ProgressOptions = {}) {
        this.now = options.now ?? (() => performance.now());
        this.heartbeatMs = options.heartbeatMs ?? 15_000;
        this.view = new LiveTable<Result>(
            [
                {
                    title: 'STATUS',
                    width: 6,
                    value: row => ({
                        text: row.status,
                        tone: row.status === 'DONE' ? 'success' : row.status === 'CANCEL' ? 'warning' : 'error',
                    }),
                },
                { title: 'TIME', width: 7, align: 'right', value: row => ({ text: row.time, tone: 'muted' }) },
                { title: 'VIEWPORT', width: 8, align: 'right', value: row => ({ text: row.viewport, tone: 'accent' }) },
                { title: 'STAGE', value: row => row.name },
            ],
            options,
        );
    }

    note(message: string): void {
        this.view.note(message);
    }
    artifact(label: string, path: string): void {
        this.view.artifact(label, path);
    }
    close(): void {
        this.view.close();
    }

    async stage<T>(
        name: string,
        task: (progress: StageProgress) => Promise<T>,
        options: StageOptions = {},
    ): Promise<T> {
        const start = this.now();
        const viewport = options.viewport === undefined ? '—' : `${options.viewport}px`;
        const elapsed = () => `${((this.now() - start) / 1000).toFixed(1)}s`;
        let detail = '';
        let path: string | undefined;
        let detailStarted = start;
        const refresh = () =>
            this.view.update(
                name,
                { text: elapsed(), tone: 'muted' },
                ...(options.viewport === undefined ? [] : [{ text: viewport, tone: 'accent' } as const]),
                ...(detail ? [detail] : []),
                ...(path
                    ? [{ text: `${((this.now() - detailStarted) / 1000).toFixed(1)}s`, tone: 'muted' } as const]
                    : []),
                ...(path ? [{ text: path, tone: 'path' } as const] : []),
            );
        refresh();
        const timer = setInterval(refresh, this.view.interactive ? 100 : this.heartbeatMs);
        timer.unref();
        let status: Result['status'] = 'DONE';
        try {
            return await task({
                update: (value, currentPath, elapsedSeconds) => {
                    const changed = value !== detail || currentPath !== path;
                    if (Number.isFinite(elapsedSeconds) && elapsedSeconds !== undefined && elapsedSeconds >= 0) {
                        const reportedStart = this.now() - elapsedSeconds * 1000;
                        detailStarted = changed ? reportedStart : Math.min(detailStarted, reportedStart);
                    } else if (changed) detailStarted = this.now();
                    if (!changed) return;
                    detail = value;
                    path = currentPath;
                    refresh();
                },
            });
        } catch (error) {
            status = error instanceof ProbeCancelled ? 'CANCEL' : 'FAILED';
            throw error;
        } finally {
            clearInterval(timer);
            this.view.append({ status, time: elapsed(), viewport, name });
        }
    }
}
