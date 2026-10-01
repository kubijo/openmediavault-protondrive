import unittest
from unittest.mock import patch

from helpers import configuration

from protondrive.common import BackupError, locked
from protondrive.config import validate


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config, _ = configuration()
        self.assertEqual(validate(config)['schedulehour'], 3)

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
        item['enable'] = '1'
        with patch('pathlib.Path.resolve', autospec=True, side_effect=lambda p: p):
            self.assertIs(validate(config)['sets'][0]['enable'], True)
        self.assertEqual(item['enable'], '1')
