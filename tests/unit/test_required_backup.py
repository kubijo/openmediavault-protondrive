import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from protondrive.common import BackupError
from protondrive.models import RequiredBackup
from protondrive.required_backup import select, verify_archived


class RequiredBackupTests(unittest.TestCase):
    def test_selects_newest_recent_regular_file_and_records_its_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old = directory / 'older.sql.gz'
            recent = directory / 'newer.sql.gz'
            old.write_bytes(b'old backup')
            recent.write_bytes(b'new backup')
            now = time.time_ns()
            os.utime(old, ns=(now - 48 * 3_600_000_000_000, now - 48 * 3_600_000_000_000))
            os.utime(recent, ns=(now - 2 * 3_600_000_000_000, now - 2 * 3_600_000_000_000))
            (directory / 'link.sql.gz').symlink_to(recent)
            requirement = RequiredBackup(name='immich', directory=str(directory), pattern='*.sql.gz', max_age_hours=36)
            selected = select(requirement, now)
            self.assertEqual(selected.path, recent)
            self.assertEqual(selected.size, len(b'new backup'))
            self.assertEqual(selected.record()['kind'], 'required-backup')
            archive = directory / 'capture.tar.zst'
            subprocess.run(
                ['tar', '--create', '--zstd', '--file', str(archive), '--directory', '/', str(recent).lstrip('/')],
                check=True,
            )
            verify_archived(archive, selected)

            recent.write_bytes(b'changed backup')
            subprocess.run(
                ['tar', '--create', '--zstd', '--file', str(archive), '--directory', '/', str(recent).lstrip('/')],
                check=True,
            )
            with self.assertRaisesRegex(BackupError, 'differs'):
                verify_archived(archive, selected)

    def test_stale_or_missing_backup_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            requirement = RequiredBackup(
                name='audiobookshelf', directory=str(directory), pattern='*.audiobookshelf', max_age_hours=24
            )
            with self.assertRaisesRegex(BackupError, 'No backup file'):
                select(requirement)
            backup = directory / 'backup.audiobookshelf'
            backup.write_bytes(b'backup')
            now = time.time_ns()
            os.utime(backup, ns=(now - 30 * 3_600_000_000_000, now - 30 * 3_600_000_000_000))
            with self.assertRaisesRegex(BackupError, 'not recent'):
                select(requirement, now)
