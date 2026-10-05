"""Render Salt/templates with OMV-shaped fixtures; validate workbench wiring."""

import configparser
import json
from pathlib import Path
from typing import cast

import yaml
from jinja2 import Environment, StrictUndefined

from build_workbench import load_document
from console import success
from tool_data import decode, field, is_mapping, items, mapping, string

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / 'src/omv/datamodels'
settings = decode((MODELS / 'conf.service.protondrive.json').read_text())
assert (
    field(settings, 'properties', 'sets', 'properties', 'set', 'items', 'properties')
    == decode((MODELS / 'conf.service.protondrive.set.json').read_text())['properties']
)
config = {key: mapping(value).get('default', '') for key, value in mapping(settings['properties']).items()}
config['instanceuuid'] = 'a0000000-0000-4000-8000-000000000001'
item = {
    key: mapping(value).get('default', '')
    for key, value in mapping(decode((MODELS / 'conf.service.protondrive.set.json').read_text())['properties']).items()
}
item.update(uuid='b0000000-0000-4000-8000-000000000001', name='appData', paths='/data/appData', stopcontainers=True)
env = Environment(undefined=StrictUndefined)


def to_bool(value: object) -> bool:
    return value in (True, 1, '1', 'true')


def get_model(model: str) -> object:
    return [item] if model.endswith('.set') else config


def pillar_get(_name: str, default: object) -> object:
    return default


env.filters.update(json=json.dumps, to_bool=to_bool)
directory = ROOT / 'src/salt'
for enabled in (False, True):
    config['enable'] = enabled
    salt = {
        'omv_utils.register_jinja_filters': lambda: None,
        'omv_conf.get': get_model,
        'pillar.get': pillar_get,
    }
    for path in directory.rglob('*'):
        if path.suffix not in ('.sls', '.j2', '.jinja'):
            continue
        output = env.from_string(path.read_text()).render(
            config=config, sets=[item], salt=salt, tpldir='omv/deploy/protondrive'
        )
        if path.suffix == '.sls':
            value = cast(object, yaml.safe_load(output))
            if not is_mapping(value):
                raise ValueError(f'Invalid Salt state: {path}')
        elif path.stem.endswith('.json'):
            assert mapping(items(decode(output)['sets'])[0])['name'] == 'appData'
        elif path.stem.endswith(('.service', '.timer')):
            unit = configparser.RawConfigParser(strict=False)
            unit.read_string(output)
            assert unit.has_section('Unit')
            assert unit.has_section('Timer' if path.stem.endswith('.timer') else 'Service')
            if path.stem.endswith('.timer'):
                assert 'Persistent=false' in output and '03:00:00' in output
workbench = ROOT / 'src/omv/workbench'
components: dict[str, dict[str, object]] = {}
routes: list[dict[str, object]] = []
navigation: list[dict[str, object]] = []
for path in workbench.rglob('*.yaml'):
    value = load_document(path, ROOT / 'src/web/templates')
    assert value['version'] == '1.0'
    if value['type'] == 'component':
        components[string(field(value, 'data', 'name'))] = mapping(value['data'])
    if value['type'] == 'route':
        routes.append(mapping(value['data']))
    if value['type'] == 'navigation-item':
        navigation.append(mapping(value['data']))
assert all(route['component'] in components for route in routes)
for route in routes:
    component = components[string(route['component'])]
    if component['type'] == 'navigationPage':
        parents = [string(item['path']) for item in navigation if item.get('url') == route['url']]
        assert any(string(item['path']).startswith(parent + '.') for parent in parents for item in navigation), (
            f'Navigation page requires child entries at its URL: {route["url"]}'
        )
    if component['type'] != 'formPage' or not mapping(mapping(component['config']).get('request', {})).get('get'):
        continue
    # OMV 7 drops editing metadata when a component route becomes a parent.
    # Such a form renders but never executes its get request or starts polling.
    assert not any(string(other['url']).startswith(string(route['url']) + '/') for other in routes), (
        f'Data-loading form must use a leaf route: {route["url"]}'
    )
    if ':' not in string(route['url']) and not string(route['url']).endswith('/create'):
        assert route.get('editing'), f'Data-loading form requires editing: true: {route["url"]}'
for path in MODELS.glob('*.json'):
    json.loads(path.read_text())
success('Salt, unit templates, datamodel JSON, and workbench references validated')
