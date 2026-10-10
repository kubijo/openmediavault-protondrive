import { Writable } from 'node:stream';
import xterm from '@xterm/headless';

/** Execute real ANSI output so tests assert cells and cursor placement, not padded strings. */
export class TerminalCapture {
    readonly terminal: xterm.Terminal;
    readonly writes: string[] = [];
    readonly stream: Writable & { isTTY: boolean; columns: number; rows: number };

    constructor(columns = 100, rows = 30) {
        this.terminal = new xterm.Terminal({
            cols: columns,
            rows,
            convertEol: true,
            allowProposedApi: true,
            scrollback: 1000,
        });
        this.stream = Object.assign(
            new Writable({
                write: (chunk: Buffer, _encoding, callback) => {
                    const text = chunk.toString();
                    this.writes.push(text);
                    this.terminal.write(text, callback);
                },
            }),
            { isTTY: true, columns, rows },
        );
    }

    async lines(): Promise<string[]> {
        await new Promise<void>((resolve, reject) =>
            this.stream.write('', error => (error ? reject(error) : resolve())),
        );
        const buffer = this.terminal.buffer.active;
        return Array.from(
            { length: buffer.length },
            (_, index) => buffer.getLine(index)?.translateToString(true) ?? '',
        );
    }

    resize(columns: number, rows: number): void {
        this.terminal.resize(columns, rows);
        this.stream.columns = columns;
        this.stream.rows = rows;
        this.stream.emit('resize');
    }

    dispose(): void {
        this.stream.destroy();
        this.terminal.dispose();
    }
}
