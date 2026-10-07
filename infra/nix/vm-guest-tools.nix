{
  pkgs,
  src,
}:
let
  inherit (builtins)
    filter
    fromTOML
    genericClosure
    head
    length
    readFile
    ;
  inherit (pkgs) lib;
  lock = fromTOML (readFile (src + "/uv.lock"));
  package =
    name:
    lib.findSingle (item: item.name == name) (throw "Missing guest dependency ${name}")
      (throw "Ambiguous guest dependency ${name}")
      lock.package;
  dependencies = genericClosure {
    startSet = map (key: { inherit key; }) [
      "tyro"
      "rich"
    ];
    operator =
      item: map (dependency: { key = dependency.name; }) ((package item.key).dependencies or [ ]);
  };
  wheels = map (
    item:
    let
      candidates = filter (wheel: lib.hasSuffix "-py3-none-any.whl" wheel.url) (package item.key).wheels;
      wheel =
        assert lib.assertMsg (
          length candidates == 1
        ) "Guest dependency ${item.key} needs one portable Python wheel";
        head candidates;
    in
    pkgs.fetchurl {
      inherit (wheel) url;
      sha256 = lib.removePrefix "sha256:" wheel.hash;
    }
  ) dependencies;
in
pkgs.runCommand "omv-protondrive-vm-tools"
  {
    nativeBuildInputs = [
      pkgs.gnutar
      pkgs.unzip
    ];
  }
  ''
    mkdir -p "$out" "$TMPDIR/omv-protondrive-vm"
    cp -r ${src}/tests/integration/. "$TMPDIR/omv-protondrive-vm/"
    cp ${src}/tools/*.py "$TMPDIR/omv-protondrive-vm/"
    # Ship the locked portable dependencies beside the guest scripts. Do not copy
    # a host virtualenv or install packages into Debian's system Python.
    ${lib.concatMapStringsSep "\n" (
      wheel: ''unzip -q -o ${wheel} -d "$TMPDIR/omv-protondrive-vm"''
    ) wheels}
    export PYTHONPATH="${src}/src:$TMPDIR/omv-protondrive-vm"
    export PYTHONDONTWRITEBYTECODE=1
    ${pkgs.python311}/bin/python "$TMPDIR/omv-protondrive-vm/owned_restore_guest.py" --help > /dev/null
    chmod u+w "$TMPDIR/omv-protondrive-vm/ui-cancel-bin"
    chmod +x "$TMPDIR/omv-protondrive-vm/ui-cancel-bin/tar.sh"
    ln -s tar.sh "$TMPDIR/omv-protondrive-vm/ui-cancel-bin/tar"
    install -m 0755 ${pkgs.pkgsStatic.just}/bin/just "$TMPDIR/omv-protondrive-vm/just"
    tar -C "$TMPDIR" -cf "$out/omv-protondrive-vm-tools.tar" omv-protondrive-vm
  ''
