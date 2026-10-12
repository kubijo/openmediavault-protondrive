export class ProbeCancelled extends Error {
    readonly exitCode: number;

    constructor(signal: 'SIGINT' | 'SIGTERM' | 'SIGHUP') {
        super(`Cancelled by ${signal}`);
        this.exitCode = { SIGINT: 130, SIGTERM: 143, SIGHUP: 129 }[signal];
    }
}

export class Cancellation {
    private readonly controller = new AbortController();
    readonly signal = this.controller.signal;
    private readonly interrupt = () => this.cancel('SIGINT');
    private readonly terminate = () => this.cancel('SIGTERM');
    private readonly hangup = () => this.cancel('SIGHUP');
    private readonly notify: (message: string) => void;

    constructor(notify: (message: string) => void) {
        this.notify = notify;
        process.on('SIGINT', this.interrupt);
        process.on('SIGTERM', this.terminate);
        process.on('SIGHUP', this.hangup);
    }

    private cancel(signal: 'SIGINT' | 'SIGTERM' | 'SIGHUP'): void {
        if (this.signal.aborted) return;
        this.notify('Cancellation requested; stopping the current operation and saving artifacts…');
        this.controller.abort(new ProbeCancelled(signal));
    }

    dispose(): void {
        process.off('SIGINT', this.interrupt);
        process.off('SIGTERM', this.terminate);
        process.off('SIGHUP', this.hangup);
    }
}
