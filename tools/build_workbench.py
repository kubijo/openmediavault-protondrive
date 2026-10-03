"""Assemble external HTML/Nunjucks templates into OMV's native YAML manifests."""

import argparse
from pathlib import Path

import yaml
from jinja2 import Environment


def load_document(path: Path, templates: Path):
    """Resolve textFile references without evaluating browser-side expressions."""
    root = templates.resolve()

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: expand(item) for key, item in value.items() if key != 'textFile'}
        if 'textFile' in value:
            if 'text' in value:
                raise ValueError(f'{path}: text and textFile are mutually exclusive')
            reference = Path(value['textFile'])
            template = (root / reference).resolve()
            if reference.is_absolute() or not template.is_relative_to(root):
                raise ValueError(f'{path}: textFile must stay inside {root}')
            source = template.read_text()
            # These templates use the common Jinja/Nunjucks syntax subset.
            Environment().parse(source, name=str(template))
            # OMV applies nl2br before inserting HTML. Source layout must not
            # become visible breaks; runtime values (including <pre>) stay intact.
            result['text'] = ' '.join(line.strip() for line in source.splitlines())
        return result

    return expand(yaml.safe_load(path.read_text()))


def build(source: Path, templates: Path, destination: Path):
    for path in sorted(source.rglob('*.yaml')):
        document = load_document(path, templates)
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=120))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('templates', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    build(args.source, args.templates, args.destination)
