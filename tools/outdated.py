"""Read-only inventory of upstream releases, locked inputs, and effective tools."""

import functools
import json
import re
import subprocess
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from itertools import zip_longest
from pathlib import Path
from urllib.parse import quote

import tomllib
import tyro
import yaml
from console import child_environment
from qa_report import Result, SectionReport, report


@dataclass
class Options:
    inventory: Path
    """Nix-generated inventory of the effective tooling environment."""
    root: Path = Path('.')
    json: bool = False
    no_color: bool = False
    in_clanker: bool = False


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
    match = re.fullmatch(r'(?:v|php-)?(\d+(?:\.\d+)+)', value)
    return tuple(map(int, match[1].split('.'))) if match else None


def compare(name, current, latest, detail=''):
    state = 'up-to-date' if current.removeprefix('v') == latest.removeprefix('v') else 'outdated'
    before, after = stable_version(current), stable_version(latest)
    if before and after:
        state = 'ahead' if before > after else ('up-to-date' if before == after else 'outdated')
    if '-unstable-' in current:
        baseline = stable_version(current.split('-unstable-')[0])
        if baseline and after and baseline >= after:
            state = 'ahead'
    return Result(name, state, current, latest, detail)


def latest_release(repo):
    try:
        release = github(f'repos/{repo}/releases/latest')
    except subprocess.CalledProcessError as exc:
        if '(HTTP 404)' not in exc.stderr:
            raise
        return latest_tag(repo)
    if release.get('draft') or release.get('prerelease'):
        raise ValueError(f'{repo} returned a non-stable release')
    return release['tag_name']


def latest_tag(repo):
    tags = github(f'repos/{repo}/tags?per_page=100')
    stable = [tag['name'] for tag in tags if stable_version(tag['name'])]
    if not stable:
        raise ValueError(f'No stable versions found for {repo}')
    return max(stable, key=stable_version)


def tool(name, version, repo, tags=False):
    latest = latest_tag(repo) if tags else latest_release(repo)
    return compare(name, version, latest, 'Tool versions come from the effective Nix package set.')


def flake_input(name, node):
    locked, original = node['locked'], node['original']
    if locked['type'] != 'github':
        return Result(name, 'unknown', detail=f'Unsupported lock source: {locked["type"]}')
    repo = f'{locked["owner"]}/{locked["repo"]}'
    ref = original.get('ref', 'HEAD')
    if stable_version(ref):
        ref = latest_release(repo)
    commit = github(f'repos/{repo}/commits/{quote(ref, safe="")}')['sha']
    result = compare(name, locked['rev'], commit, f'{repo}: {ref}')
    if original.get('rev') and result.state == 'outdated':
        result.state = 'pinned'
        result.detail += '; explicit upstream source snapshot; review compatibility with its owning flake.'
    return result


def python_package(package):
    name = package['name']
    data = json.loads(fetch(f'https://pypi.org/pypi/{quote(name, safe="")}/json'))
    latest = data['info']['version']
    if not stable_version(latest):
        stable = [version for version in data['releases'] if stable_version(version)]
        if not stable:
            raise ValueError(f'No stable version found for {name}')
        latest = max(stable, key=stable_version)
    return compare(
        f'python dependency: {name}',
        package['version'],
        latest,
        f'Latest upstream Python requirement: {data["info"].get("requires_python") or "unspecified"}. '
        'Compatibility must be resolved before upgrading.',
    )


def action(repository, current):
    tag = latest_release(repository)
    before = github(f'repos/{repository}/commits/{quote(current, safe="")}')['sha']
    commit = github(f'repos/{repository}/commits/{quote(tag, safe="")}')['sha']
    state = 'up-to-date' if before == commit else 'outdated'
    return Result(f'action: {repository}', state, current, tag)


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
    latest = providers[source['updates']['provider']](source)
    return compare(name, source.get('version', source.get('digest')), latest, source['updates']['reason'])


def runner(current):
    # Check release-backed x64 Ubuntu labels; do not silently follow beta images or another architecture.
    releases = github('repos/actions/runner-images/releases?per_page=100')
    labels = set()
    for release in releases:
        match = re.match(r'ubuntu(\d{2})/', release['tag_name'])
        if match and not release['prerelease'] and not release['draft']:
            labels.add(f'ubuntu-{match[1]}.04')
    if not labels:
        raise ValueError('No stable Ubuntu x64 runner releases found')
    return compare('GitHub runner', current, max(labels))


