import unittest

from helpers import configuration
from protondrive.common import BackupError, locked
from protondrive.config import validate


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config, _ = configuration()
        self.assertEqual(validate(config)['schedulehour'], 3)
        self.assertEqual(config['remotepath'], '/my-files/open-media-vault-proton-backup')

    def test_remote_destination_is_one_user_chosen_root_folder(self):
        config, _ = configuration()
        config['remotepath'] = '/my-files/my own backups'
        self.assertEqual(validate(config)['remotepath'], config['remotepath'])
        for path in (
            '/my-files',
            '/my-files/other/nested',
            '/my-files/../other',
            '/my-files/.',
            '/my-files/-unsafe',
            '/trash/other',
        ):
            config['remotepath'] = path
            with self.subTest(path=path), self.assertRaises(BackupError):
                validate(config)

    def test_overlap_and_traversal(self):
        for source in ('/', '/data', '/data/.omv-protondrive/x', '/etc/../data'):
            config, item = configuration()
            item['paths'] = source
            with self.subTest(source=source), self.assertRaises(BackupError):
                validate(config)

    def test_literal_exclusion(self):
        config, item = configuration()
        item['excludes'] = 'cache\nlogs\nimmich/cache'
        validate(config)
        item['excludes'] = '../outside'
        with self.assertRaises(BackupError):
            validate(config)

    def test_retention_must_keep_one(self):
        config, item = configuration()
        item['remotekeep'] = 0
        with self.assertRaises(BackupError):
            validate(config)

    def test_lock_rejects_overlap(self):
        import tempfile

        with (
            tempfile.NamedTemporaryFile() as file,
            locked(file.name),
            self.assertRaises(BackupError),
            locked(file.name),
        ):
            pass

    def test_no_mutation_of_source(self):
        config, item = configuration()
        raw_item: dict[str, object] = {**item, 'enable': '1'}
        raw_config: dict[str, object] = {**config, 'sets': [raw_item]}
        self.assertIs(validate(raw_config)['sets'][0]['enable'], True)
        self.assertEqual(raw_item['enable'], '1')
