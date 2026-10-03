"""Check packaged manifest assembly and the overview's dynamic content."""

import tempfile
import unittest
from pathlib import Path

import yaml
from build_workbench import build, load_document
from jinja2 import Environment, StrictUndefined, TemplateSyntaxError

ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / 'src/omv/workbench'
TEMPLATES = ROOT / 'src/web/templates'
OVERVIEW = Path('component.d/omv-services-protondrive-status-form-page.yaml')


class WorkbenchBuildTests(unittest.TestCase):
    def setUp(self):
        self.config = load_document(WORKBENCH / OVERVIEW, TEMPLATES)['data']['config']
        self.cards = {field['name']: field['text'] for field in self.config['fields'] if field['type'] == 'card'}
        self.environment = Environment(undefined=StrictUndefined)

    def test_package_contains_native_fields_and_preserves_rpc_and_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build(WORKBENCH, TEMPLATES, output)
            source = yaml.safe_load((WORKBENCH / OVERVIEW).read_text())['data']['config']
            packaged = yaml.safe_load((output / OVERVIEW).read_text())['data']['config']
        self.assertEqual(packaged['buttons'], source['buttons'])
        self.assertEqual(packaged['request'], source['request'])
        self.assertEqual(packaged['autoReload'], source['autoReload'])
        for field in packaged['fields']:
            self.assertNotIn('textFile', field)
            if field['type'] == 'card':
                self.assertNotIn('\n', field['text'])
        self.assertIn('{{ accountemail | escape }}', self.cards['accountstatus'])

    def account(self, state, **values):
        context = {'authstate': state, 'accountemail': '', 'accountorganization': '', 'authurl': '', 'autherror': ''}
        context.update(values)
        return self.environment.from_string(self.cards['accountstatus']).render(context)

    def test_account_states_and_missing_identity(self):
        for state, message in (
            ('loading', 'Loading account status'),
            ('signed-out', 'Not signed in'),
            ('signing-in', 'Preparing the Proton sign-in link'),
            ('signed-in', 'Account details are unavailable'),
            ('error', 'Sign-in failed'),
            ('unavailable', 'Account status unavailable'),
        ):
            with self.subTest(state=state):
                self.assertIn(message, self.account(state))

    def test_account_identity_errors_and_link_are_escaped(self):
        identity = {'accountemail': 'test@example.com', 'accountorganization': '<script>unsafe</script>'}
        signed_in = self.account('signed-in', **identity)
        self.assertIn('test@example.com', signed_in)
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;', signed_in)
        self.assertNotIn('test@example.com', self.account('signed-out', **identity))
        self.assertIn('&lt;failure&gt;', self.account('error', autherror='<failure>'))
        signing_in = self.account('signing-in', authurl='https://account.proton.me/?a=1&b=2')
        self.assertIn('href="https://account.proton.me/?a=1&amp;b=2"', signing_in)

    def test_backup_details_keep_runtime_line_breaks(self):
        output = self.environment.from_string(self.cards['backupstatus']).render(
            authstate='signed-in', running=True, phase='uploading', lastsuccess='', error='', details='one\n<two>'
        )
        self.assertIn('<strong>Running</strong>', output)
        self.assertIn('uploading', output)
        self.assertIn('No successful backup yet', output)
        self.assertIn('<pre>one\n&lt;two&gt;</pre>', output)

    def test_invalid_references_and_syntax_fail_assembly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'component.yaml'
            for reference, error in (
                ('missing.html.njk', FileNotFoundError),
                ('../outside.html.njk', ValueError),
                ('/outside.html.njk', ValueError),
            ):
                with self.subTest(reference=reference):
                    manifest.write_text(yaml.safe_dump({'textFile': reference}))
                    with self.assertRaises(error):
                        load_document(manifest, root)
            manifest.write_text('text: inline\ntextFile: unused.html.njk\n')
            with self.assertRaisesRegex(ValueError, 'mutually exclusive'):
                load_document(manifest, root)
            (root / 'broken.html.njk').write_text('{% if %}')
            manifest.write_text('textFile: broken.html.njk\n')
            with self.assertRaises(TemplateSyntaxError):
                load_document(manifest, root)


if __name__ == '__main__':
    unittest.main()
