# Restore an archive

Use a scratch directory or a disposable VM for the first restore. Archive members are relative to `/`; extracting into
the live root filesystem would replace system and application files.

1. In Proton Drive, open the configured backup folder, then the instance and set UUID folders.

2. Download both the `.tar.zst` archive and its matching `.manifest.json` file through Proton Drive's web interface.

3. Compare the archive's SHA-256 and byte length with the manifest before extracting it:

   ```sh
   sha256sum appData-20261001T0300Z.tar.zst
   stat --format=%s appData-20261001T0300Z.tar.zst
   cat appData-20261001T0300Z.tar.zst.manifest.json
   zstd --test appData-20261001T0300Z.tar.zst
   tar --list --zstd --file appData-20261001T0300Z.tar.zst
   ```

4. As root, extract into an empty scratch directory:

   ```sh
   mkdir -p /restore-test
   tar --extract --zstd --numeric-owner --same-owner --same-permissions \
       --acls --xattrs --xattrs-include='*' \
       --file appData-20261001T0300Z.tar.zst --directory /restore-test
   ```

5. Compare ownership, permissions, ACLs and extended attributes with the source using `stat`, `getfacl -n`, and
   `getfattr -d -m-`. The appData tree appears under `/restore-test/data/appData`.

6. In an isolated environment, start the recovered application against its restored state and verify its database. Do
   not point running production containers at a partially restored directory.

Restoring the system set requires separate recovery decisions about network configuration, OMV configuration, Docker,
and application versions. The plugin does not perform an automatic bare-metal restore. Sign in again on a rebuilt NAS;
the default sets do not contain the plugin's private Proton session state.
