"""Check VM cache publication, disk isolation, and orchestration without booting guests."""

import contextlib
import io
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console
from tests.integration import provision_guest as provision

import vm_runtime
from process_output import ProcessOutput
from tool_data import decode
from vm_cache import BaseCache, fingerprint

ROOT = Path(__file__).resolve().parents[2]


class CacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = BaseCache(self.root, {'image': 'debian', 'provision': 'v1'})

        def build(path: Path) -> None:
            path.write_bytes(b'base image')

        self.build = Mock(side_effect=build)

    def test_reuses_matching_complete_image(self):
        first, built = self.cache.ensure(self.build)
        self.assertTrue(built)
        self.assertEqual(self.cache.ensure(self.build), (first, False))
        self.build.assert_called_once()
        self.assertEqual(first.stat().st_mode & 0o222, 0)

    def test_changed_inputs_invalidate_base(self):
        original, _ = self.cache.ensure(self.build)
        changed = BaseCache(self.root, {**self.cache.inputs, 'provision': 'v2'})
        replacement, built = changed.ensure(self.build)
        self.assertTrue(built)
        self.assertNotEqual(original, replacement)
        self.assertTrue(original.exists())

    def test_refresh_keeps_old_backing_file_immutable(self):
        original, _ = self.cache.ensure(self.build)
        digest = fingerprint(original)

        def build(path: Path) -> None:
            path.write_bytes(b'new base')

        replacement, built = self.cache.ensure(build, refresh=True)
        self.assertTrue(built)
        self.assertNotEqual(original, replacement)
        self.assertEqual(fingerprint(original), digest)
        self.assertEqual(self.cache.current(), replacement)

    def test_failed_refresh_never_publishes_partial_image(self):
        original, _ = self.cache.ensure(self.build)

        def fail(path: Path) -> None:
            path.write_bytes(b'incomplete')
            raise RuntimeError('provisioning failed')

        with self.assertRaisesRegex(RuntimeError, 'provisioning failed'):
            self.cache.ensure(fail, refresh=True)
        self.assertEqual(self.cache.current(), original)
        self.assertFalse(list(self.cache.directory.glob('.building-*')))

    def test_empty_builder_output_is_not_published(self):
        with self.assertRaisesRegex(RuntimeError, 'did not produce'):
            self.cache.ensure(lambda path: path.touch())
        self.assertIsNone(self.cache.current())

    def test_invalid_pointer_and_missing_image_are_cache_misses(self):
        image, _ = self.cache.ensure(self.build)
        image.unlink()
        self.assertIsNone(self.cache.current())
        pointer = self.cache.directory / 'current.json'
        for value in ('not json', '{}', '{"generation": null}', '{"generation": "../../outside"}'):
            with self.subTest(value=value):
                pointer.write_text(value)
                self.assertIsNone(self.cache.current())

    def test_concurrent_requests_build_once(self):
        barrier = threading.Barrier(2)

        def request(_: int) -> tuple[Path, bool]:
            barrier.wait(timeout=5)
            return BaseCache(self.root, self.cache.inputs).ensure(self.build)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(request, range(2)))
        self.assertEqual(first[0], second[0])
        self.assertEqual(sorted((first[1], second[1])), [False, True])
        self.build.assert_called_once()

    def test_real_qcow_overlays_do_not_modify_cached_base(self):
        def command(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(args, check=True, capture_output=True, text=True)

        def build(path: Path) -> None:
            command('qemu-img', 'create', '-f', 'qcow2', str(path), '1M')

        base, _ = self.cache.ensure(build)
        digest = fingerprint(base)
        for index in range(2):
            overlay = self.root / f'overlay-{index}.qcow2'
            command('qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(base), str(overlay))
            command('qemu-io', '-f', 'qcow2', '-c', 'read -P 0 0 512', str(overlay))
            command('qemu-io', '-f', 'qcow2', '-c', 'write -P 42 0 512', str(overlay))
            info = decode(command('qemu-img', 'info', '--output=json', str(overlay)).stdout)
            self.assertEqual(info['full-backing-filename'], str(base))
        self.assertEqual(fingerprint(base), digest)


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vm = vm_runtime
        image = self.root / 'debian.qcow2'
        package = self.root / 'plugin.deb'
        image.write_bytes(b'debian')
        package.write_bytes(b'plugin')
        self.options = self.vm.Options(image=image, package=package, cache_dir=self.root / 'cache', reports=self.root)
        self.output = ProcessOutput(in_clanker=True)
        self.buffer = io.StringIO()
        self.output.console = Console(file=self.buffer, color_system=None)

    def test_cache_hit_still_boots_fresh_guest_and_runs_tests(self):
        guests: list[Mock] = []

        @contextlib.contextmanager
        def boot(*args: object, **kwargs: object):
            guest = Mock()
            guests.append(guest)
            yield guest

        def build(path: Path, *args: object) -> None:
            path.write_bytes(b'base')

        with (
            patch.object(self.vm, 'run', return_value=Mock(stdout='python3 (>= 3.11)')),
            patch.object(self.vm, 'base_inputs', return_value={'image': 'debian'}),
            patch.object(self.vm, 'build_base', side_effect=build) as build,
            patch.object(self.vm, 'boot', side_effect=boot),
            patch.object(self.vm, 'provision') as provision,
            patch.object(self.vm, 'test_guest') as test_guest,
        ):
            self.vm.run_vm(self.options, self.output)
            self.options.package.write_bytes(b'updated plugin')
            self.vm.run_vm(self.options, self.output)
        build.assert_called_once()
        provision.assert_not_called()
        self.assertEqual(test_guest.call_count, 2)
        self.assertEqual(len(guests), 2)
        self.assertIsNot(guests[0], guests[1])
        self.assertIn('CACHE HIT', self.buffer.getvalue())

    def test_no_cache_provisions_without_touching_cache(self):
        self.options.no_cache = True
        with (
            patch.object(self.vm, 'run', return_value=Mock(stdout='python3')),
            patch.object(self.vm, 'BaseCache') as cache,
            patch.object(self.vm, 'boot', return_value=contextlib.nullcontext(Mock())),
            patch.object(self.vm, 'provision') as provision,
            patch.object(self.vm, 'test_guest') as test_guest,
        ):
            self.vm.run_vm(self.options, self.output)
        cache.assert_not_called()
        provision.assert_called_once()
        test_guest.assert_called_once()
        self.assertFalse(self.options.cache_dir.exists())

    def test_conflicting_modes_fail_before_starting_vm(self):
        self.options.no_cache = True
        self.options.refresh_base = True
        with self.assertRaisesRegex(ValueError, 'cannot be combined'):
            self.vm.run_vm(self.options, self.output)

    def test_dependency_and_image_changes_invalidate_inputs(self):
        with patch.object(self.vm, 'run', return_value=Mock(stdout='QEMU fixture')):
            original = self.vm.base_inputs(self.options, 'python3')
            self.options.package.write_bytes(b'plugin code edit')
            self.assertEqual(original, self.vm.base_inputs(self.options, 'python3'))
            self.assertNotEqual(original, self.vm.base_inputs(self.options, 'python3, zstd'))
            self.options.image.write_bytes(b'new Debian')
            self.assertNotEqual(original, self.vm.base_inputs(self.options, 'python3'))

    def test_failed_shutdown_does_not_convert_or_publish(self):
        guest = Mock()
        guest = Mock(process=Mock(wait=Mock(return_value=1)))
        with (
            patch.object(self.vm, 'boot', return_value=contextlib.nullcontext(guest)),
            patch.object(self.vm, 'provision'),
            patch.object(self.vm, 'run') as run,
            self.assertRaisesRegex(RuntimeError, 'shut down cleanly'),
        ):
            self.vm.build_base(self.root / 'base.qcow2', self.options, 'python3', self.output)
        run.assert_not_called()

    def test_provisioning_refuses_to_run_outside_disposable_guest(self):
        with (
            patch.object(provision.Path, 'exists', return_value=False),
            patch.object(provision.subprocess, 'run') as run,
            self.assertRaisesRegex(SystemExit, 'disposable VM'),
        ):
            provision.main()
        run.assert_not_called()

    def test_seal_removes_identity_then_schedules_shutdown_without_new_ssh(self):
        files = ('etc/machine-id', 'etc/ssh/ssh_host_ed25519_key', 'root/.ssh/authorized_keys')
        for name in files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('old identity')

        def fixture_path(name: str) -> Path:
            return self.root / name.lstrip('/')

        with (
            patch.object(provision, 'Path', side_effect=fixture_path),
            patch.object(provision.subprocess, 'run', return_value=Mock(stdout='')) as run,
            patch.object(provision.os, 'sync'),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            provision.seal()
        self.assertFalse((self.root / 'root/.ssh/authorized_keys').exists())
        self.assertFalse((self.root / 'etc/ssh/ssh_host_ed25519_key').exists())
        self.assertEqual((self.root / 'etc/machine-id').read_text(), 'uninitialized\n')
        self.assertEqual(run.call_args.args[0][0], 'systemd-run')