def host_nix():
    output = subprocess.check_output(['nix', '--version'], text=True, timeout=15, env=child_environment()).strip()
    match = re.search(r'(\d+\.\d+(?:\.\d+)?)$', output)
    if not match:
        raise ValueError('Unrecognized host Nix version')
    return compare('Nix client on PATH', match[1], latest_release('NixOS/nix'), output)


def guarded(name, callback, domain='Report'):
    try:
        return replace(callback(), domain=domain)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # stderr from external clients can include request metadata; report the failure without echoing it.
        return Result(name, 'error', detail=f'Lookup failed: {type(exc).__name__}: {exc}', domain=domain)


def inventory(root, effective):
    lock = json.loads((root / 'flake.lock').read_text())
    python_lock = tomllib.loads((root / 'uv.lock').read_text())
    sources = json.loads((root / 'config/sources.json').read_text())
    jobs = [('Nix client on PATH', host_nix, 'Toolchain')]
    for name, item in effective['tools'].items():
        jobs.append((name, functools.partial(tool, name, **item), 'Toolchain'))
    for name, node in lock['nodes'].items():
        if 'locked' in node:
            label = f'flake input: {name}'
            jobs.append((label, functools.partial(flake_input, label, node), 'Nix inputs'))
    for package in python_lock['package']:
        if 'registry' in package.get('source', {}):
            jobs.append(
                (
                    f'python dependency: {package["name"]}',
                    functools.partial(python_package, package),
                    'Python dependencies',
                )
            )
    actions, runners = set(), set()
    for path in sorted((root / '.github/workflows').glob('*.y*ml')):
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for job in workflow['jobs'].values():
            if isinstance(job.get('runs-on'), str) and job['runs-on'].startswith('ubuntu-'):
                runners.add(job['runs-on'])
            for step in job.get('steps', []):
                if 'uses' in step:
                    repository, ref = step['uses'].rsplit('@', 1)
                    actions.add((repository, ref))
    for repo, ref in sorted(actions):
        jobs.append((f'action: {repo}', functools.partial(action, repo, ref), 'GitHub CI'))
    for label in sorted(runners):
        jobs.append(('GitHub runner', functools.partial(runner, label), 'GitHub CI'))
    for name, source in sources.items():
        jobs.append((name, functools.partial(external_source, name, source), 'External sources'))
    results = [
        Result(
            'NAS runtime',
            'compatibility-pinned',
            'Debian 12 / OMV 7 / Python 3.11',
            detail='The installed plugin targets Debian-maintained Python 3.11. Tooling Python is reported separately.',
            domain='Runtime compatibility',
        )
    ]
    if not effective['nix-tools-revision']:
        results.append(
            Result(
                'effective nix-tools source',
                'blocked',
                'local override',
                detail='A local nix-tools override is active; its revision cannot be verified against the lockfile.',
                domain='Nix inputs',
            )
        )
    return jobs, results


def collect(jobs, fixed, output):
    by_domain = {}
    for index, job in enumerate(jobs):
        by_domain.setdefault(job[2], []).append(index)
    for result in fixed:
        by_domain.setdefault(result.domain, [])
    remaining = {domain: len(indices) for domain, indices in by_domain.items()}
    results = {}

    def complete(domain):
        components = [results[index] for index in by_domain[domain]]
        components.extend(result for result in fixed if result.domain == domain)
        output.section_ready(domain, components)

    for domain, count in remaining.items():
        if not count:
            complete(domain)
    # Interleave domains so a large toolchain cannot monopolize the worker queue.
    order = [index for batch in zip_longest(*by_domain.values()) for index in batch if index is not None]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(guarded, *jobs[index]): index for index in order}
        for future in as_completed(futures):
            index = futures[future]
            result = results[index] = future.result()
            output.advance(result.domain)
            remaining[result.domain] -= 1
            if not remaining[result.domain]:
                complete(result.domain)
    return [results[index] for index in range(len(jobs))] + fixed


def main(options: Options):
    try:
        jobs, fixed = inventory(options.root.resolve(), json.loads(options.inventory.read_text()))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return report(
            [Result('inventory', 'error', detail=str(exc), domain='Report')],
            json_output=options.json,
            no_color=options.no_color,
            in_clanker=options.in_clanker,
        )
    totals = Counter(job[2] for job in jobs)
    for result in fixed:
        totals.setdefault(result.domain, 0)
    with SectionReport(
        totals, json_output=options.json, no_color=options.no_color, in_clanker=options.in_clanker
    ) as output:
        results = collect(jobs, fixed, output)
    return output.finish(results)


if __name__ == '__main__':
    raise SystemExit(main(tyro.cli(Options)))
