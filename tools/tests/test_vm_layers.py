"""Installed-layer invalidation and publication without building or booting a VM."""

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import vm_layers
import vm_runtime
from process_output import ProcessOutput


class InstalledLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.root / 'base.qcow2'
        self.package = self.root / 'plugin.deb'
        self.bundle = self.root / 'tools.tar'
        for path in (self.base, self.package, self.bundle):
            path.write_bytes(path.name.encode())
        self.options = vm_runtime.Options(
            image=self.base,
            package=self.package,
            reports=self.root,
            cache_dir=self.root / 'cache',
        )
        self.output = ProcessOutput(in_clanker=True)

    def test_package_bundle_and_parent_generation_each_invalidate(self) -> None:
        original = vm_layers.installed_inputs(self.base, self.package, self.bundle)
        self.package.write_bytes(b'updated package')
        changed = vm_layers.installed_inputs(self.base, self.package, self.bundle)
        self.assertNotEqual(original, changed)
        self.bundle.write_bytes(b'updated tools')
        changed_again = vm_layers.installed_inputs(self.base, self.package, self.bundle)
        self.assertNotEqual(changed, changed_again)
        refreshed = self.root / 'refreshed.qcow2'
        refreshed.write_bytes(self.base.read_bytes())
        self.assertNotEqual(changed_again, vm_layers.installed_inputs(refreshed, self.package, self.bundle))

    def test_warm_install_layer_does_not_boot_a_builder(self) -> None:
        def build(destination: Path, *_: object) -> None:
            destination.write_bytes(b'sealed installed image')

        with patch.object(vm_layers, 'build_installed', side_effect=build) as builder:
            first = vm_layers.resolve_installed_base(self.base, self.options, self.bundle, self.output)
            second = vm_layers.resolve_installed_base(self.base, self.options, self.bundle, self.output)
        builder.assert_called_once()
        self.assertEqual(first, second)
        self.assertEqual(first.stat().st_mode & 0o222, 0)

    def test_failed_seal_shutdown_never_converts_or_publishes(self) -> None:
        guest = Mock(process=Mock(wait=Mock(return_value=1)))
        with (
            patch.object(vm_runtime, 'boot', return_value=contextlib.nullcontext(guest)),
            patch.object(vm_runtime, 'run') as run,
            self.assertRaisesRegex(RuntimeError, 'shut down cleanly'),
        ):
            vm_layers.resolve_installed_base(self.base, self.options, self.bundle, self.output)
        run.assert_not_called()
        self.assertFalse(list((self.root / 'cache').rglob('current.json')))


if __name__ == '__main__':
    unittest.main()
