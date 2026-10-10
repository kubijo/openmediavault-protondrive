import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

export type GuestLease = {
    assertHeld(): void;
    release(): Promise<void>;
};

/** An SSH lease may be deliberately released before reboot, but unexpected loss aborts the flow. */
export async function acquireGuestLease(
    command: string,
    args: string[],
    signal: AbortSignal,
    onLost: () => void,
): Promise<GuestLease> {
    signal.throwIfAborted();
    const child = spawn(command, args, { stdio: ['pipe', 'pipe', 'pipe'], detached: true });
    const ready = Promise.withResolvers<void>();
    let released = false;
    let lost = false;
    let diagnostic = '';
    const abort = () => ready.reject(signal.reason);
    signal.addEventListener('abort', abort, { once: true });
    child.stderr.on('data', (data: Buffer) => {
        diagnostic = (diagnostic + data.toString()).slice(-1024 * 1024);
    });
    const lines = createInterface({ input: child.stdout });
    lines.on('line', line => {
        if (line === '{"locked": true}') ready.resolve();
    });
    child.once('error', error => ready.reject(error));
    child.stdin.on('error', error => ready.reject(error));
    const closed = new Promise<void>(resolve => {
        child.once('close', () => {
            lost = true;
            ready.reject(new Error(diagnostic || 'VM flow lock connection closed'));
            if (!released) onLost();
            resolve();
        });
    });
    function stopGroup(signal: NodeJS.Signals): void {
        if (child.pid === undefined) return;
        try {
            process.kill(-child.pid, signal);
        } catch (error) {
            if (!(error instanceof Error && 'code' in error && error.code === 'ESRCH')) throw error;
        }
    }
    async function release(): Promise<void> {
        released = true;
        child.stdin.end();
        // The wrapper can exit before SSH closes its inherited pipes. Terminate
        // the owned process group and bound cleanup even if it ignores SIGTERM.
        const stop = setTimeout(() => stopGroup('SIGTERM'), 5000);
        const kill = setTimeout(() => stopGroup('SIGKILL'), 6000);
        try {
            await closed;
        } finally {
            clearTimeout(stop);
            clearTimeout(kill);
            lines.close();
        }
    }
    const timeout = setTimeout(() => ready.reject(new Error('Timed out acquiring the VM flow lock')), 30_000);
    try {
        await ready.promise;
        signal.throwIfAborted();
    } catch (error) {
        await release();
        throw error;
    } finally {
        clearTimeout(timeout);
        signal.removeEventListener('abort', abort);
    }
    return {
        assertHeld() {
            if (released || lost) throw new Error('VM flow lock connection was lost; recovery is required');
        },
        release,
    };
}
