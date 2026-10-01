import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def configuration():
    directory = ROOT / 'src/omv/datamodels'

    def defaults(name):
        return {k: v.get('default', '') for k, v in json.loads((directory / name).read_text())['properties'].items()}

    config = defaults('conf.service.protondrive.json')
    item = defaults('conf.service.protondrive.set.json')
    config['instanceuuid'] = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    item.update(uuid='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', name='appData', paths='/data/appData')
    config['sets'] = [item]
    return config, item
