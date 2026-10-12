import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chalk } from 'zx';
import { LiveTable } from '../src/terminal/table.ts';
import { TerminalCapture } from './fixtures/terminal.ts';

type Row = { status: string; time: string; name: string };
const columns = [
    { title: 'STATUS', width: 6, value: (row: Row) => row.status },
    { title: 'TIME', width: 7, align: 'right' as const, value: (row: Row) => row.time },
    { title: 'STAGE', value: (row: Row) => row.name },
];

test('bordered zebra rows align; live updates stay below the table and append only on completion', async context => {
    const { NO_COLOR: _noColor, ...env } = process.env;
    context.mock.property(process, 'env', { ...env, TERM: 'xterm-256color' });
    const level = chalk.level ?? 0;
    context.after(() => {
        chalk.level = level;
    });
    const output = new TerminalCapture();
    const view = new LiveTable(columns, { stream: output.stream, interactive: true });
    context.after(() => {
        view.close();
        output.dispose();
    });
    view.append({ status: 'DONE', time: '0.1s', name: 'Launch' });
    view.append({ status: 'DONE', time: '12.3s', name: 'Login' });
    view.update('Backup', { text: '420px', tone: 'accent' }, 'uploading');
    let lines = await output.lines();
    const border = lines.findIndex(line => line.startsWith('└'));
    assert.ok(border > 0);
    assert.ok(lines.findIndex(line => line.includes('› Backup')) > border);
    const body = lines.filter(line => line.startsWith('│'));
    assert.equal(body.length, 3);
    const positions = (line: string) => [...line.matchAll(/│/g)].map(match => match.index);
    for (const line of body) assert.deepEqual(positions(line), positions(body[0] ?? ''));
    const first = lines.findIndex(line => line.includes('Launch'));
    const second = lines.findIndex(line => line.includes('Login'));
    assert.equal(output.terminal.buffer.active.getLine(first)?.getCell(1)?.getBgColorMode(), 0);
    assert.notEqual(output.terminal.buffer.active.getLine(second)?.getCell(1)?.getBgColorMode(), 0);
    output.writes.length = 0;
    view.update('Backup', 'retention');
    lines = await output.lines();
    assert.ok(lines.some(line => line.includes('› Backup  retention')));
    assert.ok(!output.writes.join('').includes('Launch'), 'live updates must not repaint completed rows');
    view.append({ status: 'DONE', time: '162.5s', name: 'Backup' });
    lines = await output.lines();
    assert.equal(lines.filter(line => line.includes('Backup')).length, 1);
    assert.ok(!lines.some(line => line.includes('›')));
});

test('resize and a short terminal preserve every completed row in final scrollback', async context => {
    const output = new TerminalCapture(100, 12);
    const view = new LiveTable(columns, { stream: output.stream, interactive: true, color: false });
    context.after(() => {
        view.close();
        output.dispose();
    });
    for (let index = 0; index < 20; index++) view.append({ status: 'DONE', time: `${index}s`, name: `Stage ${index}` });
    view.update('Working on a long filename /data/备份/archive.tar.zst');
    await output.lines();
    output.resize(50, 10);
    view.append({ status: 'DONE', time: '20s', name: 'After resize' });
    view.close();
    const lines = await output.lines();
    for (let index = 0; index < 20; index++)
        assert.equal(lines.filter(line => line.includes(`Stage ${index} `)).length, 1);
    // Previously committed rows remain in scrollback and reflow with the terminal.
    // The new section uses the new width without replaying those rows.
    const section = lines.slice(lines.findLastIndex(line => line.startsWith('┌')));
    const bordered = section.filter(line => line.startsWith('│'));
    assert.equal(bordered.length, 2);
    assert.ok(bordered.every(line => line.length === 49));
    assert.equal(lines.filter(line => line.includes('After resize')).length, 1);
    assert.equal(output.stream.listenerCount('resize'), 0);
    const count = output.writes.length;
    view.close();
    assert.equal(output.writes.length, count);
});

test('notes and artifact paths remain in history when another table starts; NO_COLOR is honored', async context => {
    context.mock.property(process, 'env', { ...process.env, NO_COLOR: '1' });
    const output = new TerminalCapture();
    const view = new LiveTable(columns, { stream: output.stream, interactive: true });
    context.after(() => {
        view.close();
        output.dispose();
    });
    view.append({ status: 'DONE', time: '1s', name: 'Before' });
    view.note('Recovery needs confirmation');
    view.append({ status: 'DONE', time: '2s', name: 'After' });
    view.artifact('Video', '/some path/video.webm');
    view.append({ status: 'DONE', time: '3s', name: 'Another viewport' });
    view.close();
    const lines = await output.lines();
    for (const text of ['Before', 'Recovery needs confirmation', 'After', '/some path/video.webm', 'Another viewport'])
        assert.equal(lines.filter(line => line.includes(text)).length, 1);
    assert.ok(lines.includes('/some path/video.webm'));
    // biome-ignore lint/suspicious/noControlCharactersInRegex: Assert that NO_COLOR suppresses ANSI styling while cursor controls remain allowed.
    assert.ok(!output.writes.some(chunk => /\u001b\[[\d;]*m/.test(chunk)));
});
