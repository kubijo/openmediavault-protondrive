"""Explicit scopes must fail closed and never stop unrelated containers."""

import json
import tempfile
import unittest
from pathlib import Path

from helpers import configuration
from protondrive.common import BackupError
from protondrive.containers import selection
from protondrive.recovery import Recovery

A, B, C = 'a' * 64, 'b' * 64, 'c' * 64


class ContainerScopeTests(unittest.TestCase):
    def test_missing_selection_fails_before_stop(self) -> None:
        _, item = configuration()
        item['containerids'] = C
        calls: list[tuple[str, ...]] = []

        def command(*args: str) -> str:
            calls.append(args)
            return A

        with self.assertRaisesRegex(BackupError, 'no longer exists'):
            selection(item, command)
        self.assertEqual(calls, [('ps', '--all', '--quiet', '--no-trunc')])

    def test_project_resolves_only_matching_containers(self) -> None:
        _, item = configuration()
        item['composeprojects'] = 'selected'

        def command(*args: str) -> str:
            if args[0] == 'ps':
                return f'{A}\n{B}'
            return json.dumps({'com.docker.compose.project': 'selected' if args[-1] == A else 'unrelated'})

        self.assertEqual(selection(item, command), (A,))
        item['composeprojects'] = 'missing'
        with self.assertRaisesRegex(BackupError, 'has no containers'):
            selection(item, command)

    def test_stop_and_recovery_preserve_unselected_and_stopped(self) -> None:
        running = {A: True, B: True, C: False}
        calls: list[tuple[str, ...]] = []

        def command(*args: str) -> str:
            calls.append(args)
            if args[0] == 'ps':
                return '\n'.join(cid for cid, active in running.items() if active)
            if args[0] == 'inspect':
                return str(running[args[-1]]).lower()
            if args[0] in ('stop', 'start'):
                running[args[-1]] = args[0] == 'start'
            return ''

        with tempfile.TemporaryDirectory() as directory:
            recovery = Recovery(Path(directory), command)
            recovery.stop(1, (A, C))
            self.assertEqual(running, {A: False, B: True, C: False})
            Recovery(Path(directory), command).restore()
        self.assertEqual(running, {A: True, B: True, C: False})
        self.assertEqual([args[-1] for args in calls if args[0] == 'stop'], [A])
        self.assertEqual([args[-1] for args in calls if args[0] == 'start'], [A])
