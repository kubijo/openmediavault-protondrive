"""Assemble external HTML/Nunjucks templates into OMV's native YAML manifests."""

import argparse
from pathlib import Path
from typing import cast

import yaml
from jinja2 import Environment

from tool_data import is_list, is_mapping, mapping, string


def load_document(path: Path, templates: Path) -> dict[str, object]:
    """Resolve textFile references without evaluating browser-side expressions."""
    root = templates.resolve()

    def expand(value: object) -> object:
        if is_list(value):
            return [expand(item) for item in value]
        if not is_mapping(value):
            return value
        result = {key: expand(item) for key, item in value.items() if key != 'textFile'}
        if 'textFile' in value:
            if 'text' in value:
                raise ValueError(f'{path}: text and textFile are mutually exclusive')
            reference = Path(string(value['textFile']))
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

    return mapping(expand(cast(object, yaml.safe_load(path.read_text()))))


def build(source: Path, templates: Path, destination: Path) -> None:
    for path in sorted(source.rglob('*.yaml')):
        document = load_document(path, templates)
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=120))


class Arguments(argparse.Namespace):
    source: Path
    templates: Path
    destination: Path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('templates', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args(namespace=Arguments())
    build(args.source, args.templates, args.destination)
