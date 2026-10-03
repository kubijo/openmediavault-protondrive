"""Development commands for the persistent OMV test guest."""

import argparse
from pathlib import Path

import interactive_guest as guest
import vm_banner
from console import new_console


def recipes():
    vm_banner.recipes(new_console(), 'Guest commands · just <recipe>', '/root/justfile')


def banner():
    console = new_console()
    vm_banner.path(console, 'Guest justfile', '/root/justfile')
    recipes()


def pending():
    if guest.DIRTY_MODULES.exists():
        guest.run('omv-salt', 'deploy', 'list-dirty')
    else:
        print('No pending configuration changes.')


def apply():
    guest.check_pending_changes()
    guest.wait_for_monit()
    guest.apply_configuration(*guest.INSTALL_MODULES)


def main():
    commands = {'pending': pending, 'apply': apply, 'recipes': recipes, 'banner': banner}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=commands)
    options = parser.parse_args()
    if not Path('/var/lib/protondrive-interactive-vm').is_file():
        raise SystemExit('Run inside the interactive test VM')
    guest.setup_traceback()
    commands[options.command]()


if __name__ == '__main__':
    main()
