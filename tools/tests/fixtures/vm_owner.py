"""Exercise terminal signals with a disposable QEMU daemon and a fake SSH session."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import interactive_vm
from vm_control import VMState

root = Path(sys.argv[2])
state = VMState(root)
state.instance.mkdir()
state.write({'initialized': True, 'ssh_port': 2222, 'http_port': 8080, 'remote_folder': 'fixture'})
run = subprocess.run


def session(command, **kwargs):
    if command[0] == 'ssh':
        (root / 'ready').touch()
        if sys.argv[1] == 'exit':
            return subprocess.CompletedProcess(command, 0)
        return run([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
    return run(command, **kwargs)


with (
    patch.object(interactive_vm, 'setup_shell'),
    patch.object(interactive_vm.subprocess, 'run', side_effect=session),
    patch.object(interactive_vm.vm_runtime, 'connect', return_value=Mock()),
    patch.object(
        interactive_vm.vm_runtime,
        'qemu_command',
        return_value=['qemu-system-x86_64', '-machine', 'none', '-m', '64', '-display', 'none', '-S'],
    ),
):
    interactive_vm.main(interactive_vm.Options('up', state_dir=root, in_clanker=True))
