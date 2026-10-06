"""Deployment must confirm monitoring, not merely submission of a Monit action."""

import subprocess
import unittest
from unittest.mock import patch

from protondrive import monitoring
from protondrive.common import BackupError


def result(output: str = '', code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(['monit'], code, output, '')


def status(*states: str) -> str:
    return 'Monit 5.33.0 uptime: 1m\n\n' + ''.join(
        f"Program 'protondrive-{name}'\n  monitoring status {state}\n\n"
        for name, state in zip(monitoring.CHECKS, states, strict=False)
    )


class MonitoringTests(unittest.TestCase):
    def test_waits_through_reload_and_pending_actions_for_all_four_checks(self) -> None:
        ready = status(*['Monitored'] * 4)
        pending = status('Not monitored', 'Monitored', 'Monitored', 'Monitored')
        with (
            patch.object(
                monitoring.subprocess,
                'run',
                side_effect=[result(), result(code=1), result(), result(pending), result(), result(ready)],
            ) as run,
            patch.object(monitoring.time, 'sleep') as sleep,
        ):
            monitoring.set_monitoring(True)
        self.assertEqual(run.call_count, 6)
        self.assertEqual(sleep.call_count, 2)

    def test_missing_checks_cannot_report_success_and_wait_is_bounded(self) -> None:
        with (
            patch.object(monitoring.subprocess, 'run', side_effect=[result(), result(status('Monitored'))]),
            patch.object(monitoring.time, 'monotonic', side_effect=[0, 90]),
            self.assertRaisesRegex(BackupError, 'within 90 seconds'),
        ):
            monitoring.set_monitoring(True)

    def test_shutdown_waits_until_every_check_is_unmonitored(self) -> None:
        with (
            patch.object(
                monitoring.subprocess,
                'run',
                side_effect=[
                    result(),
                    result(status(*['Monitored'] * 4)),
                    result(),
                    result(status(*['Not monitored'] * 4)),
                ],
            ) as run,
            patch.object(monitoring.time, 'sleep'),
        ):
            monitoring.set_monitoring(False)
        self.assertEqual(run.call_args_list[0].args[0], ['monit', '-g', 'protondrive', 'unmonitor'])

    def test_absent_or_partially_registered_checks_can_be_unmonitored(self) -> None:
        for output in (status(), status('Not monitored'), status('Not monitored', 'Not monitored')):
            with (
                self.subTest(output=output),
                patch.object(monitoring.subprocess, 'run', side_effect=[result(code=1), result(output)]) as run,
            ):
                monitoring.set_monitoring(False)
                self.assertEqual(run.call_args_list[1].args[0], ['monit', '-B', 'status'])

    def test_unavailable_daemon_is_not_mistaken_for_absent_checks(self) -> None:
        with (
            patch.object(monitoring.subprocess, 'run', return_value=result(code=1)),
            patch.object(monitoring.time, 'monotonic', side_effect=[0, 90]),
            self.assertRaisesRegex(BackupError, 'within 90 seconds'),
        ):
            monitoring.set_monitoring(False)

    def test_malformed_status_cannot_report_success(self) -> None:
        for output in ('', 'unrecognized output', "Monit 5.33\nProgram 'protondrive-backup'\n"):
            with (
                self.subTest(output=output),
                patch.object(monitoring.subprocess, 'run', side_effect=[result(), result(output)]),
                self.assertRaises(BackupError),
            ):
                monitoring.set_monitoring(False)
