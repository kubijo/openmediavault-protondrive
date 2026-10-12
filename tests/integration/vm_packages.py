"""Apt packages for the disposable OMV VM base."""

BOOTSTRAP = ('wget', 'gnupg', 'ca-certificates', 'systemd-resolved', 'psmisc')
BASE = (
    'openmediavault',
    'docker.io',
    'docker-cli',
    'busybox-static',
    'acl',
    'attr',
    'python3-jinja2',
    'python3-yaml',
    'lintian',
)
