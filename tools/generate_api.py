"""Generate local protobuf bindings, or verify them without changing the source."""

import argparse
import subprocess
import tempfile
from pathlib import Path

GENERATED = (
    Path('src/api/protondrive_api/v1'),
    Path('src/api/buf'),
    Path('src/web/app/src/generated'),
)


def files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for directory in GENERATED
        for path in (root / directory).rglob('*')
        if path.is_file() and path.suffix in ('.py', '.pyi', '.ts')
    }


def generate(check: bool) -> None:
    with tempfile.TemporaryDirectory(prefix='protondrive-bindings-') as temporary:
        output = Path(temporary)
        subprocess.run(['buf', 'lint'], check=True)
        subprocess.run(['buf', 'generate', '--output', str(output)], check=True)
        # Some pinned generators emit trailing spaces and blank final lines.
        # Normalize only whitespace here, identically for updates and drift checks.
        expected = {
            path: b'\n'.join(line.rstrip() for line in data.splitlines()).rstrip() + b'\n'
            for path, data in files(output).items()
        }
        current = files(Path('.'))
        changed = sorted(path for path in expected.keys() | current.keys() if expected.get(path) != current.get(path))
        if check and changed:
            raise SystemExit('Generated bindings are stale:\n' + '\n'.join(map(str, changed)))
        if not check:
            for path in changed:
                if path in expected:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(expected[path])
                else:
                    path.unlink()


class Arguments(argparse.Namespace):
    check: bool = False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    options = parser.parse_args(namespace=Arguments())
    generate(options.check)


if __name__ == '__main__':
    main()
