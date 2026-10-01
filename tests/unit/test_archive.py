import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import configuration

from protondrive.archive import archive, check_space, estimate
from protondrive.common import BackupError


class ArchiveTests(unittest.TestCase):
    def test_low_space(self):
        with (
            patch('shutil.disk_usage', return_value=shutil._ntuple_diskusage(100, 95, 5)),
            self.assertRaises(BackupError),
        ):
            check_space('/', 4, 2)

    @unittest.skipUnless(shutil.which('zstd') and shutil.which('tar'), 'tar/zstd required')
    def test_restore_metadata_and_exclusions(self):
        self.roundtrip(0o750)

    @unittest.skipIf(
        bool(os.environ.get('PROTON_HERMETIC_TESTS')), 'Nix sandbox forbids setgid; exercised by just test'
    )
    @unittest.skipUnless(shutil.which('zstd') and shutil.which('tar'), 'tar/zstd required')
    def test_restore_setgid(self):
        self.roundtrip(0o2750)

    @unittest.skipIf(
        bool(os.environ.get('PROTON_HERMETIC_TESTS')), 'Nix sandbox has no user xattrs; exercised by just test'
    )
    @unittest.skipUnless(shutil.which('zstd') and shutil.which('tar'), 'tar/zstd required')
    def test_restore_extended_metadata(self):
        self.roundtrip(0o750, extended=True)

    @unittest.skipUnless(os.getuid() == 0, 'Numeric ownership is exercised as root in Debian guests')
    def test_restore_numeric_owner(self):
        self.roundtrip(0o2750, owner=(1234, 2345))

    def roundtrip(self, mode, extended=False, owner=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            if owner:
                os.chown(source, *owner)
            source.chmod(mode)
            (source / 'file').write_text('database content\n')
            (source / 'link').symlink_to('file')
            (source / 'cache').mkdir()
            (source / 'cache/ignored').write_text('cache')
            if extended:
                os.setxattr(source / 'file', 'user.backup-test', b'preserved')
            if extended and shutil.which('setfacl'):
                subprocess.run(['setfacl', '-m', f'u:{os.getuid()}:r--', str(source / 'file')], check=True)
            _, item = configuration()
            item.update(paths=str(source), excludes='cache')
            self.assertGreater(estimate(item), 0)
            partial = root / 'backup.tar.zst.partial'
            archive(item, partial, 0)
            restored = root / 'restored'
            restored.mkdir()
            subprocess.run(
                [
                    'tar',
                    '--extract',
                    '--zstd',
                    '--numeric-owner',
                    '--same-permissions',
                    '--acls',
                    '--xattrs',
                    '--xattrs-include=*',
                    '--file',
                    str(partial),
                    '--directory',
                    str(restored),
                ],
                check=True,
            )
            result = restored / str(source).lstrip('/')
            self.assertEqual((result / 'file').read_text(), 'database content\n')
            if extended:
                self.assertEqual(os.getxattr(result / 'file', 'user.backup-test'), b'preserved')
            self.assertEqual(stat.S_IMODE(result.stat().st_mode), mode)
            if owner:
                self.assertEqual((result.stat().st_uid, result.stat().st_gid), owner)
            self.assertTrue((result / 'link').is_symlink())
            self.assertFalse((result / 'cache').exists())
            if extended and shutil.which('getfacl'):
                self.assertIn(
                    f'user:{os.getuid()}:r--',
                    subprocess.check_output(['getfacl', '-n', str(result / 'file')], text=True),
                )

    @unittest.skipUnless(shutil.which('zstd') and shutil.which('tar'), 'tar/zstd required')
    def test_tar_failure_removes_partial(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, item = configuration()
            item['paths'] = temporary + '/does-not-exist'
            partial = Path(temporary) / 'failed.partial'
            with self.assertRaises(BackupError):
                archive(item, partial, 0)
            self.assertFalse(partial.exists())
