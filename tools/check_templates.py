"""Render Salt/templates with OMV-shaped fixtures; validate workbench wiring."""

import configparser
import json
from pathlib import Path

import yaml
from build_workbench import load_document
from console import success
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / 'src/omv/datamodels'
settings = json.loads((MODELS / 'conf.service.protondrive.json').read_text())
assert (
    settings['properties']['sets']['properties']['set']['items']['properties']
    == json.loads((MODELS / 'conf.service.protondrive.set.json').read_text())['properties']
)
config = {key: value.get('default', '') for key, value in settings['properties'].items()}
config['instanceuuid'] = 'a0000000-0000-4000-8000-000000000001'
item = {
    key: value.get('default', '')
    for key, value in json.loads((MODELS / 'conf.service.protondrive.set.json').read_text())['properties'].items()
}
item.update(uuid='b0000000-0000-4000-8000-000000000001', name='appData', paths='/data/appData', stopcontainers=True)
env = Environment(undefined=StrictUndefined)
env.filters.update(json=json.dumps, to_bool=lambda v: v in (True, 1, '1', 'true'))
directory = ROOT / 'src/salt'
for enabled in (False, True):
    config['enable'] = enabled
    salt = {
        'omv_utils.register_jinja_filters': lambda: None,
        'omv_conf.get': lambda model: [item] if model.endswith('.set') else config,
        'pillar.get': lambda name, default: default,
    }
    for path in directory.rglob('*'):
        if path.suffix not in ('.sls', '.j2', '.jinja'):
            continue
        output = env.from_string(path.read_text()).render(
            config=config, sets=[item], salt=salt, tpldir='omv/deploy/protondrive'
        )
        if path.suffix == '.sls':
            value = yaml.safe_load(output)
            if not isinstance(value, dict):
                raise ValueError(f'Invalid Salt state: {path}')
        elif path.stem.endswith('.json'):
            assert json.loads(output)['sets'][0]['name'] == 'appData'
        elif path.stem.endswith(('.service', '.timer')):
            unit = configparser.RawConfigParser(strict=False)
            unit.read_string(output)
            assert unit.has_section('Unit')
            assert unit.has_section('Timer' if path.stem.endswith('.timer') else 'Service')
            if path.stem.endswith('.timer'):
                assert 'Persistent=false' in output and '03:00:00' in output
workbench = ROOT / 'src/omv/workbench'
components = {}
routes = []
navigation = []
for path in workbench.rglob('*.yaml'):
    value = load_document(path, ROOT / 'src/web/templates')
    assert value['version'] == '1.0'
    if value['type'] == 'component':
        components[value['data']['name']] = value['data']
    if value['type'] == 'route':
        routes.append(value['data'])
    if value['type'] == 'navigation-item':
        navigation.append(value['data'])
assert all(route['component'] in components for route in routes)
for route in routes:
    component = components[route['component']]
    if component['type'] == 'navigationPage':
        parents = [item['path'] for item in navigation if item.get('url') == route['url']]
        assert any(item['path'].startswith(parent + '.') for parent in parents for item in navigation), (
            f'Navigation page requires child entries at its URL: {route["url"]}'
        )
    if component['type'] != 'formPage' or not component['config'].get('request', {}).get('get'):
        continue
    # OMV 7 drops editing metadata when a component route becomes a parent.
    # Such a form renders but never executes its get request or starts polling.
    assert not any(other['url'].startswith(route['url'] + '/') for other in routes), (
        f'Data-loading form must use a leaf route: {route["url"]}'
    )
    if ':' not in route['url'] and not route['url'].endswith('/create'):
        assert route.get('editing'), f'Data-loading form requires editing: true: {route["url"]}'
for path in MODELS.glob('*.json'):
    json.loads(path.read_text())
success('Salt, unit templates, datamodel JSON, and workbench references validated')
