"""Check VM cache publication, disk isolation, and orchestration without booting guests."""

import contextlib
import io
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Self, cast
from unittest.mock import Mock, patch

from rich.console import Console
from tests.integration import provision_guest as provision
from tests.integration import vm_seed_identity as seed_identity

import vm_runtime
from process_output import ProcessOutput
from tool_data import decode
from vm_cache import BaseCache, fingerprint

ROOT = Path(__file__).resolve().parents[2]


class FakeProcess:
    def __init__(self, outcomes: list[int | subprocess.TimeoutExpired] | None = None, poll_code: int | None = None):
        self.outcomes = outcomes or []
        self.poll_code = poll_code
        self.timeouts: list[float | None] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, subprocess.TimeoutExpired):
            raise outcome
        return outcome

    def poll(self) -> int | None:
        return self.poll_code


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

    def test_seed_iso_carries_the_key_used_by_cached_guests(self):
        def command(*args: str, **_kwargs: object) -> Mock:
            if args[0] == 'ssh-keygen':
                (self.root / 'key.pub').write_text('ssh-ed25519 AAAA test\n')
            return Mock()

        with patch.object(self.vm, 'run', side_effect=command) as run:
            self.vm.prepare(self.root, self.options.image)
        self.assertEqual((self.root / 'authorized-key').read_text(), 'ssh-ed25519 AAAA test\n')
        iso = next(call.args for call in run.call_args_list if call.args[0] == 'genisoimage')
        self.assertIn(str(self.root / 'authorized-key'), iso)

    def test_disposable_vm_exposes_a_private_host_monitor(self):
        commands: list[list[str]] = []

        def launch(command: list[str], *, stdin: object) -> FakeProcess:
            commands.append(command)
            return FakeProcess(poll_code=0)

        with (
            patch.object(self.vm, 'prepare'),
            patch.object(self.vm, 'free_port', return_value=2222),
            patch.object(self.vm, 'connect', return_value=Mock()),
            patch.object(self.vm.subprocess, 'Popen', side_effect=launch),
            self.vm.boot(self.options, self.options.image, self.output) as _guest,
        ):
            pass
        command = commands[0]
        monitor = command[command.index('-qmp') + 1]
        self.assertRegex(monitor, r'^unix:.*/protondrive-vm-[^/]+/control\.sock,server=on,wait=off$')

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
            patch.object(self.vm, 'run', return_value=Mock(stdout='python3 (>= 3.13)')),
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
            self.assertEqual(original['packages'], fingerprint(ROOT / 'tests/integration/vm_packages.py'))
            self.options.package.write_bytes(b'plugin code edit')
            self.assertEqual(original, self.vm.base_inputs(self.options, 'python3'))
            self.assertNotEqual(original, self.vm.base_inputs(self.options, 'python3, zstd'))
            self.options.image.write_bytes(b'new Debian')
            self.assertNotEqual(original, self.vm.base_inputs(self.options, 'python3'))

    def test_base_recipe_excludes_test_output_but_tracks_provisioning(self):
        source = self.root / 'vm_runtime.py'
        source.write_text(
            """\
def prepare():
    return "seed-v1"
def run_vm():
    print("old progress")
"""
        )
        original = self.vm.base_recipe_fingerprint(source)
        source.write_text(source.read_text().replace('old progress', 'new progress'))
        self.assertEqual(original, self.vm.base_recipe_fingerprint(source))
        source.write_text(source.read_text().replace('seed-v1', 'seed-v2'))
        self.assertNotEqual(original, self.vm.base_recipe_fingerprint(source))

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

    def test_sealed_vm_uses_host_power_button_if_guest_timer_stalls(self):
        process = FakeProcess([subprocess.TimeoutExpired('qemu', 15), 0])
        guest = vm_runtime.Guest(ssh=[], scp=[], directory=self.root, process=cast(subprocess.Popen[bytes], process))
        with patch.object(self.vm, 'qmp') as qmp:
            self.vm.wait_for_sealed_shutdown(guest, 'Installed VM', self.output)
        qmp.assert_called_once_with(self.root / 'control.sock', 'system_powerdown')
        self.assertEqual(process.timeouts, [15, 105])
        self.assertIn('requesting host ACPI shutdown', self.buffer.getvalue())

    def test_sealed_vm_never_publishes_after_host_shutdown_timeout(self):
        process = FakeProcess([subprocess.TimeoutExpired('qemu', 15), subprocess.TimeoutExpired('qemu', 105)])
        guest = vm_runtime.Guest(ssh=[], scp=[], directory=self.root, process=cast(subprocess.Popen[bytes], process))
        with patch.object(self.vm, 'qmp'), self.assertRaisesRegex(RuntimeError, 'did not shut down'):
            self.vm.wait_for_sealed_shutdown(guest, 'Installed VM', self.output)

    def test_provisioning_refuses_to_run_outside_disposable_guest(self):
        with (
            patch.object(provision.Path, 'exists', return_value=False),
            patch.object(provision.subprocess, 'run') as run,
            self.assertRaisesRegex(SystemExit, 'disposable VM'),
        ):
            provision.main()
        run.assert_not_called()

    def test_seal_removes_identity_then_schedules_shutdown_without_new_ssh(self):
        files = (
            'etc/machine-id',
            'etc/ssh/ssh_host_ed25519_key',
            'root/.ssh/authorized_keys',
            'var/lib/cloud/instance/obj.pkl',
            'var/log/cloud-init.log',
        )
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
            provision.seal('sample-plugin')
        self.assertFalse((self.root / 'root/.ssh/authorized_keys').exists())
        self.assertFalse((self.root / 'etc/ssh/ssh_host_ed25519_key').exists())
        self.assertFalse((self.root / 'var/lib/cloud').exists())
        self.assertFalse((self.root / 'var/log/cloud-init.log').exists())
        self.assertEqual((self.root / 'etc/machine-id').read_text(), 'uninitialized\n')
        self.assertEqual(run.call_args.args[0][0], 'systemd-run')

    def test_guest_identity_service_requires_fresh_seed_before_ssh(self):
        (self.root / 'etc/systemd/system').mkdir(parents=True)
        self.assertTrue(
            (ROOT / 'tests/integration/vm_seed_identity.py').read_text().startswith('#!/usr/bin/env python3\n')
        )

        def fixture_path(name: str) -> Path:
            return self.root / name.lstrip('/')

        with (
            patch.object(provision, 'Path', side_effect=fixture_path),
            patch.object(provision, 'run'),
        ):
            provision.install_seed_identity()
        drop_in = self.root / 'etc/systemd/system/ssh.service.d/omv-test-seed-identity.conf'
        self.assertIn('Requires=omv-test-seed-identity.service', drop_in.read_text())
        self.assertIn('After=omv-test-seed-identity.service', drop_in.read_text())
        self.assertIn(
            'RemainAfterExit=yes', (self.root / 'etc/systemd/system/omv-test-seed-identity.service').read_text()
        )
        self.assertIn(
            'StandardError=journal+console',
            (self.root / 'etc/systemd/system/omv-test-seed-identity.service').read_text(),
        )

    def test_guest_identity_uses_current_seed_key(self):
        seed = self.root / 'seed'
        seed.mkdir()
        (seed / 'authorized-key').write_text('ssh-ed25519 AAAA test\n')
        destination = self.root / 'root/.ssh'
        seed_identity.apply_key(seed, destination)
        self.assertEqual((destination / 'authorized_keys').read_text(), 'ssh-ed25519 AAAA test\n')
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        self.assertEqual((destination / 'authorized_keys').stat().st_mode & 0o777, 0o600)
        (seed / 'authorized-key').write_text('invalid\n')
        with self.assertRaisesRegex(ValueError, 'Invalid test VM SSH key'):
            seed_identity.apply_key(seed, destination)
