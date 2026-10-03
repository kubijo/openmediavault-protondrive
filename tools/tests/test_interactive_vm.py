"""Persistent VM lifecycle tests using temporary disks and a fake local QMP server."""

import contextlib
import io
import json
import os
import pty
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import interactive_vm
import vm_control
import vm_runtime
from process_output import ProcessOutput
from rich.console import Console
from test_vm_cache import load_guest_module
from vm_control import VMState

MONIT_RELOAD_ERROR = """Failed to execute omv-salt deploy run nginx:
----------
          ID: monitor_nginx_service
    Function: module.run
      Result: False
     Comment: 'monit.monitor': False
Summary for protondrive-test
Succeeded: 21 (changed=12)
Failed:     1
[ERROR   ] stdout: There is no service named "nginx"
"""


def failed_apply(message):
    return subprocess.CalledProcessError(
        1, ['omv-rpc'], output=json.dumps({'response': None, 'error': {'message': message}})
    )


class InteractiveVMTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='omv-interactive-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = VMState(self.root / 'state')
        self.options = interactive_vm.Options('up', state_dir=self.state.root)
        self.output = ProcessOutput(in_clanker=True)
        self.output.console = Console(file=io.StringIO(), color_system=None)

    def seed_state(self):
        self.state.instance.mkdir(parents=True)
        self.state.write({'initialized': True, 'ssh_port': 2222, 'http_port': 8080, 'remote_folder': '/my-files/test'})
        (self.state.instance / 'disk.qcow2').write_bytes(b'persistent login state')
        (self.state.instance / 'admin-password').write_text('fixture-password\n')

    def test_existing_state_does_not_resolve_new_base_or_replace_disk(self):
        self.seed_state()
        with patch.object(vm_runtime, 'resolve_base') as resolve:
            interactive_vm.create(self.options, self.state, self.output)
        resolve.assert_not_called()
        self.assertEqual((self.state.instance / 'disk.qcow2').read_bytes(), b'persistent login state')

    def test_real_persistent_overlay_survives_base_cache_removal(self):
        base = self.root / 'cached-base.qcow2'
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(base), '16M'], check=True, capture_output=True)
        package = self.root / 'plugin.deb'
        package.touch()
        self.options.image = base
        self.options.package = package
        runtime_run = vm_runtime.run

        def run(*args, **kwargs):
            if args[0] == 'dpkg-deb':
                return Mock(stdout='python3')
            return runtime_run(*args, **kwargs)

        with (
            self.state.lock(),
            patch.object(vm_runtime, 'resolve_base', return_value=base),
            patch.object(vm_runtime, 'run', side_effect=run),
        ):
            interactive_vm.create(self.options, self.state, self.output)
        base.unlink()
        result = subprocess.run(
            ['qemu-img', 'info', '--output=json', str(self.state.instance / 'disk.qcow2')],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(result.stdout)['full-backing-filename'], str(self.state.instance / 'base.qcow2'))
        subprocess.run(['qemu-img', 'check', str(self.state.instance / 'disk.qcow2')], check=True, capture_output=True)
        self.assertEqual(self.state.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.state.instance / 'admin-password').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.state.instance / 'admin-password').read_text(), 'admin\n')
        configuration = json.loads((self.state.instance / 'interactive-init.json').read_text())
        self.assertEqual(configuration['admin_password'], 'admin')
        self.assertFalse(self.state.read()['initialized'])
        self.assertTrue((self.state.instance / 'key').exists())

    def test_reset_requires_confirmation_and_stopped_vm(self):
        self.seed_state()
        with self.assertRaisesRegex(ValueError, 'deletes'):
            interactive_vm.reset(self.options, self.state, self.output)
        self.options.yes = True
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            self.assertRaisesRegex(RuntimeError, 'Stop'),
        ):
            interactive_vm.reset(self.options, self.state, self.output)
        self.assertTrue(self.state.metadata.exists())
        with patch.object(self.state, 'running', return_value=None):
            interactive_vm.reset(self.options, self.state, self.output)
        self.assertFalse(self.state.instance.exists())

    def test_snapshot_restore_round_trip_requires_stopped_initialized_vm(self):
        self.seed_state()
        disk = self.state.instance / 'disk.qcow2'
        disk.unlink()
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '16M'], check=True, capture_output=True)
        subprocess.run(
            ['qemu-io', '-f', 'qcow2', '-c', 'write -P 0x41 0 512', str(disk)], check=True, capture_output=True
        )
        with patch.object(self.state, 'running', return_value=None):
            interactive_vm.snapshot(self.options, self.state, self.output)
        subprocess.run(
            ['qemu-io', '-f', 'qcow2', '-c', 'write -P 0x42 0 512', str(disk)], check=True, capture_output=True
        )
        with patch.object(self.state, 'running', return_value=None):
            interactive_vm.snapshot(self.options, self.state, self.output, restore=True)
        subprocess.run(
            ['qemu-io', '-f', 'qcow2', '-c', 'read -P 0x41 0 512', str(disk)], check=True, capture_output=True
        )
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            self.assertRaisesRegex(RuntimeError, 'Stop the VM'),
        ):
            interactive_vm.snapshot(self.options, self.state, self.output, restore=True)

    def test_lifecycle_lock_blocks_second_owner_and_reset(self):
        self.seed_state()
        self.options.yes = True
        with self.state.lock(), self.assertRaisesRegex(RuntimeError, 'already active'):
            interactive_vm.reset(self.options, VMState(self.state.root), self.output)
        self.assertTrue(self.state.metadata.exists())

    def test_stop_requests_graceful_shutdown_and_preserves_files(self):
        self.seed_state()
        with (
            patch.object(self.state, 'running', side_effect=[{'status': 'running'}, None]),
            patch.object(vm_control, 'qmp') as qmp,
        ):
            self.state.stop()
        qmp.assert_called_once_with(self.state.monitor, 'system_powerdown')
        self.assertEqual((self.state.instance / 'disk.qcow2').read_bytes(), b'persistent login state')

    def test_interactive_stop_uses_systemd_instead_of_ignored_power_button(self):
        guest = Mock()
        with (
            patch.object(self.state, 'running', side_effect=[{'status': 'running'}, None]),
            patch.object(interactive_vm, 'connection', return_value=guest),
            patch.object(vm_control, 'qmp') as qmp,
        ):
            interactive_vm.stop(self.state)
        guest.run.assert_called_once_with('systemctl', 'poweroff', '--no-block', timeout=15)
        qmp.assert_not_called()

    def test_force_stop_is_explicit_and_timeout_does_not_force_poweroff(self):
        with (
            patch.object(self.state, 'running', side_effect=[{'status': 'running'}, None]),
            patch.object(vm_control, 'qmp') as qmp,
        ):
            self.state.stop(force=True)
        qmp.assert_called_once_with(self.state.monitor, 'quit')
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(vm_control, 'qmp') as qmp,
            patch.object(vm_control.time, 'monotonic', side_effect=[0, 121]),
            self.assertRaisesRegex(RuntimeError, 'still running'),
        ):
            self.state.stop()
        qmp.assert_called_once_with(self.state.monitor, 'system_powerdown')

    def test_install_on_initialized_guest_does_not_send_initial_settings(self):
        self.seed_state()
        package = self.root / 'plugin.deb'
        package.touch()
        self.options.package = package
        (self.state.root / 'logs').mkdir()
        guest = Mock()
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(interactive_vm, 'connection', return_value=guest),
            patch.object(interactive_vm, 'setup_shell'),
        ):
            interactive_vm.install(self.options, self.state, self.output)
        self.assertEqual(guest.copy.call_count, 1)
        self.assertNotIn('/root/interactive-init.json', guest.python.call_args.args)
        self.assertEqual((self.state.instance / 'disk.qcow2').read_bytes(), b'persistent login state')

    def test_ports_are_only_forwarded_on_localhost(self):
        command = vm_runtime.qemu_command(self.root, self.root / 'serial.log', {22: 2222, 80: 8080})
        network = command[command.index('-netdev') + 1]
        self.assertEqual(network, 'user,id=net0,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:8080-:80')

    def test_up_reuses_running_vm_and_releases_lifecycle_lock(self):
        self.seed_state()
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(interactive_vm, 'create') as create,
            patch.object(interactive_vm, 'install') as install,
        ):
            interactive_vm.up(self.options, self.state, self.output)
            create.assert_not_called()
            install.assert_not_called()
            with self.state.lock():
                pass

    def test_running_vm_refuses_port_changes_and_retries_unfinished_install(self):
        self.seed_state()
        metadata = self.state.read()
        metadata['initialized'] = False
        self.state.write(metadata)
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(interactive_vm, 'install') as install,
        ):
            interactive_vm.up(self.options, self.state, self.output)
            install.assert_called_once()
            self.options.ssh_port = 2223
            with self.assertRaisesRegex(ValueError, 'Stop the VM'):
                interactive_vm.up(self.options, self.state, self.output)

    def test_failed_bringup_leaves_daemon_available_for_diagnosis(self):
        self.seed_state()
        with (
            patch.object(self.state, 'running', return_value=None),
            patch.object(interactive_vm.subprocess, 'run') as run,
            patch.object(vm_runtime, 'connect', side_effect=RuntimeError('SSH unavailable')),
            patch.object(interactive_vm, 'stop') as stop,
            self.assertRaisesRegex(RuntimeError, 'SSH unavailable'),
        ):
            interactive_vm.up(self.options, self.state, self.output)
        self.assertIn('-daemonize', run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        stop.assert_not_called()

    def test_ssh_exit_status_is_preserved_without_stopping_vm(self):
        self.seed_state()
        guest = Mock(ssh=['ssh', 'root@localhost'])
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(interactive_vm, 'setup_shell'),
            patch.object(interactive_vm, 'describe'),
            patch.object(interactive_vm, 'connection', return_value=guest),
            patch.object(interactive_vm.subprocess, 'run', return_value=Mock(returncode=7)),
            patch.object(interactive_vm, 'stop') as stop,
        ):
            self.assertEqual(interactive_vm.open_shell(self.options, self.state, self.output), 7)
        stop.assert_not_called()

    def test_up_no_shell_returns_without_opening_ssh(self):
        self.options.no_shell = True
        with (
            patch.object(interactive_vm, 'up') as up,
            patch.object(interactive_vm, 'describe') as describe,
            patch.object(interactive_vm, 'open_shell') as shell,
        ):
            interactive_vm.main(self.options)
        up.assert_called_once()
        describe.assert_called_once()
        shell.assert_not_called()

    def test_guest_shell_installs_tools_without_reinstalling_plugin(self):
        self.seed_state()
        binary = self.root / 'just'
        binary.touch()
        self.options.guest_just = binary
        guest = Mock()
        with patch.object(interactive_vm, 'connection', return_value=guest):
            interactive_vm.setup_shell(self.options, self.state)
        self.assertEqual(guest.run.call_args_list[0].args, ('test', '-f', '/var/lib/protondrive-interactive-vm'))
        guest.run.assert_any_call('install', '-m', '0755', '/root/just', '/usr/local/bin/just')
        guest.run.assert_any_call('install', '-m', '0644', '/root/guest.just', '/root/justfile')
        guest.python.assert_not_called()

    def test_describe_does_not_expose_saved_credentials(self):
        self.seed_state()
        interactive_vm.describe(self.state, self.output, 'STOPPED')
        self.assertNotIn('fixture-password', self.output.console.file.getvalue())

    def test_interactive_guest_refuses_host_and_upgrade_preserves_settings(self):
        guest = load_guest_module('interactive_guest')
        with (
            patch.object(guest.Path, 'exists', return_value=False),
            self.assertRaisesRegex(SystemExit, 'interactive VM'),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        with (
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run') as run,
            patch.object(guest, 'initialize') as initialize,
            patch.object(guest, 'wait_for_monit') as wait,
            patch.object(guest, 'brand_web_ui'),
            patch.object(guest, 'check_pending_changes'),
            patch.dict(guest.os.environ),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        initialize.assert_not_called()
        self.assertIn('--reinstall', run.call_args_list[0].args)
        wait.assert_called_once()

    def test_monit_readiness_retries_then_succeeds_and_timeout_fails(self):
        guest = load_guest_module('interactive_guest')
        failed = Mock(returncode=1, stdout='', stderr='socket unavailable')
        with (
            patch.object(guest.subprocess, 'run', side_effect=[failed, Mock(returncode=0), Mock(returncode=0)]) as run,
            patch.object(guest.time, 'sleep') as sleep,
        ):
            guest.wait_for_monit()
        self.assertEqual(run.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ['monit', 'status', 'nginx'],
                ['monit', 'status', 'nginx'],
                ['monit', 'status', 'php-fpm'],
            ],
        )
        sleep.assert_called_once_with(1)
        with (
            patch.object(guest.subprocess, 'run', return_value=failed),
            self.assertRaisesRegex(RuntimeError, 'socket unavailable'),
        ):
            guest.wait_for_monit(timeout=0)

    def test_web_deployment_waits_for_monit_and_does_not_ignore_failure(self):
        guest = load_guest_module('interactive_guest')
        steps = []
        with (
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run', side_effect=lambda *args, **kwargs: steps.append(args)),
            patch.object(guest, 'wait_for_monit', side_effect=lambda: steps.append(('ready',))),
            patch.object(guest, 'brand_web_ui'),
            patch.object(guest, 'check_pending_changes'),
            patch.dict(guest.os.environ),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        readiness = steps.index(('ready',))
        self.assertEqual(steps[readiness - 1], ('omv-salt', 'deploy', 'run', 'monit'))
        apply = steps[readiness + 1]
        self.assertEqual(apply[:-1], ('omv-rpc', '-u', 'admin', 'Config', 'applyChanges'))
        self.assertEqual(json.loads(apply[-1]), {'modules': list(guest.INSTALL_MODULES), 'force': True})
        self.assertEqual(sum('applyChanges' in step for step in steps), 1)
        with (
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run') as run,
            patch.object(guest, 'wait_for_monit', side_effect=RuntimeError('not ready')),
            patch.object(guest, 'check_pending_changes'),
            patch.dict(guest.os.environ),
            self.assertRaisesRegex(RuntimeError, 'not ready'),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        self.assertEqual(run.call_args.args, ('omv-salt', 'deploy', 'run', 'monit'))

    def test_apply_retries_only_after_monit_readiness_and_pending_change_check(self):
        guest = load_guest_module('interactive_guest')
        for service, state in (('nginx', 'monitor_nginx_service'), ('php-fpm', 'monitor_phpfpm_service')):
            message = MONIT_RELOAD_ERROR.replace('monitor_nginx_service', state).replace('"nginx"', f'"{service}"')
            for failure in (
                message,
                message.replace(
                    f'There is no service named "{service}"',
                    'Unix socket /run/monit.sock connection error -- No such file or directory',
                ),
            ):
                with (
                    self.subTest(service=service, failure=failure),
                    patch.object(guest, 'run', side_effect=[failed_apply(failure), None]) as run,
                    patch.object(guest, 'wait_for_monit') as wait,
                    patch.object(guest, 'check_pending_changes') as check,
                ):
                    guest.apply_configuration(*guest.INSTALL_MODULES)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[0], run.call_args_list[1])
                wait.assert_called_once()
                check.assert_called_once()

    def test_apply_does_not_retry_unrelated_or_multiple_failures(self):
        guest = load_guest_module('interactive_guest')
        for message in (
            'nginx configuration is invalid',
            MONIT_RELOAD_ERROR.replace('monitor_nginx_service', 'test_nginx_service_config'),
            MONIT_RELOAD_ERROR.replace('Failed:     1', 'Failed:     2'),
        ):
            with (
                self.subTest(message=message),
                patch.object(guest, 'run', side_effect=failed_apply(message)) as run,
                patch.object(guest, 'wait_for_monit') as wait,
                self.assertRaisesRegex(RuntimeError, 'OMV configuration apply failed'),
            ):
                guest.apply_configuration(*guest.INSTALL_MODULES)
            run.assert_called_once()
            wait.assert_not_called()

    def test_apply_retry_limit_and_new_pending_changes_still_fail(self):
        guest = load_guest_module('interactive_guest')
        for pending_error, attempts in ((None, 3), (RuntimeError('unrelated pending change'), 1)):
            with (
                self.subTest(pending_error=pending_error),
                patch.object(guest, 'run', side_effect=failed_apply(MONIT_RELOAD_ERROR)) as run,
                patch.object(guest, 'wait_for_monit'),
                patch.object(guest, 'check_pending_changes', side_effect=pending_error),
                self.assertRaises(RuntimeError),
            ):
                guest.apply_configuration(*guest.INSTALL_MODULES)
            self.assertEqual(run.call_count, attempts)

    def test_failed_configuration_apply_stops_installation(self):
        guest = load_guest_module('interactive_guest')
        failure = subprocess.CalledProcessError(1, ['omv-rpc', 'Config', 'applyChanges'])
        with (
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run') as run,
            patch.object(guest, 'wait_for_monit'),
            patch.object(guest, 'apply_configuration', side_effect=failure),
            patch.object(guest, 'check_pending_changes'),
            patch.object(guest, 'brand_web_ui') as branding,
            patch.dict(guest.os.environ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        branding.assert_not_called()
        self.assertFalse(any(call.args[0] == 'omv-mkworkbench' for call in run.call_args_list))

    def test_install_without_dirty_modules_file_reaches_configuration_apply(self):
        guest = load_guest_module('interactive_guest')
        pending = self.root / 'dirtymodules.json'
        with (
            patch.object(guest, 'DIRTY_MODULES', pending),
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run'),
            patch.object(guest, 'wait_for_monit'),
            patch.object(guest, 'apply_configuration') as apply,
            patch.object(guest, 'brand_web_ui'),
            patch.dict(guest.os.environ),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        apply.assert_called_once_with(*guest.INSTALL_MODULES)
        self.assertFalse(pending.exists())

    def test_pending_state_accepts_empty_list_but_rejects_malformed_content(self):
        guest = load_guest_module('interactive_guest')
        pending = self.root / 'dirtymodules.json'
        with patch.object(guest, 'DIRTY_MODULES', pending):
            pending.write_text('[]')
            guest.check_pending_changes()
            for content in ('', '{', '{}', '[null]'):
                with self.subTest(content=content):
                    pending.write_text(content)
                    with self.assertRaises((ValueError, RuntimeError)):
                        guest.check_pending_changes()

    def test_unreadable_pending_state_is_not_treated_as_clean(self):
        guest = load_guest_module('interactive_guest')
        with (
            patch.object(guest.Path, 'read_text', side_effect=PermissionError),
            self.assertRaises(PermissionError),
        ):
            guest.check_pending_changes()

    def test_unrelated_pending_changes_block_install_before_mutation(self):
        guest = load_guest_module('interactive_guest')
        pending = self.root / 'dirtymodules.json'
        pending.write_text('["samba", "protondrive"]')
        with (
            patch.object(guest, 'DIRTY_MODULES', pending),
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run') as run,
            self.assertRaisesRegex(RuntimeError, 'samba'),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        run.assert_not_called()
        self.assertEqual(json.loads(pending.read_text()), ['samba', 'protondrive'])
        pending.write_text('["protondrive"]')
        with patch.object(guest, 'DIRTY_MODULES', pending):
            guest.check_pending_changes()

    def test_pending_changes_are_rechecked_before_final_apply(self):
        guest = load_guest_module('interactive_guest')
        with (
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run'),
            patch.object(guest, 'wait_for_monit'),
            patch.object(guest, 'check_pending_changes', side_effect=[None, RuntimeError('samba')]),
            patch.object(guest, 'apply_configuration') as apply,
            patch.dict(guest.os.environ),
            self.assertRaisesRegex(RuntimeError, 'samba'),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        apply.assert_not_called()

    def test_retry_cannot_overwrite_files_until_remote_cancellation_is_confirmed(self):
        self.seed_state()
        self.options.package = self.root / 'plugin.deb'
        self.options.package.touch()
        guest = Mock()
        guest.ensure_idle.side_effect = RuntimeError('remote job still running')
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(interactive_vm, 'connection', return_value=guest),
            self.assertRaisesRegex(RuntimeError, 'remote job still running'),
        ):
            interactive_vm.install(self.options, self.state, self.output)
        guest.copy.assert_not_called()

    def test_branding_is_idempotent_and_preserves_packaged_html(self):
        guest = load_guest_module('interactive_guest')
        webroot = self.root / 'html'
        snippets = self.root / 'snippets'
        webroot.mkdir()
        index = webroot / 'index.html'
        index.write_text('packaged OMV entry point')
        with patch.object(guest, 'run'):
            guest.brand_web_ui(webroot, snippets)
            first = (snippets / '90-interactive-vm.conf').read_text()
            guest.brand_web_ui(webroot, snippets)
        self.assertEqual(index.read_text(), 'packaged OMV entry point')
        self.assertEqual((snippets / '90-interactive-vm.conf').read_text(), first)
        self.assertEqual(
            (webroot / 'interactive-vm.css').read_text(),
            Path(guest.__file__).with_name('interactive-vm.css').read_text(),
        )
        source = Path(__file__).resolve().parents[2]
        shutil.copyfile(source / 'src/nginx/90-protondrive.conf', snippets / '90-protondrive.conf')
        configuration = self.root / 'nginx.conf'
        shutil.copyfile(Path(__file__).parent / 'fixtures/nginx.conf', configuration)
        result = subprocess.run(
            ['nginx', '-t', '-e', 'stderr', '-p', str(self.root), '-c', str(configuration)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_remote_pty_has_closed_input_and_plain_mode_does_not_request_pty(self):
        guest = vm_runtime.guest_connection(self.root, self.root / 'key', 2222)
        fixture = Path(__file__).parent / 'fixtures/process_output.py'
        output = Mock(animate=True)
        guest.python(output, str(fixture), self.root / 'log', 10, 'remote-environment')
        command = output.run.call_args.args[0]
        self.assertEqual(command[1], '-tt')
        self.assertIn('--property=KillMode=control-group', command[-1])
        # Exercise the payload's terminal and stdin locally; supervision is tested separately.
        words = shlex.split(command[-1])
        payload = shlex.join(words[words.index('env') : words.index('<')]) + ' < /dev/null'
        master, slave = pty.openpty()
        try:
            with patch.dict(os.environ, {'PATH': f'{Path(sys.executable).parent}:{os.environ["PATH"]}'}):
                result = subprocess.run(
                    ['sh', '-c', payload],
                    input=b'input must not reach guest',
                    stdout=slave,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=False,
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b'input disconnected', os.read(master, 4096))
        finally:
            os.close(master)
            os.close(slave)
        output.animate = False
        guest.python(output, str(fixture), self.root / 'log', 10)
        command = output.run.call_args.args[0]
        self.assertEqual(command[1], '-T')
        self.assertIn('TERM=dumb', command[-1])

    def test_interactive_connection_does_not_enable_disposable_test_scripts(self):
        guest = Mock()
        guest.ssh = ['ssh', 'fixture-guest']
        output = Mock()
        output.stage.side_effect = lambda title: contextlib.nullcontext()
        runtime = vm_runtime.Options(image=self.root, package=self.root, reports=self.root)
        with (
            patch.object(vm_runtime, 'guest_connection', return_value=guest),
            patch.object(vm_runtime.subprocess, 'run', return_value=Mock(returncode=0)),
        ):
            vm_runtime.connect(runtime, self.root, self.root / 'key', 2222, Mock(), output, '', disposable=False)
        guest.run.assert_not_called()


class MonitorTests(unittest.TestCase):
    def test_qmp_negotiates_capabilities_and_ignores_events(self):
        with tempfile.TemporaryDirectory(prefix='omv-qmp-') as temporary, socket.socket(socket.AF_UNIX) as listener:
            path = Path(temporary) / 'qmp.sock'
            listener.bind(str(path))
            listener.listen(1)
            listener.settimeout(5)

            def server():
                with listener.accept()[0] as connection, connection.makefile('rwb', buffering=0) as stream:
                    connection.settimeout(5)
                    stream.write(json.dumps({'QMP': {'version': {}, 'capabilities': []}}).encode() + b'\r\n')
                    for name in ('qmp_capabilities', 'query-status'):
                        request = json.loads(stream.readline())
                        self.assertEqual(request['execute'], name)
                        stream.write(json.dumps({'event': 'RESUME'}).encode() + b'\r\n')
                        response = {'return': {'status': 'running'}, 'id': request['id']}
                        stream.write(json.dumps(response).encode() + b'\r\n')

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(server)
                self.assertEqual(vm_control.qmp(path, 'query-status'), {'status': 'running'})
                future.result(timeout=5)
