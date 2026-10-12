"""Hostile archives must fail before writing outside a private extraction tree."""

import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protondrive.common import BackupError
from protondrive.operation import Cancelled, OperationControl
from protondrive.restore import extract, inspect, protected_directory, publish, select
from protondrive.restore_journal import OWNER_FILE, Publication, reconcile


def member(name: str, kind: bytes = tarfile.REGTYPE, target: str = '') -> tarfile.TarInfo:
    value = tarfile.TarInfo(name)
    value.type = kind
    value.linkname = target
    value.uid, value.gid = os.geteuid(), os.getegid()
    value.mode = 0o750 if kind == tarfile.DIRTYPE else 0o640
    value.mtime = 1_700_000_000
    return value


def write_archive(path: Path, entries: list[tarfile.TarInfo]) -> None:
    with tarfile.open(path, 'w') as archive:
        for entry in entries:
            entry.size = 7 if entry.isfile() else 0
            archive.addfile(entry, io.BytesIO(b'payload') if entry.isfile() else None)


class RestoreTests(unittest.TestCase):
    def test_reserved_marker_is_refused_during_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'marker.tar'
            write_archive(archive, [member(OWNER_FILE), member('link', tarfile.LNKTYPE, OWNER_FILE)])
            entries = inspect(archive)
            for name in [OWNER_FILE, 'link']:
                with self.subTest(name=name), self.assertRaisesRegex(BackupError, 'ownership marker'):
                    select(entries, [name])

    def test_unsupported_acl_destination_refuses_archived_acls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'acl.tar'
            value = member('file')
            value.pax_headers = {'SCHILY.acl.access': 'user::rw-,group::r--,other::---'}
            write_archive(archive, [value])
            self.assertTrue(inspect(archive)[0].has_acl)
            with (
                patch('protondrive.restore.supports_acl', return_value=False),
                self.assertRaisesRegex(BackupError, 'POSIX ACLs'),
            ):
                extract(archive, ['file'], root / 'output', 0, lambda _: None)
            self.assertEqual(list(root.iterdir()), [archive])

    def test_cancellation_before_publication_removes_only_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'valid.tar'
            write_archive(archive, [member('file')])
            control = OperationControl()
            checkpoints: list[Publication] = []

            def progress(message: str) -> None:
                if message == 'Publishing extracted files':
                    self.assertTrue(control.cancel())

            with self.assertRaises(Cancelled):
                extract(archive, ['file'], root / 'output', 0, progress, control=control, checkpoint=checkpoints.append)
            self.assertEqual(list(root.iterdir()), [archive])
            self.assertEqual([entry.phase for entry in checkpoints], ['preparing', 'extracting'])
            self.assertFalse(reconcile(checkpoints[-1]))

    def test_publication_is_journaled_and_cannot_be_cancelled_after_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'valid.tar'
            write_archive(archive, [member('file')])
            control = OperationControl()
            checkpoints: list[Publication] = []
            extract(
                archive, ['file'], root / 'output', 0, lambda _: None, control=control, checkpoint=checkpoints.append
            )
            self.assertEqual(
                [entry.phase for entry in checkpoints], ['preparing', 'extracting', 'publishing', 'published']
            )
            self.assertFalse(control.cancel())
            # Simulate restart after the rename but before its completion record.
            self.assertTrue(reconcile(checkpoints[-2]))
            self.assertEqual((root / 'output/file').read_bytes(), b'payload')

    def test_restart_refuses_to_clean_a_replaced_staging_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / 'staging'
            staging.mkdir()
            parent_info, info = root.stat(), staging.stat()
            record = Publication(
                str(root),
                parent_info.st_dev,
                parent_info.st_ino,
                'staging',
                'output',
                info.st_dev,
                info.st_ino,
                'extracting',
            )
            staging.rename(root / 'original')
            staging.mkdir()
            (staging / 'keep').write_text('unrelated')
            with self.assertRaisesRegex(BackupError, 'Unidentified'):
                reconcile(record)
            self.assertEqual((staging / 'keep').read_text(), 'unrelated')

    def test_oversized_pax_header_is_refused_before_allocating_its_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'huge.tar'
            header = member('pax', tarfile.XHDTYPE)
            header.size = 128 * 1024 * 1024
            with archive.open('wb') as stream:
                stream.write(header.tobuf())
                stream.truncate(header.size + 512)
            with self.assertRaisesRegex(BackupError, 'metadata block'):
                inspect(archive)

    def test_rejects_traversal_absolute_and_control_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'bad.tar'
            for name in ('../outside', '/outside', 'inside/../../outside', 'line\nbreak', '././path'):
                with self.subTest(name=name):
                    write_archive(archive, [member(name)])
                    with self.assertRaisesRegex(BackupError, 'unsafe path'):
                        inspect(archive)

    def test_duplicate_and_symlink_parent_fail_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'bad.tar'
            for entries in (
                [member('file'), member('./file')],
                [member('dir', tarfile.SYMTYPE, 'target'), member('dir/child')],
                [member('dir/child'), member('dir', tarfile.SYMTYPE, 'target')],
            ):
                write_archive(archive, entries)
                with self.assertRaises(BackupError):
                    inspect(archive)

    def test_links_are_resolved_before_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'links.tar'
            write_archive(
                archive,
                [
                    member('dir', tarfile.DIRTYPE),
                    member('dir/up', tarfile.SYMTYPE, '..'),
                    member('escape', tarfile.SYMTYPE, 'dir/up/../outside'),
                    member('absolute', tarfile.SYMTYPE, '/etc/passwd'),
                    member('cycle', tarfile.SYMTYPE, 'cycle'),
                    member('pipe', tarfile.FIFOTYPE),
                ],
            )
            entries = {entry.name: entry for entry in inspect(archive)}
            self.assertFalse(entries['dir/up'].issue)
            for name in ('escape', 'absolute', 'cycle', 'pipe'):
                self.assertTrue(entries[name].issue)
                with self.assertRaises(BackupError):
                    select(list(entries.values()), [name])

    def test_selection_preserves_links_metadata_and_leaves_unselected_files_out(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, destination = root / 'valid.tar', root / 'extracted'
            write_archive(
                archive,
                [
                    member('data', tarfile.DIRTYPE),
                    member('data/original'),
                    member('data/hard', tarfile.LNKTYPE, 'data/original'),
                    member('data/link', tarfile.SYMTYPE, 'original'),
                    member('unselected'),
                ],
            )
            extract(archive, ['data/hard', 'data/link'], destination, 0, lambda _: None)
            self.assertEqual((destination / 'data/hard').read_bytes(), b'payload')
            self.assertEqual((destination / 'data/original').stat().st_ino, (destination / 'data/hard').stat().st_ino)
            self.assertEqual((destination / 'data/link').readlink(), Path('original'))
            self.assertEqual((destination / 'data').stat().st_mode & 0o777, 0o750)
            self.assertEqual((destination / 'data/original').stat().st_mtime, 1_700_000_000)
            self.assertFalse((destination / 'unselected').exists())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)

    def test_failed_selection_creates_no_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'unsafe.tar'
            write_archive(archive, [member('dir', tarfile.DIRTYPE), member('dir/unsafe', tarfile.SYMTYPE, '/etc')])
            with self.assertRaises(BackupError):
                extract(archive, ['dir'], root / 'output', 0, lambda _: None)
            self.assertEqual(list(root.iterdir()), [archive])

    def test_existing_destination_and_symlink_ancestors_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'valid.tar'
            write_archive(archive, [member('file')])
            destination = root / 'existing'
            destination.mkdir()
            (root / 'alias').symlink_to(destination, target_is_directory=True)
            with self.assertRaisesRegex(BackupError, 'already exists'):
                extract(archive, ['file'], destination, 0, lambda _: None)
            with self.assertRaises(OSError):
                extract(archive, ['file'], root / 'alias/new', 0, lambda _: None)
            self.assertEqual(list(destination.iterdir()), [])

    def test_atomic_publication_does_not_overwrite_a_racing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'staging').mkdir()
            (root / 'destination').mkdir()
            with protected_directory(root) as parent, self.assertRaisesRegex(BackupError, 'already exists'):
                publish(parent, 'staging', 'destination')
            self.assertTrue((root / 'staging').is_dir())
            self.assertTrue((root / 'destination').is_dir())

    def test_gnu_sparse_archive_is_inspectable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (root / 'sparse').open('wb') as output:
                output.seek(16 * 1024 * 1024)
                output.write(b'end')
            archive = root / 'sparse.tar'
            subprocess.run(['tar', '--sparse', '-cf', str(archive), '-C', str(root), 'sparse'], check=True)
            entries = inspect(archive)
            self.assertEqual(entries[0].size, 16 * 1024 * 1024 + 3)
