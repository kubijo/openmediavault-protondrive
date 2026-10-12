import { stripVTControlCharacters } from 'node:util';
import Table from 'cli-table3';
import { createLogUpdate } from 'log-update';
import { chalk } from 'zx';

export type Tone = 'muted' | 'accent' | 'path' | 'success' | 'warning' | 'error';
export type Cell = string | { text: string; tone: Tone };
export type Column<Row> = {
    title: string;
    /** Content width in terminal cells. Omit on the column that fills the remaining space. */
    width?: number;
    align?: 'left' | 'right';
    value: (row: Row) => Cell;
};
export type TerminalStream = NodeJS.WritableStream & { isTTY?: boolean; columns?: number; rows?: number };
export type TerminalOptions = { stream?: TerminalStream; interactive?: boolean; color?: boolean };

const plain = (cell: Cell): string =>
    stripVTControlCharacters(typeof cell === 'string' ? cell : cell.text)
        .replace(/\s+/g, ' ')
        .trim();

/**
 * A growing results table with a separate live line. No task or signal ownership.
 * append(row) completes a row; update(...cells) replaces the live line.
 * note/artifact commit the current table before printing permanent output.
 * close() preserves all rows in scrollback, including those above the viewport.
 */
export class LiveTable<Row> {
    readonly interactive: boolean;
    readonly #columns: readonly Column<Row>[];
    readonly #stream: TerminalStream;
    readonly #colored: boolean;
    readonly #render: ReturnType<typeof createLogUpdate>;
    #widths: number[] | undefined;
    #rowCount = 0;
    #bottom = '';
    #live: Cell[] = [];
    #closed = false;
    readonly #resize = () => {
        const live = this.#live;
        this.#commit();
        this.#live = live;
        if (live.length) this.#paint();
    };

    constructor(columns: readonly Column<Row>[], options: TerminalOptions = {}) {
        if (!columns.length) throw new Error('A live table needs at least one column');
        this.#columns = columns;
        this.#stream = options.stream ?? process.stderr;
        this.interactive =
            options.interactive ?? (Boolean(this.#stream.isTTY) && !process.env.CI && process.env.TERM !== 'dumb');
        this.#colored = this.interactive && process.env.NO_COLOR === undefined && (options.color ?? true);
        if (this.#colored && !chalk.level) chalk.level = process.env.TERM?.includes('256color') ? 2 : 1;
        this.#render = createLogUpdate(this.#stream, { showCursor: true });
        if (this.interactive) this.#stream.on('resize', this.#resize);
    }

    append(row: Row): void {
        this.#assertOpen();
        this.#live = [];
        if (this.interactive) {
            this.#open();
            const table = this.#table(false);
            table.push(this.#columns.map(column => this.#cell(column.value(row))));
            let line = table.toString().split('\n').slice(1, -1).join('\n');
            if (this.#rowCount % 2 === 1)
                line = this.#style(line, chalk.level >= 2 ? chalk.bgAnsi256(235) : chalk.bgBlackBright);
            this.#render.persist(line);
            this.#rowCount++;
            this.#paint();
        } else this.#write(this.#columns.map(column => plain(column.value(row))).join('  '));
    }

    update(...cells: Cell[]): void {
        this.#assertOpen();
        this.#live = cells;
        if (this.interactive) this.#paint();
        else if (cells.length) this.#write(`RUN  ${cells.map(plain).join('  ')}`);
    }

    note(message: string): void {
        this.#assertOpen();
        this.#commit();
        this.#write(stripVTControlCharacters(message));
    }

    artifact(label: string, path: string): void {
        this.#assertOpen();
        this.#commit();
        this.#write(
            `\n${this.#style(plain(label), chalk.bold)}\n${this.#style(stripVTControlCharacters(path), chalk.blue)}\n`,
        );
    }

    close(): void {
        if (this.#closed) return;
        this.#commit();
        this.#render.done();
        this.#stream.off('resize', this.#resize);
        this.#closed = true;
    }

    #assertOpen(): void {
        if (this.#closed) throw new Error('The live table is closed');
    }

    #write(text: string): void {
        this.#stream.write(`${text}\n`);
    }
    #style(text: string, style: typeof chalk): string {
        return this.#colored ? style(text) : text;
    }
    #cell(cell: Cell): string {
        if (typeof cell === 'string') return plain(cell);
        const styles = {
            muted: chalk.dim,
            accent: chalk.cyan,
            path: chalk.blue,
            success: chalk.green,
            warning: chalk.yellow,
            error: chalk.red,
        };
        return this.#style(plain(cell), styles[cell.tone]);
    }

    #open(): void {
        if (this.#widths) return;
        const available = Math.max(20, Math.min(this.#stream.columns || 80, 120) - 1);
        const widths = this.#columns.map(column => (column.width === undefined ? 3 : column.width + 2));
        const flexible = Math.max(
            0,
            this.#columns.findIndex(column => column.width === undefined),
        );
        const total = () => widths.reduce((sum, width) => sum + width, this.#columns.length + 1);
        widths[flexible] = (widths[flexible] ?? 3) + Math.max(0, available - total());
        while (total() > available && widths.some(width => width > 3)) {
            const widest = widths.indexOf(Math.max(...widths));
            widths[widest] = (widths[widest] ?? 3) - 1;
        }
        this.#widths = widths;
        this.#rowCount = 0;
        const table = this.#table(true);
        table.push(this.#columns.map(() => ''));
        const lines = table.toString().split('\n');
        this.#bottom = lines.at(-1) ?? '';
        this.#render.persist(lines.slice(0, 3).join('\n'));
    }

    #table(head: boolean): Table.Table {
        return new Table({
            head: head ? this.#columns.map(column => column.title) : [],
            colWidths: this.#widths,
            colAligns: this.#columns.map(column => column.align ?? 'left'),
            rowHeights: head ? [1, 1] : [1],
            style: { head: [], border: [], compact: true },
        });
    }

    #paint(): void {
        if (this.#closed) return;
        this.#open();
        const live = this.#live.length
            ? `\n\n${this.#style('›', chalk.cyan)} ${this.#live.map(cell => this.#cell(cell)).join('  ')}`
            : '';
        this.#render(`${this.#bottom}${live}`);
    }

    #commit(): void {
        if (this.interactive) {
            if (this.#widths) this.#render.persist(`${this.#bottom}\n`);
        }
        this.#widths = undefined;
        this.#live = [];
    }
}
