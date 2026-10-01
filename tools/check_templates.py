"""Render Salt/templates with OMV-shaped fixtures; validate workbench wiring."""

import configparser
import json
from pathlib import Path

import yaml
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
components = set()
routes = []
for path in workbench.rglob('*.yaml'):
    value = yaml.safe_load(path.read_text())
    assert value['version'] == '1.0'
    if value['type'] == 'component':
        components.add(value['data']['name'])
    if value['type'] == 'route':
        routes.append(value['data'])
assert all(route['component'] in components for route in routes)
for path in MODELS.glob('*.json'):
    json.loads(path.read_text())
success('Salt, unit templates, datamodel JSON, and workbench references validated')
