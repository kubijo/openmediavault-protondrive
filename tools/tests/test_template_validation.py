"""Consumer rendering checks must reject invalid output, independently of salt-lint."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures/templates'


class TemplateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in ('src/salt', 'src/omv/datamodels', 'src/omv/workbench'):
            shutil.copytree(ROOT / directory, self.root / directory)
        (self.root / 'tools').mkdir()
        for name in ('check_templates.py', 'console.py'):
            shutil.copy2(ROOT / 'tools' / name, self.root / 'tools' / name)

    def render(self):
        return subprocess.run(
            [sys.executable, str(self.root / 'tools/check_templates.py')],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_output_types_with_control_blocks_and_whitespace_operators(self):
        shutil.copytree(FIXTURES / 'valid', self.root / 'src/salt', dirs_exist_ok=True)
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_rejected(self, fixture):
        shutil.copy2(FIXTURES / 'invalid' / fixture, self.root / 'src/salt' / fixture)
        result = self.render()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_yaml(self):
        self.assert_rejected('broken.sls')

    def test_invalid_json(self):
        self.assert_rejected('broken.json.j2')

    def test_invalid_unit_header(self):
        self.assert_rejected('broken-header.service.j2')

    def test_missing_service_section(self):
        self.assert_rejected('missing-section.service.j2')

    def test_undefined_variable(self):
        self.assert_rejected('undefined-variable.service.j2')


if __name__ == '__main__':
    unittest.main()
