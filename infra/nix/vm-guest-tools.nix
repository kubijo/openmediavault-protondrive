{
  pkgs,
  src,
}:
pkgs.runCommand "omv-protondrive-vm-tools" { nativeBuildInputs = [ pkgs.gnutar ]; } ''
  mkdir -p "$out" "$TMPDIR/omv-protondrive-vm"
  cp -r ${src}/tests/integration/. "$TMPDIR/omv-protondrive-vm/"
  cp ${src}/tools/*.py "$TMPDIR/omv-protondrive-vm/"
  chmod u+w "$TMPDIR/omv-protondrive-vm/ui-cancel-bin"
  chmod +x "$TMPDIR/omv-protondrive-vm/ui-cancel-bin/tar.sh"
  ln -s tar.sh "$TMPDIR/omv-protondrive-vm/ui-cancel-bin/tar"
  install -m 0755 ${pkgs.pkgsStatic.just}/bin/just "$TMPDIR/omv-protondrive-vm/just"
  tar -C "$TMPDIR" -cf "$out/omv-protondrive-vm-tools.tar" omv-protondrive-vm
''
