import { createInterface } from 'node:readline';
import type { Readable } from 'node:stream';

export type GuestActivity = { phase: string; file: string };

/** Drain events alongside the command; command failure takes precedence over output errors. */
export async function collectGuestOutput(
    child: PromiseLike<unknown> & { stdout: Readable },
    onActivity: (activity: GuestActivity) => void,
): Promise<Record<string, unknown>> {
    const output = new GuestOutput(onActivity);
    const reader = createInterface({ input: child.stdout });
    const drained = Promise.withResolvers<void>();
    const inputError = (error: Error) => {
        drained.reject(error);
        reader.close();
    };
    child.stdout.on('error', inputError);
    reader.on('error', inputError);
    reader.on('line', line => output.accept(line));
    reader.once('close', () => drained.resolve());
    try {
        const [command, stream] = await Promise.allSettled([child, drained.promise]);
        if (command.status === 'rejected') throw command.reason;
        if (stream.status === 'rejected') throw stream.reason;
        return output.result();
    } finally {
        reader.close();
        child.stdout.off('error', inputError);
        reader.off('error', inputError);
    }
}

/** Line-delimited guest events followed by exactly one result object. */
export class GuestOutput {
    readonly #onActivity: (activity: GuestActivity) => void;
    #result: Record<string, unknown> | undefined;
    #error: Error | undefined;

    constructor(onActivity: (activity: GuestActivity) => void) {
        this.#onActivity = onActivity;
    }

    accept(line: string): void {
        // Keep draining stdout after malformed input so the child cannot block.
        if (this.#error) return;
        try {
            const value: unknown = JSON.parse(line);
            if (!value || typeof value !== 'object' || Array.isArray(value) || this.#result)
                throw new Error('Invalid guest event or result');
            if ('event' in value) {
                if (
                    value.event !== 'activity' ||
                    !('phase' in value) ||
                    typeof value.phase !== 'string' ||
                    !('file' in value) ||
                    typeof value.file !== 'string'
                )
                    throw new Error('Invalid guest activity');
                this.#onActivity({ phase: value.phase, file: value.file });
            } else this.#result = value as Record<string, unknown>;
        } catch (error) {
            this.#error = error instanceof Error ? error : new Error(String(error));
        }
    }

    result(): Record<string, unknown> {
        if (this.#error) throw this.#error;
        if (!this.#result) throw new Error('Missing guest result');
        return this.#result;
    }
}
