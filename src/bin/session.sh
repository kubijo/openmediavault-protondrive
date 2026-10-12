#!/bin/bash
set -euo pipefail
umask 077
export HOME=/var/lib/openmediavault-protondrive/proton
export XDG_DATA_HOME="$HOME/.local/share"
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_CACHE_HOME="$HOME/.cache"
export XDG_RUNTIME_DIR=/run/omv-protondrive
export GCR_ALLOW_INTERACTION=false
mkdir -p "$XDG_DATA_HOME/keyrings" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"
if [[ ! -f "$HOME/keyring.key" ]]; then
    head -c 32 /dev/urandom | base64 >"$HOME/keyring.key.tmp"
    mv "$HOME/keyring.key.tmp" "$HOME/keyring.key"
fi
chmod 600 "$HOME/keyring.key"
if [[ ${1:-} != inside ]]; then
    exec dbus-run-session -- "$0" inside
fi
# GNOME Keyring reads the password from stdin; never export it to children.
gnome-keyring-daemon --unlock --components=secrets <"$HOME/keyring.key" >/dev/null
for _attempt in {1..30}; do
    if dbus-send --session --print-reply --dest=org.freedesktop.secrets \
        /org/freedesktop/secrets/collection/login org.freedesktop.DBus.Properties.Get \
        string:org.freedesktop.Secret.Collection string:Locked | grep -q 'boolean false'; then
        exec /usr/sbin/omv-protondrive daemon
    fi
    sleep 1
done
echo 'The private login keyring could not be unlocked' >&2
exit 1
