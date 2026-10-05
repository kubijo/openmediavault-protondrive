from pathlib import Path

from protondrive.config import validate
from protondrive.json_data import JSONValue, decode, object_value
from protondrive.models import BackupSet, Configuration

ROOT = Path(__file__).resolve().parents[2]


def configuration() -> tuple[Configuration, BackupSet]:
    directory = ROOT / 'src/omv/datamodels'

    def defaults(name: str) -> dict[str, JSONValue]:
        properties = object_value(object_value(decode((directory / name).read_text()))['properties'])
        return {key: object_value(value).get('default', '') for key, value in properties.items()}

    config = defaults('conf.service.protondrive.json')
    item = defaults('conf.service.protondrive.set.json')
    config['instanceuuid'] = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    item.update(uuid='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', name='appData', paths='/data/appData')
    config['sets'] = [item]
    checked = validate(config)
    return checked, checked['sets'][0]
