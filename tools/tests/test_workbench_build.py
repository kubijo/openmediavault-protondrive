"""Check packaged manifest assembly and the overview's dynamic content."""

import tempfile
import unittest
from pathlib import Path
from typing import cast

import yaml
from jinja2 import Environment, StrictUndefined, TemplateSyntaxError

from build_workbench import build, load_document
from tool_data import field, items, mapping, string

ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / 'src/omv/workbench'
TEMPLATES = ROOT / 'src/web/templates'
OVERVIEW = Path('component.d/omv-services-protondrive-status-form-page.yaml')


class WorkbenchBuildTests(unittest.TestCase):
    def setUp(self):
        self.config = mapping(field(load_document(WORKBENCH / OVERVIEW, TEMPLATES), 'data', 'config'))
        self.cards = {
            string(card['name']): string(card['text'])
            for raw in items(self.config['fields'])
            if (card := mapping(raw))['type'] == 'card'
        }
        self.environment = Environment(undefined=StrictUndefined)

    def test_package_contains_native_fields_and_preserves_rpc_and_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build(WORKBENCH, TEMPLATES, output)
            source = mapping(field(cast(object, yaml.safe_load((WORKBENCH / OVERVIEW).read_text())), 'data', 'config'))
            packaged = mapping(field(cast(object, yaml.safe_load((output / OVERVIEW).read_text())), 'data', 'config'))
        self.assertEqual(packaged['buttons'], source['buttons'])
        self.assertEqual(packaged['request'], source['request'])
        self.assertEqual(packaged['autoReload'], source['autoReload'])
        for raw in items(packaged['fields']):
            card = mapping(raw)
            self.assertNotIn('textFile', card)
            if card['type'] == 'card':
                self.assertNotIn('\n', string(card['text']))
        self.assertIn('{{ accountemail | escape }}', self.cards['accountstatus'])

    def account(self, state: str, **values: str) -> str:
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

    def test_current_item_is_escaped_and_only_shown_during_relevant_phases(self):
        template = self.environment.from_string(self.cards['backupstatus'])
        for running, phase, visible in (
            (True, 'archiving', True),
            (True, 'verifying', True),
            (True, 'uploading', True),
            (True, 'retention', True),
            (True, 'preflight', False),
            (False, 'completed', False),
        ):
            with self.subTest(running=running, phase=phase):
                output = template.render(
                    authstate='signed-in',
                    running=running,
                    phase=phase,
                    message='<archive>.tar.zst',
                    lastsuccess='',
                    error='',
                    details='',
                )
                self.assertEqual('Current item: &lt;archive&gt;.tar.zst' in output, visible)
                self.assertNotIn('<archive>', output)

    def test_transfer_replaces_stale_item_and_disappears_when_idle(self):
        template = self.environment.from_string(self.cards['backupstatus'])
        for running in (True, False):
            output = template.render(
                authstate='signed-in',
                running=running,
                phase='uploading',
                message='old-archive',
                transferphase='Verification download',
                transferfile='<archive>.tar.zst',
                transferelapsed=12,
                lastsuccess='',
                error='',
                details='',
            )
            self.assertEqual('class="protondrive-transfer-phase">Verification download</span>' in output, running)
            self.assertEqual('class="protondrive-transfer-elapsed">12</span>s elapsed' in output, running)
            self.assertNotIn('old-archive', output)
            self.assertNotIn('<archive>', output)

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
