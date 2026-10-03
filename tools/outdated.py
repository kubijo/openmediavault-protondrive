"""Consumer policy adapters for nix-tools' outdated report; never an aggregate checker."""

import functools
import json
import re
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import tyro
import yaml
from console import child_environment
from qa_report import Result


@dataclass
class Options:
    root: Path


@functools.lru_cache(maxsize=256)
def github(endpoint):
    result = subprocess.run(
        ['gh', 'api', endpoint],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
        env=child_environment(),
    )
    return json.loads(result.stdout)


@functools.lru_cache(maxsize=256)
def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'omv-protondrive-outdated/1'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode()


def stable_version(value):
    match = re.fullmatch(r'v?(\d+(?:\.\d+)+)', value)
    return tuple(map(int, match[1].split('.'))) if match else None


def compare(name, current, latest, detail=''):
    if not isinstance(current, str) or not current or not isinstance(latest, str) or not latest:
        raise ValueError('Missing source version')
    state = 'up-to-date' if current.removeprefix('v') == latest.removeprefix('v') else 'outdated'
    before, after = stable_version(current), stable_version(latest)
    if before and after:
        state = 'ahead' if before > after else ('up-to-date' if before == after else 'outdated')
    return Result(name, state, current, latest, detail)


def proton_version(source):
    prefix, artifact = source['url'].rsplit(f'/{source["version"]}/', 1)
    versions = set(
        re.findall(
            re.escape(prefix) + r'/([\d.]+)/' + re.escape(artifact),
            fetch(source['updates']['url']),
        )
    )
    if len(versions) != 1:
        raise ValueError('Proton download index did not identify exactly one matching artifact release')
    return versions.pop()


def debian_version(source):
    data = json.loads(fetch(source['updates']['url']))
    versions = {item['data']['info']['version'] for item in data['items'] if 'info' in item.get('data', {})}
    if len(versions) != 1:
        raise ValueError('Debian metadata did not identify exactly one image version')
    return versions.pop()


def container_digest(source):
    data = json.loads(fetch(f'{source["updates"]["url"]}/{quote(source["tag"], safe="")}'))
    return data['digest']


def external_source(name, source):
    providers = {'proton-cli': proton_version, 'debian-cloud': debian_version, 'docker-hub': container_digest}
    provider = source['updates']['provider']
    if provider not in providers:
        return Result(name, 'unknown', detail=f'Unsupported application source provider: {provider}')
    latest = providers[provider](source)
    return compare(name, source.get('version', source.get('digest')), latest, source['updates']['reason'])


def runner(current):
    if not re.fullmatch(r'ubuntu-\d{2}\.04', current):
        return Result('GitHub runner', 'unknown', current, detail='No stable Ubuntu x64 runner policy for this label')
    # Release-backed x64 Ubuntu labels only, never beta images or another architecture.
    releases = github('repos/actions/runner-images/releases?per_page=100')
    labels = set()
    for release in releases:
        match = re.match(r'ubuntu(\d{2})/', release['tag_name'])
        if match and not release['prerelease'] and not release['draft']:
            labels.add(f'ubuntu-{match[1]}.04')
    if not labels:
        raise ValueError('No stable Ubuntu x64 runner releases found')
    return compare('GitHub runner', current, max(labels))


def guarded(name, callback):
    try:
        return callback()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # URLs and native stderr may contain credentials; never echo them.
        return Result(name, 'error', detail=f'Lookup failed ({type(exc).__name__}); sensitive diagnostics withheld')


def application(root):
    sources = json.loads((root / 'config/sources.json').read_text())
    if not isinstance(sources, dict) or not sources:
        raise ValueError('Missing application source inventory')
    jobs = [(name, functools.partial(external_source, name, source)) for name, source in sources.items()]
    workflows = sorted((root / '.github/workflows').glob('*.y*ml'))
    if not workflows:
        raise ValueError('Missing workflow inventory for runner policy')
    labels = set()
    for path in workflows:
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for job in workflow['jobs'].values():
            if 'runs-on' in job:
                label = job['runs-on']
                labels.add(label if isinstance(label, str) else json.dumps(label, sort_keys=True))
    jobs.extend(('GitHub runner', functools.partial(runner, label)) for label in sorted(labels))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda job: guarded(*job), jobs))
    results.append(
        Result(
            'NAS runtime',
            'pinned',
            'Debian 12 / OMV 7 / Python 3.11',
            detail='Compatibility target: Debian-maintained Python 3.11. Tooling Python is reported separately.',
        )
    )
    return results


def main(options: Options):
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
                    {field: getattr(result, field) for field in ('name', 'state', 'current', 'latest', 'detail')}
                    for result in results
                ],
            }
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main(tyro.cli(Options)))
