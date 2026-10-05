"""Stream subprocess logs into terminal scrollback with optional Rich stage indicators."""

import os
import subprocess
import tempfile
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager, nullcontext
from encodings.utf_8 import IncrementalDecoder
from pathlib import Path
from typing import TextIO

from rich.ansi import AnsiDecoder
from rich.console import Console
from rich.live import Live
from rich.text import Text

from console import child_environment, install_traceback, live_output, new_console, style_diagnostic


class Transcript:
    """Decode line-oriented ANSI output; replace CR progress rows without cursor emulation."""

    def __init__(self, console: Console, log: TextIO, live: Live | None = None) -> None:
        self.console, self.log, self.live = console, log, live
        self.decoder = AnsiDecoder()
        self.line = ''
        self.carriage_return = False

    def commit(self, *, newline: bool = True) -> None:
        text = self.decoder.decode_line(self.line)
        ending = '\n' if newline else ''
        self.log.write(text.plain + ending)
        self.log.flush()
        self.console.print(style_diagnostic(text), end=ending, soft_wrap=True)
        self.line = ''
        self.carriage_return = False

    def feed(self, text: str) -> None:
        for character in text:
            if self.carriage_return and character not in '\r\n':
                # Keep ANSI style transitions from the replaced progress row.
                self.decoder.decode_line(self.line)
                self.line = ''
                self.carriage_return = False
            if character == '\r':
                self.carriage_return = True
            elif character == '\n':
                self.commit()
            else:
                self.line += character
        if self.live is not None:
            preview = AnsiDecoder()
            preview.style = self.decoder.style
            self.live.update(style_diagnostic(preview.decode_line(self.line)))

    def finish(self) -> None:
        if self.live is not None:
            self.live.update(Text())
        if self.line:
            self.commit(newline=False)


class ProcessOutput:
    def __init__(self, *, no_color: bool = False, in_clanker: bool = False) -> None:
        install_traceback(no_color=no_color, in_clanker=in_clanker)
        self.console = new_console(no_color=no_color, in_clanker=in_clanker)
        self.animate = live_output(self.console, no_color=no_color, in_clanker=in_clanker)

    @contextmanager
    def stage(self, title: str) -> Generator[None, None, None]:
        self.console.print(Text(f'\n{title}', style='bold cyan'))
        started = time.monotonic()
        indicator = self.console.status(Text(title)) if self.animate else nullcontext()
        try:
            with indicator:
                yield
        except BaseException:
            self.console.print(
                Text.assemble(
                    ('FAILED: ', 'bold red'),
                    title,
                    (f' ({time.monotonic() - started:.1f}s)', 'dim'),
                )
            )
            raise
        self.console.print(
            Text.assemble(
                ('DONE: ', 'green'),
                title,
                (f' ({time.monotonic() - started:.1f}s)', 'dim'),
            )
        )

    def run(self, command: Sequence[str], log_path: Path, *, timeout: float) -> None:
        """Render guest colour/progress in a human TTY and save a clean text transcript."""
        decoder = IncrementalDecoder(errors='replace')
        progress = Live(console=self.console, transient=True, refresh_per_second=10) if self.animate else nullcontext()
        with tempfile.TemporaryFile() as raw, log_path.open('w', encoding='utf-8') as log, progress as live:
            transcript = Transcript(self.console, log, live)
            offset = 0

            def relay(*, final: bool = False) -> None:
                nonlocal offset
                while data := os.pread(raw.fileno(), 65536, offset):
                    offset += len(data)
                    transcript.feed(decoder.decode(data))
                    if not final:
                        break
                if final:
                    transcript.feed(decoder.decode(b'', final=True))
                    transcript.finish()

            with subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=raw,
                stderr=subprocess.STDOUT,
                env=child_environment(terminal=self.animate),
            ) as process:
                deadline = time.monotonic() + timeout
                try:
                    while process.poll() is None:
                        relay()
                        if time.monotonic() >= deadline:
                            raise subprocess.TimeoutExpired(command, timeout)
                        time.sleep(0.1)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    relay(final=True)
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, command)
