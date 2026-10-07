{
  pkgs,
  python,
  src,
  cli,
  apiRuntime,
  webApp,
}:
let
  # Source organization is independent of OMV's required installation paths.
  installPaths = {
    "src/bin/omv-protondrive" = "usr/sbin/omv-protondrive";
    "src/bin/omv-protondrive-auth" = "usr/sbin/omv-protondrive-auth";
    "src/bin/session.sh" = "usr/share/openmediavault-protondrive/session.sh";
    "src/protondrive" = "usr/share/openmediavault-protondrive/protondrive";
    "src/api/protondrive_api" = "usr/share/openmediavault-protondrive/protondrive_api";
    "src/api/buf" = "usr/share/openmediavault-protondrive/buf";
    "src/api/auth.php" = "usr/share/openmediavault-protondrive/auth.php";
    "src/omv" = "usr/share/openmediavault";
    "src/salt" = "srv/salt/omv/deploy/protondrive";
    "src/monit/protondrive.sls" = "srv/salt/omv/deploy/monit/services/protondrive.sls";
    "src/web/protondrive.css" = "usr/share/openmediavault-protondrive/protondrive.css";
    "src/nginx/90-protondrive.conf" = "etc/nginx/openmediavault-webgui.d/90-protondrive.conf";
  };
in
pkgs.runCommand "openmediavault-protondrive-7.0.0"
  {
    nativeBuildInputs = [
      pkgs.binutils
      pkgs.dpkg
      pkgs.gzip
    ];
  }
  ''
    export SOURCE_DATE_EPOCH=1790845200
    cp -r ${src}/debian debian
    chmod -R u+w debian
    mkdir -p package/DEBIAN "$out"
    ${pkgs.lib.concatStringsSep "\n" (
      pkgs.lib.mapAttrsToList (source: destination: ''
        mkdir -p package/${dirOf destination}
        cp -r ${src}/${source} package/${destination}
      '') installPaths
    )}
    mkdir -p package/usr/lib/openmediavault-protondrive/api package/usr/share/openmediavault-protondrive/web
    cp -r ${apiRuntime}/. package/usr/lib/openmediavault-protondrive/api/
    cp -r ${webApp}/. package/usr/share/openmediavault-protondrive/web/
    find package -type d -exec chmod 0755 {} +
    find package -type f -exec chmod 0644 {} +
    ${python}/bin/python ${src}/tools/build_workbench.py \
      ${src}/src/omv/workbench ${src}/src/web/templates \
      package/usr/share/openmediavault/workbench
    find package -name __pycache__ -type d -prune -exec rm -r {} +
    find package -name '*.pyc' -delete
    find package/usr/lib/openmediavault-protondrive/api -type f -name '*.so' \
      -exec strip --strip-unneeded {} +
    install -Dm755 ${cli} package/usr/lib/openmediavault-protondrive/proton-drive
    chmod 0755 package/usr/sbin/* package/usr/share/openmediavault/confdb/create.d/* \
      package/usr/share/openmediavault-protondrive/session.sh
    for script in postinst prerm postrm; do
      install -m755 debian/openmediavault-protondrive.$script package/DEBIAN/$script
    done
    install -m644 debian/openmediavault-protondrive.triggers package/DEBIAN/triggers
    printf '%s\n' /etc/nginx/openmediavault-webgui.d/90-protondrive.conf > package/DEBIAN/conffiles
    install -Dm644 debian/openmediavault-protondrive.lintian-overrides package/usr/share/lintian/overrides/openmediavault-protondrive
    docs=package/usr/share/doc/openmediavault-protondrive
    mkdir -p "$docs"
    install -m644 debian/copyright ${src}/README.md "$docs/"
    install -m644 ${src}/docs/restore.md "$docs/RESTORE.md"
    gzip -n -9 < debian/changelog > "$docs/changelog.gz"
    # Debian's own structured control parser supplies XB fields, version and size.
    dpkg-gencontrol -popenmediavault-protondrive -Ppackage -v7.0.0
    (cd package; find usr srv etc -type f -print0 | sort -z | xargs -0 md5sum) > package/DEBIAN/md5sums
    find package -exec touch --date="@$SOURCE_DATE_EPOCH" {} +
    dpkg-deb --build --root-owner-group package "$out/openmediavault-protondrive_7.0.0_amd64.deb"
  ''
