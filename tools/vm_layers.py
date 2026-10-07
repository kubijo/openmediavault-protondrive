"""Cache an installed plugin above the immutable, plugin-free OMV generation."""

from pathlib import Path

import vm_runtime
from process_output import ProcessOutput
from vm_cache import BaseCache, fingerprint

GUEST_TOOLS = '/usr/local/lib/omv-protondrive-vm'


def installed_inputs(base: Path, package: Path, bundle: Path) -> dict[str, str]:
    return {
        # Apt refreshes invalidate descendants even when the recipe is unchanged.
        'base_generation': str(base.resolve(strict=True)),
        'package': fingerprint(package),
        'guest_bundle': fingerprint(bundle),
        'builder': fingerprint(Path(__file__)),
    }


def build_installed(
    destination: Path, base: Path, options: vm_runtime.Options, bundle: Path, output: ProcessOutput
) -> None:
    with vm_runtime.boot(options, base, output, label='installed-') as guest:
        with output.stage('Install plugin in reusable regression layer'):
            guest.copy(options.package, bundle)
            guest.run('mkdir', '-p', GUEST_TOOLS)
            guest.run('tar', '-xf', f'/root/{bundle.name}', '-C', GUEST_TOOLS, '--strip-components=1')
            guest.python(
                output,
                f'{GUEST_TOOLS}/regression_guest.py',
                options.reports / 'installed-build.log',
                options.timeout,
                'install',
                '--package',
                f'/root/{options.package.name}',
            )
        with output.stage('Seal installed regression layer'):
            guest.python(
                output,
                f'{GUEST_TOOLS}/regression_guest.py',
                options.reports / 'installed-seal.log',
                120,
                'seal',
            )
            if guest.process is None or guest.process.wait(timeout=120) != 0:
                raise RuntimeError('Installed VM did not shut down cleanly; cache will not be published')
            vm_runtime.run('qemu-img', 'convert', '-O', 'qcow2', str(guest.directory / 'disk.qcow2'), str(destination))
            vm_runtime.run('qemu-img', 'check', str(destination))


def resolve_installed_base(base: Path, options: vm_runtime.Options, bundle: Path, output: ProcessOutput) -> Path:
    cache = BaseCache(options.cache_dir / 'installed', installed_inputs(base, options.package, bundle))
    with output.stage('Resolve installed regression layer'):
        image, built = cache.ensure(lambda destination: build_installed(destination, base, options, bundle, output))
        output.console.print(f'INSTALLED CACHE {"BUILT" if built else "HIT"}: {image}')
        return image
