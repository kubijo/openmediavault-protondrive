/** Small subprocess peer for testing the lease transport lifecycle. */
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

if (process.argv.includes('--with-child')) {
    spawn(process.execPath, [fileURLToPath(import.meta.url), '--stubborn'], {
        stdio: ['ignore', 'inherit', 'inherit'],
    });
}
if (process.argv.includes('--stubborn')) {
    process.on('SIGTERM', () => undefined);
    setInterval(() => undefined, 1000);
}
process.stdout.write('{"locked": true}\n');
if (process.argv.includes('--disconnect')) {
    setTimeout(() => process.exit(0), 50);
} else {
    process.stdin.resume();
    process.stdin.on('end', () => {
        if (!process.argv.includes('--stubborn')) process.exit(0);
    });
}
