import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import configuration

from protondrive.common import BackupError
from protondrive.retention import metadata, prune_remote
from protondrive.runner import prune_local


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.config, self.item = configuration()

    def value(self, number):
        timestamp = f'202610{number:02}T0300Z'
        return {
            'format': 1,
            'instanceuuid': self.config['instanceuuid'],
            'setuuid': self.item['uuid'],
            'timestamp': timestamp,
            'archive': f'appData-{timestamp}.tar.zst',
            'size': 20,
            'sha256': 'a' * 64,
        }

    def test_only_old_complete_pairs_are_removed(self):
        self.item['remotekeep'] = 2
        values = [self.value(i) for i in (1, 2, 3)]
        listing = []
        for value in values:
            listing.extend(
                [
                    {'name': value['archive'], 'uid': value['archive'], 'type': 'file', 'size': 20},
                    {
                        'name': value['archive'] + '.manifest.json',
                        'uid': value['archive'] + 'm',
                        'type': 'file',
                        'size': 300,
                    },
                ]
            )
        listing.append({'name': 'personal.txt', 'uid': 'personal', 'type': 'file', 'size': 10})
        cli = Mock()
        cli.list.return_value = listing
        with patch('protondrive.retention.read_remote_manifest', side_effect=values):
            prune_remote(cli, self.config, self.item, '/my-files/test')
        self.assertEqual(cli.purge.call_count, 2)
        self.assertEqual(cli.purge.call_args_list[0].args[1]['name'], values[0]['archive'])

    def test_foreign_manifest_prevents_any_cleanup(self):
        value = self.value(1)
        value['instanceuuid'] = 'another-instance'
        with self.assertRaises(BackupError):
            metadata(value, self.config, self.item, value['archive'])

    def test_unconfirmed_local_archives_survive_retention(self):
        from protondrive.config import remote_folder

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.item['localkeep'] = 1
            for number in (1, 2, 3):
                value = self.value(number)
                archive = directory / value['archive']
                archive.write_bytes(b'archive')
                archive.with_name(archive.name + '.manifest.json').write_text(json.dumps(value))
                if number != 1:
                    archive.with_name(archive.name + '.uploaded.json').write_text(
                        json.dumps({'folder': remote_folder(self.config, self.item), 'sha256': value['sha256']})
                    )
            prune_local(self.config, self.item, directory)
            self.assertTrue((directory / self.value(1)['archive']).exists())
            self.assertFalse((directory / self.value(2)['archive']).exists())
            self.assertTrue((directory / self.value(3)['archive']).exists())
