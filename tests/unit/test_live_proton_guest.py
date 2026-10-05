import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.integration.live_proton_guest import download_pair, latest_complete_archive

from protondrive.common import BackupError
from protondrive.models import RemoteEntry


class LiveProtonGuestTests(unittest.TestCase):
    def test_download_reports_each_file_before_starting_and_stops_on_error(self):
        events: list[tuple[str, str]] = []

        def callback(phase: str, name: str) -> None:
            events.append((phase, name))

        def execute(command: list[str], **kwargs: object) -> None:
            self.assertEqual(command[-2], '--remote')
            self.assertEqual(events[-1][1], Path(command[-1]).name)
            if command[-1].endswith('.manifest.json'):
                raise RuntimeError('download failed')

        with (
            patch('tests.integration.live_proton_guest.pwd.getpwnam', return_value=Mock(pw_uid=1, pw_gid=1)),
            patch('tests.integration.live_proton_guest.os.chown'),
            patch.object(Path, 'chmod'),
            patch('tests.integration.live_proton_guest.subprocess.run', side_effect=execute),
            self.assertRaisesRegex(RuntimeError, 'download failed'),
        ):
            download_pair(123, '/managed/set', 'a.tar.zst', Path('/scratch'), callback)
        self.assertEqual(
            events,
            [
                ('Restore archive download', 'a.tar.zst'),
                ('Restore manifest download', 'a.tar.zst.manifest.json'),
            ],
        )

    def test_selects_latest_complete_unambiguous_backup(self):
        listing: list[RemoteEntry] = [
            {'name': 'system-20261004T1000Z.tar.zst', 'type': 'file', 'size': None, 'uid': 'old'},
            {
                'name': 'system-20261004T1000Z.tar.zst.manifest.json',
                'type': 'file',
                'size': None,
                'uid': 'old-manifest',
            },
            {'name': 'system-20261004T1100Z.tar.zst', 'type': 'file', 'size': None, 'uid': 'new'},
            {
                'name': 'system-20261004T1100Z.tar.zst.manifest.json',
                'type': 'file',
                'size': None,
                'uid': 'new-manifest',
            },
            {'name': 'system-20261004T1200Z.tar.zst', 'type': 'file', 'size': None, 'uid': 'incomplete'},
        ]
        self.assertEqual(latest_complete_archive(listing, 'system'), 'system-20261004T1100Z.tar.zst')
        listing.append(
            {'name': 'system-20261004T1100Z.tar.zst.manifest.json', 'type': 'file', 'size': None, 'uid': 'duplicate'}
        )
        with self.assertRaises(BackupError):
            latest_complete_archive(listing, 'system')
