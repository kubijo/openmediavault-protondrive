"""Lock Debian 12 compatible wheels from the API's uv dependency lock."""

import argparse
import json
import subprocess
import tomllib
from pathlib import Path
from typing import cast

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import parse_wheel_filename

from protondrive.json_data import object_value, string, validate


def resolve(project: Path) -> list[dict[str, str]]:
    lock = object_value(validate(cast(object, tomllib.loads((project / 'uv.lock').read_text()))))
    packages = lock['package']
    if not isinstance(packages, list):
        raise TypeError('Invalid uv package list')
    requirements = subprocess.run(
        ['uv', 'export', '--project', str(project), '--frozen', '--no-dev', '--no-emit-project', '--no-hashes'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    environment: dict[str, str] = {key: str(value) for key, value in default_environment().items()}
    environment.update(
        python_version='3.11',
        python_full_version='3.11.0',
        implementation_version='3.11.0',
        implementation_name='cpython',
        platform_python_implementation='CPython',
        sys_platform='linux',
        platform_machine='x86_64',
        platform_system='Linux',
        os_name='posix',
    )
    platforms = [f'manylinux_2_{minor}_x86_64' for minor in range(36, 4, -1)]
    platforms += ['manylinux2014_x86_64', 'manylinux2010_x86_64', 'manylinux1_x86_64']
    tags = list(cpython_tags((3, 11), abis=['cp311'], platforms=platforms))
    tags += list(compatible_tags((3, 11), interpreter='cp311', platforms=platforms))
    priority = {tag: index for index, tag in enumerate(tags)}
    result: list[dict[str, str]] = []
    for line in requirements.splitlines():
        if not line or line.lstrip().startswith('#'):
            continue
        requirement = Requirement(line)
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        package = next(
            object_value(item)
            for item in packages
            if object_value(item)['name'] == requirement.name
            and string(object_value(item)['version']) in requirement.specifier
        )
        wheels = package['wheels']
        if not isinstance(wheels, list):
            raise TypeError(f'No wheels for {requirement.name}')
        candidates: list[tuple[int, dict[str, str]]] = []
        for entry in wheels:
            wheel = object_value(entry)
            url = string(wheel['url'])
            filename = url.rsplit('/', 1)[-1]
            _, _, _, wheel_tags = parse_wheel_filename(filename)
            ranks = [priority[tag] for tag in wheel_tags if tag in priority]
            if ranks:
                candidates.append((min(ranks), {'name': filename, 'url': url, 'hash': string(wheel['hash'])}))
        if not candidates:
            raise ValueError(f'No Debian 12 / CPython 3.11 wheel for {requirement.name}')
        result.append(min(candidates, key=lambda item: item[0])[1])
    return result


class Arguments(argparse.Namespace):
    check: bool = False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    options = parser.parse_args(namespace=Arguments())
    project = Path('src/api')
    rendered = json.dumps(resolve(project), indent=2) + '\n'
    output = project / 'runtime-wheels.json'
    if options.check:
        if output.read_text() != rendered:
            raise SystemExit('API runtime wheels are stale; run tools/api_wheels.py')
    else:
        output.write_text(rendered)


if __name__ == '__main__':
    main()
