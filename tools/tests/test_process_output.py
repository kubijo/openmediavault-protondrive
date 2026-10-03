"""Exercise live subprocess output without booting a VM or requiring a terminal."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from process_output import ProcessOutput, Transcript
from rich.console import Console

FIXTURE = Path(__file__).parent / 'fixtures/process_output.py'


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ProcessOutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.log = self.root / 'tests.log'
        self.buffer = io.StringIO()
        self.output = ProcessOutput(in_clanker=True)
        self.output.console = Console(file=self.buffer, color_system=None, width=120)

    def command(self, mode, *args):
        return [sys.executable, '-u', str(FIXTURE), mode, *map(str, args)]

    def test_streams_before_exit_preserving_stderr_unicode_and_partial_lines(self):
        marker = self.root / 'output-seen'
        original = self.output.console.print

        def capture(text, **kwargs):
            original(text, **kwargs)
            if '[red]literal' in str(text):
                marker.touch()

        with patch.object(self.output.console, 'print', side_effect=capture):
            self.output.run(self.command('stream', marker), self.log, timeout=10)
        expected = '[red]literal\nstderr\n€\nfinal without newline'
        self.assertEqual(self.log.read_text(), expected)
        self.assertEqual(self.buffer.getvalue(), expected)

    def test_failure_retains_diagnostics_and_exit_code(self):
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.output.run(self.command('failure'), self.log, timeout=10)
        self.assertEqual(raised.exception.returncode, 7)
        self.assertEqual(self.log.read_text(), 'diagnostic before failure\n')
        self.assertEqual(self.buffer.getvalue(), self.log.read_text())

    def test_timeout_stops_process_and_keeps_output(self):
        pid_file = self.root / 'pid'
        with self.assertRaises(subprocess.TimeoutExpired):
            self.output.run(self.command('timeout', pid_file), self.log, timeout=1)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
        self.assertEqual(self.log.read_text(), 'ready\n')
        self.assertEqual(self.buffer.getvalue(), 'ready\n')

    def test_child_has_plain_output_and_closed_stdin(self):
        self.output.run(self.command('environment'), self.log, timeout=10)
        self.assertEqual(self.buffer.getvalue(), 'plain child with stdin closed\n')

    def test_split_ansi_progress_and_crlf_produce_clean_log(self):
        log = io.StringIO()
        transcript = Transcript(self.output.console, log)
        for part in ('\x1b[3', '2m€ 10%\r', '€ 100%\x1b[0m\r', '\n', 'next\r\r\n', 'tail'):
            transcript.feed(part)
        transcript.finish()
        self.assertEqual(log.getvalue(), '€ 100%\nnext\ntail')
        self.assertEqual(self.buffer.getvalue(), log.getvalue())

    def test_human_progress_saves_clean_log_without_alternate_screen(self):
        buffer = Terminal()
        with patch.dict(os.environ, {'TERM': 'xterm'}, clear=True), contextlib.redirect_stdout(buffer):
            output = ProcessOutput()
            with output.stage('Coloured guest'):
                output.run(self.command('colour'), self.log, timeout=10)
        self.assertEqual(self.log.read_text(), 'complete\n')
        self.assertNotIn('\x1b[?1049', buffer.getvalue())

    def test_guest_styles_do_not_change_diagnostics_or_logs(self):
        buffer = io.StringIO()
        log = io.StringIO()
        console = Console(file=buffer, force_terminal=True, color_system='standard')
        transcript = Transcript(console, log)
        messages = [
            'RUN command',
            'PASS: complete',
            'ERROR: failure',
            '          ID: configure_monit_service',
            '      Result: True',
            '      Result: False',
            'Summary for protondrive-test',
            'Succeeded: 16 (changed=10)',
            'Failed:     0',
            'Failed:     2',
        ]
        for message in messages:
            transcript.feed(message + '\n')
            transcript.feed(f'\x1b[35m{message}\x1b[0m\n')
        transcript.finish()
        rendered = buffer.getvalue().splitlines()
        logged = log.getvalue().splitlines()
        self.assertNotIn('\x1b', log.getvalue())
        for index, message in enumerate(messages):
            self.assertEqual(rendered[index * 2], rendered[index * 2 + 1])
            self.assertEqual(logged[index * 2 : index * 2 + 2], [message, message])

    def test_interruption_stops_child_and_flushes_log(self):
        pid_file = self.root / 'pid'
        original = Transcript.feed

        def interrupt(transcript, text):
            original(transcript, text)
            if 'ready' in text:
                raise KeyboardInterrupt

        with patch.object(Transcript, 'feed', interrupt), self.assertRaises(KeyboardInterrupt):
            self.output.run(self.command('timeout', pid_file), self.log, timeout=10)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
        self.assertEqual(self.log.read_text(), 'ready\n')

    def test_failed_stage_does_not_report_done(self):
        with self.assertRaisesRegex(RuntimeError, 'probe'), self.output.stage('Guest tests'):
            raise RuntimeError('probe')
        self.assertIn('FAILED: Guest tests', self.buffer.getvalue())
        self.assertNotIn('DONE:', self.buffer.getvalue())

    def test_agent_pipe_and_color_controls_disable_animation(self):
        for environment, flags, terminal in (
            ({'CODEX_THREAD_ID': ''}, {}, True),
            ({'TERM': 'dumb', 'FORCE_COLOR': '1'}, {}, True),
            ({'NO_COLOR': '', 'FORCE_COLOR': '1'}, {}, True),
            ({'FORCE_COLOR': '0'}, {}, True),
            ({}, {'in_clanker': True}, True),
            ({}, {'no_color': True}, True),
            ({}, {}, False),
        ):
            with self.subTest(environment=environment, flags=flags, terminal=terminal):
                buffer = Terminal() if terminal else io.StringIO()
                with patch.dict(os.environ, environment, clear=True), contextlib.redirect_stdout(buffer):
                    output = ProcessOutput(**flags)
                    self.assertFalse(output.animate)
                    with output.stage('Probe'):
                        output.console.out('retained output')
                self.assertNotIn('\x1b', buffer.getvalue())
                self.assertIn('DONE: Probe', buffer.getvalue())

    def test_human_spinner_keeps_transcript_outside_alternate_screen(self):
        buffer = Terminal()
        with patch.dict(os.environ, {'TERM': 'xterm'}, clear=True), contextlib.redirect_stdout(buffer):
            output = ProcessOutput()
            self.assertTrue(output.animate)
            with output.stage('Probe'):
                output.console.out('retained output')
        self.assertIn('retained output', buffer.getvalue())
        self.assertNotIn('\x1b[?1049', buffer.getvalue())
