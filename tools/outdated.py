"""Consumer policy adapters for nix-tools' outdated report; never an aggregate checker."""

import functools
import json
import re
import subprocess
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import HTTPResponse
from pathlib import Path
from typing import cast
from urllib.parse import quote

import yaml

from cli_options import parse_options
from console import child_environment
from qa_report import Result
from tool_data import decode, decode_value, items, mapping, string


@dataclass
class Options:
    root: Path


@functools.lru_cache(maxsize=256)
def github(endpoint: str) -> list[dict[str, object]]:
    result = subprocess.run(
        ['gh', 'api', endpoint],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
        env=child_environment(),
    )
    return [mapping(value) for value in items(decode_value(result.stdout))]


@functools.lru_cache(maxsize=256)
def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={'User-Agent': 'omv-protondrive-outdated/1'})
    response = cast(object, urllib.request.urlopen(request, timeout=30))
    if not isinstance(response, HTTPResponse):
        raise TypeError('Expected an HTTP response')
    with response:
        return response.read().decode()


def stable_version(value: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r'v?(\d+(?:\.\d+)+)', value)
    return tuple(map(int, match[1].split('.'))) if match else None


def compare(name: str, current: object, latest: object, detail: str = '') -> Result:
    if not isinstance(current, str) or not current or not isinstance(latest, str) or not latest:
        raise ValueError('Missing source version')
    state = 'up-to-date' if current.removeprefix('v') == latest.removeprefix('v') else 'outdated'
    before, after = stable_version(current), stable_version(latest)
    if before and after:
        state = 'ahead' if before > after else ('up-to-date' if before == after else 'outdated')
    return Result(name, state, current, latest, detail)


def proton_version(source: Mapping[str, object]) -> str:
    prefix, artifact = string(source['url']).rsplit(f'/{source["version"]}/', 1)
    versions = {
        match[1]
        for match in re.finditer(
            re.escape(prefix) + r'/([\d.]+)/' + re.escape(artifact),
            fetch(string(mapping(source['updates'])['url'])),
        )
    }
    if len(versions) != 1:
        raise ValueError('Proton download index did not identify exactly one matching artifact release')
    return versions.pop()


def debian_version(source: Mapping[str, object]) -> str:
    data = decode(fetch(string(mapping(source['updates'])['url'])))
    records = [mapping(mapping(item).get('data', {})) for item in items(data['items'])]
    versions = {string(mapping(item['info'])['version']) for item in records if 'info' in item}
    if len(versions) != 1:
        raise ValueError('Debian metadata did not identify exactly one image version')
    return versions.pop()


def container_digest(source: Mapping[str, object]) -> str:
    url = string(mapping(source['updates'])['url'])
    data = decode(fetch(f'{url}/{quote(string(source["tag"]), safe="")}'))
    return string(data['digest'])


def external_source(name: str, source: Mapping[str, object]) -> Result:
    providers = {'proton-cli': proton_version, 'debian-cloud': debian_version, 'docker-hub': container_digest}
    updates = mapping(source['updates'])
    provider = string(updates['provider'])
    if provider not in providers:
        return Result(name, 'unknown', detail=f'Unsupported application source provider: {provider}')
    latest = providers[provider](source)
    return compare(name, source.get('version', source.get('digest')), latest, string(updates['reason']))


def runner(current: str) -> Result:
    if not re.fullmatch(r'ubuntu-\d{2}\.04', current):
        return Result('GitHub runner', 'unknown', current, detail='No stable Ubuntu x64 runner policy for this label')
    # Release-backed x64 Ubuntu labels only, never beta images or another architecture.
    releases = github('repos/actions/runner-images/releases?per_page=100')
    labels: set[str] = set()
    for release in releases:
        match = re.match(r'ubuntu(\d{2})/', string(release['tag_name']))
        if match and not release['prerelease'] and not release['draft']:
            labels.add(f'ubuntu-{match[1]}.04')
    if not labels:
        raise ValueError('No stable Ubuntu x64 runner releases found')
    return compare('GitHub runner', current, max(labels))


def guarded(name: str, callback: Callable[[], Result]) -> Result:
    try:
        return callback()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # URLs and native stderr may contain credentials; never echo them.
        return Result(name, 'error', detail=f'Lookup failed ({type(exc).__name__}); sensitive diagnostics withheld')


def application(root: Path) -> list[Result]:
    sources = decode((root / 'config/sources.json').read_text())
    if not sources:
        raise ValueError('Missing application source inventory')
    jobs: list[tuple[str, Callable[[], Result]]] = [
        (name, functools.partial(external_source, name, mapping(source))) for name, source in sources.items()
    ]
    workflows = sorted((root / '.github/workflows').glob('*.y*ml'))
    if not workflows:
        raise ValueError('Missing workflow inventory for runner policy')
    labels: set[str] = set()
    for path in workflows:
        workflow = mapping(cast(object, yaml.load(path.read_text(), Loader=yaml.BaseLoader)))
        for raw in mapping(workflow['jobs']).values():
            job = mapping(raw)
            if 'runs-on' in job:
                label = job['runs-on']
                labels.add(label if isinstance(label, str) else json.dumps(label, sort_keys=True))
    jobs.extend(('GitHub runner', functools.partial(runner, label)) for label in sorted(labels))

    def run_job(job: tuple[str, Callable[[], Result]]) -> Result:
        return guarded(*job)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run_job, jobs))
    results.append(
        Result(
            'NAS runtime',
            'pinned',
            'Debian 12 / OMV 7 / Python 3.11',
            detail='Compatibility target: Debian-maintained Python 3.11. Tooling Python is reported separately.',
        )
    )
    return results


def main(options: Options) -> int:
    try:
        results = application(options.root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        results = [Result('application inventory', 'error', detail=f'Invalid inventory ({type(exc).__name__})')]
    # Valid adapter documents always exit 0. nix-tools owns aggregate 0/1/2 semantics.
    print(
        json.dumps(
            {
                'schemaVersion': 1,
                'results': [
                    {
                        'name': result.name,
                        'state': result.state,
                        'current': result.current,
                        'latest': result.latest,
                        'detail': result.detail,
                    }
                    for result in results
                ],
            }
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main(parse_options(Options)))
