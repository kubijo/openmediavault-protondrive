#!/bin/sh
# GPL-3.0-or-later. Follows the OMV OneDrive confdb creation convention.
set -eu
# shellcheck disable=SC1091
. /usr/share/openmediavault/scripts/helper-functions
base=/config/services/protondrive
if ! omv_config_exists "$base"; then
    omv_config_add_node /config/services protondrive
    omv_config_add_key "$base" enable 0
    omv_config_add_key "$base" schedulehour 3
    omv_config_add_key "$base" scheduleminute 0
    omv_config_add_key "$base" stagingpath /data/.omv-protondrive
    omv_config_add_key "$base" remotepath '/my-files/open-media-vault-proton-backup'
    omv_config_add_key "$base" destinations ''
    omv_config_add_key "$base" instanceuuid "$(cat /proc/sys/kernel/random/uuid)"
    omv_config_add_key "$base" minimumfreebytes 1073741824
    omv_config_add_key "$base" containerstoptimeout 120
    omv_config_add_key "$base" commandtimeout 180
    omv_config_add_key "$base" transfertimeout 86400
    omv_config_add_node "$base" sets
    for name in system appData; do
        # add_node is idempotent, so populate a unique node before naming it set.
        omv_config_add_node "$base/sets" "$name"
        setpath="$base/sets/$name"
        omv_config_add_key "$setpath" uuid "$(cat /proc/sys/kernel/random/uuid)"
        omv_config_add_key "$setpath" name "$name"
        omv_config_add_key "$setpath" enable 1
        omv_config_add_key "$setpath" localkeep 2
        omv_config_add_key "$setpath" remotekeep 7
        if [ "$name" = system ]; then
            omv_config_add_key "$setpath" paths '/etc
/usr/local
/var/spool/cron'
            omv_config_add_key "$setpath" excludes ''
            omv_config_add_key "$setpath" stopcontainers 0
        else
            omv_config_add_key "$setpath" paths /data/appData
            omv_config_add_key "$setpath" excludes 'cache
logs
immich/cache
immich/redis'
            omv_config_add_key "$setpath" stopcontainers 1
        fi
        omv_config_rename "$setpath" set
    done
fi
