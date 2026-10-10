import contextlib
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console

import audit
import console
import outdated
import qa_report
from cli_options import parse_options
from qa_report import Result
from tool_data import decode, field, items, mapping


@dataclass
class CLIOptions:
    count: int = 1


class CLIOptionsTests(unittest.TestCase):
    def test_parses_real_tyro_options(self) -> None:
        with patch.object(sys, 'argv', ['fixture', '--count', '3']):
            self.assertEqual(parse_options(CLIOptions), CLIOptions(count=3))

    def test_wrong_parser_result_is_rejected_at_boundary(self) -> None:
        def malformed(schema: type[CLIOptions]) -> object:
            return {'count': 3}

        with self.assertRaises(TypeError):
            parse_options(CLIOptions, malformed)


class TerminalTests(unittest.TestCase):
    def test_crash_handler_preserves_failure_and_hides_locals_in_all_output_modes(self):
        secret = secrets.token_hex(24)
        cases: tuple[tuple[dict[str, str], dict[str, bool], bool], ...] = (
            ({'FORCE_COLOR': '1'}, {}, False),
            ({'FORCE_COLOR': '1', 'NO_COLOR': ''}, {}, True),
            ({'FORCE_COLOR': '1', 'TERM': 'dumb'}, {}, True),
            ({'IN_CLANKER': ''}, {}, True),
            ({'FORCE_COLOR': '1'}, {'in_clanker': True}, True),
            ({'FORCE_COLOR': '1'}, {'no_color': True}, True),
        )
        for environment, options, plain in cases:
            with self.subTest(environment=environment, options=options):
                script = (
                    'import os, sys, traceback\n'
                    'from console import install_traceback\n'
                    f'install_traceback(**{options!r})\n'
                    'print(sys.excepthook is traceback.print_exception)\n'
                    'credential = os.environ["TEST_SECRET"]\n'
                    'raise RuntimeError("crash probe")\n'
                )
                result = subprocess.run(
                    [sys.executable, '-c', script],
                    env={'PYTHONPATH': str(Path(console.__file__).parent), 'TEST_SECRET': secret, **environment},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout.strip(), str(plain))
                self.assertIn('RuntimeError', result.stderr)
                self.assertIn('crash probe', result.stderr)
                self.assertNotIn(secret, result.stderr)
                if plain:
                    self.assertNotIn('\x1b', result.stderr)

    def test_agent_detection_uses_presence_and_allows_explicit_color_override(self):
        for variable in console.AGENT_ENVS:
            with (
                self.subTest(variable=variable),
                patch.dict(os.environ, {variable: ''}, clear=True),
            ):
                self.assertTrue(console.plain_output())
                with patch.dict(os.environ, {'FORCE_COLOR': '1'}):
                    self.assertFalse(console.plain_output())

    def test_no_color_and_dumb_terminal_win_over_force(self):
        for environment in ({'NO_COLOR': ''}, {'TERM': 'dumb'}):
            with (
                self.subTest(environment=environment),
                patch.dict(os.environ, {'FORCE_COLOR': '1', **environment}, clear=True),
            ):
                self.assertTrue(console.color_disabled())

    def test_human_can_force_color_through_a_pipe(self):
        with patch.dict(os.environ, {'FORCE_COLOR': '1'}, clear=True), patch('sys.stdout.isatty', return_value=False):
            self.assertFalse(console.plain_output())
            self.assertTrue(console.color_disabled(no_color=True))
            self.assertTrue(console.plain_output(in_clanker=True))

    def test_agent_summary_is_plain_explicit_and_last(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = qa_report.report([Result('python', 'failed', '3.14.7', '3.14.8')], in_clanker=True)
        self.assertEqual(code, 1)
        self.assertNotIn('\x1b', output.getvalue())
        self.assertEqual(output.getvalue().splitlines()[-1], 'AUDIT: FAILED (1 failed)')

    def test_audit_errors_take_precedence_over_findings(self):
        state, code, counts = qa_report.summary([Result('python', 'failed'), Result('proton', 'error')])
        self.assertEqual((state, code), ('ERROR', 2))
        self.assertEqual(counts, {'error': 1, 'failed': 1})
        self.assertEqual(qa_report.summary([])[:2], ('ERROR', 2))

    def test_json_preserves_all_diagnostics_without_colors(self):
        output = io.StringIO()
        diagnostic = '\n'.join(map(str, range(50)))
        with contextlib.redirect_stdout(output):
            qa_report.report([Result('tool', 'failed', detail=diagnostic)], json_output=True, in_clanker=True)
        data = decode(output.getvalue())
        self.assertEqual(data['state'], 'FAILED')
        self.assertEqual(field(items(data['components'])[0], 'detail'), diagnostic)

    def test_rich_output_respects_color_controls_and_literal_markup(self):
        for environment, colored in (
            ({'FORCE_COLOR': '1'}, True),
            ({'FORCE_COLOR': '1', 'NO_COLOR': ''}, False),
            ({'FORCE_COLOR': '1', 'TERM': 'dumb'}, False),
        ):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    qa_report.report([Result('[red]literal', 'failed', '1', '2')])
                self.assertEqual('\x1b[' in output.getvalue(), colored)
                self.assertIn('[red]literal', output.getvalue())
                self.assertIn('AUDIT:', output.getvalue())

    def test_domain_tables_have_stripes_separators_and_a_final_summary(self):
        results = [
            Result('uv', 'failed', '1', '2', domain='Toolchain'),
            Result('jinja2', 'up-to-date', '3', '3', domain='Python dependencies'),
            Result('ruff', 'up-to-date', '4', '4', domain='Toolchain'),
            Result('nix-tools', 'blocked', detail='Unpublished source', domain='Nix inputs'),
        ]
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            terminal = Console(file=output, width=120, force_terminal=True, color_system='256', record=True)
            with patch.object(qa_report, 'new_console', return_value=terminal):
                qa_report.rich_report(results, 'AUDIT', 'BLOCKED', '1 blocked, 1 failed, 2 up-to-date', False)
        text = terminal.export_text()
        self.assertEqual(text.count('Toolchain'), 1)
        self.assertEqual(text.count('Python dependencies'), 1)
        self.assertLess(text.index('ruff'), text.index('Python dependencies'))
        self.assertEqual(text.count('Component'), 3)
        self.assertIn('│', text)
        rows = output.getvalue().splitlines()
        self.assertNotIn('48;5;', next(row for row in rows if ' uv ' in row))
        self.assertIn('48;5;', next(row for row in rows if 'ruff' in row))
        self.assertLess(text.index('Unpublished source'), text.index('Summary'))
        self.assertEqual(text.splitlines()[-1], 'AUDIT: BLOCKED (1 blocked, 1 failed, 2 up-to-date)')

    def test_tables_fit_contents_and_cap_width_without_redundant_prefixes(self):
        for width in (80, 200):
            output = io.StringIO()
            terminal = Console(file=output, width=width, color_system=None)
            qa_report.rich_section(
                terminal,
                'Nix inputs',
                [
                    Result('flake input: nixpkgs', 'failed', 'a' * 40, 'b' * 40, domain='Nix inputs'),
                ],
            )
            text = output.getvalue()
            self.assertNotIn('flake input:', text)
            self.assertIn('nixpkgs', text)
            self.assertLessEqual(max(map(len, text.splitlines())), min(width, 120))
        output = io.StringIO()
        qa_report.rich_section(
            Console(file=output, width=200),
            'Python dependencies',
            [
                Result('python dependency: rich', 'up-to-date', '15', '15', domain='Python dependencies'),
            ],
        )
        self.assertNotIn('python dependency:', output.getvalue())
        self.assertLess(max(map(len, output.getvalue().splitlines())), 80)
        self.assertEqual(
            qa_report.display_name(Result('action: actions/checkout', 'passed', domain='GitHub CI')), 'actions/checkout'
        )

    def test_animation_requires_a_human_tty_even_with_forced_color(self):
        for environment, tty, allowed in (
            ({}, True, True),
            ({'FORCE_COLOR': '1'}, False, False),
            ({'FORCE_COLOR': '1', 'CODEX_THREAD_ID': ''}, True, False),
            ({'FORCE_COLOR': '1', 'TERM': 'dumb'}, True, False),
            ({'NO_COLOR': ''}, True, False),
            ({'FORCE_COLOR': '0'}, True, False),
        ):
            with self.subTest(environment=environment, tty=tty), patch.dict(os.environ, environment, clear=True):
                terminal = Mock(is_terminal=True, file=Mock(isatty=Mock(return_value=tty)))
                self.assertEqual(console.live_output(terminal), allowed)
                self.assertFalse(console.live_output(terminal, in_clanker=True))
                self.assertFalse(console.live_output(terminal, no_color=True))


class LookupTests(unittest.TestCase):
    def test_unknown_sources_and_empty_versions_cannot_pass(self):
        result = outdated.external_source('custom', {'updates': {'provider': 'unsupported'}})
        self.assertEqual(result.state, 'unknown')
        for value in ('', None):
            with self.assertRaises(ValueError):
                outdated.compare('missing', '1', value)
        self.assertEqual(outdated.runner('ubuntu-latest').state, 'unknown')

    def test_adapter_reports_findings_and_errors_as_valid_documents(self):
        for state in ('up-to-date', 'outdated', 'error', 'unknown'):
            output = io.StringIO()
            with (
                patch.object(outdated, 'application', return_value=[Result('source', state)]),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(outdated.main(outdated.Options(Path('.'))), 0)
            report = decode(output.getvalue())
            self.assertEqual(report['schemaVersion'], 1)
            self.assertEqual(field(items(report['results'])[0], 'state'), state)
            self.assertEqual(
                set(mapping(items(report['results'])[0])), {'name', 'state', 'current', 'latest', 'detail'}
            )
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(outdated.main(outdated.Options(Path(directory))), 0)
        self.assertEqual(json.loads(output.getvalue())['results'][0]['state'], 'error')

    def test_application_retains_successful_sources_when_one_lookup_fails(self):
        root = Path(__file__).resolve().parents[2]

        def lookup(name: str, source: Mapping[str, object]) -> Result:
            if name == 'proton-cli':
                raise ValueError('private diagnostic')
            return Result(name, 'up-to-date', '1', '1')

        with (
            patch.object(outdated, 'external_source', side_effect=lookup),
            patch.object(outdated, 'runner', return_value=Result('GitHub runner', 'up-to-date')),
        ):
            rows = {row.name: row for row in outdated.application(root)}
        self.assertEqual(rows['proton-cli'].state, 'error')
        self.assertEqual(rows['debian-vm'].state, 'up-to-date')
        self.assertEqual(rows['debian-container'].state, 'up-to-date')
        self.assertEqual(rows['NAS runtime'].state, 'pinned')
        self.assertNotIn('private diagnostic', rows['proton-cli'].detail)

    def test_proton_uses_configured_platform_and_metadata_endpoint(self):
        source = {
            'version': '1.0.0',
            'url': 'https://example.test/cli/1.0.0/linux-arm64/proton-drive',
            'updates': {
                'provider': 'proton-cli',
                'url': 'https://example.test/releases',
                'reason': 'target architecture',
            },
        }
        page = '<a href="https://example.test/cli/2.0.0/linux-arm64/proton-drive">download</a>'
        with patch.object(outdated, 'fetch', return_value=page) as fetch:
            self.assertEqual(outdated.external_source('cli', source).latest, '2.0.0')
            fetch.assert_called_once_with('https://example.test/releases')
        with patch.object(outdated, 'fetch', return_value='<html>changed layout</html>'):
            result = outdated.guarded('cli', lambda: outdated.external_source('cli', source))
            self.assertEqual(result.state, 'error')

    def test_container_uses_json_repository_and_tag(self):
        source = {
            'tag': 'example-slim',
            'digest': 'sha256:old',
            'updates': {'provider': 'docker-hub', 'url': 'https://example.test/custom/tags', 'reason': 'compatibility'},
        }
        with patch.object(outdated, 'fetch', return_value=json.dumps({'digest': 'sha256:new'})) as fetch:
            self.assertEqual(outdated.external_source('image', source).state, 'outdated')
            fetch.assert_called_once_with('https://example.test/custom/tags/example-slim')

    def test_runner_ignores_arm_and_prerelease_images(self):
        releases = [
            {'tag_name': 'ubuntu24/20260920', 'prerelease': False, 'draft': False},
            {'tag_name': 'ubuntu26/20260920', 'prerelease': False, 'draft': False},
            {'tag_name': 'ubuntu28/20260920', 'prerelease': True, 'draft': False},
            {'tag_name': 'ubuntu30-arm/20260920', 'prerelease': False, 'draft': False},
        ]
        with patch.object(outdated, 'github', return_value=releases):
            result = outdated.runner('ubuntu-24.04')
        self.assertEqual((result.state, result.latest), ('outdated', 'ubuntu-26.04'))


class AuditTests(unittest.TestCase):
    def test_missing_tool_is_an_error(self):
        result = audit.run_step({'name': 'missing', 'command': ['/does-not-exist'], 'findingCodes': [1]}, Path('.'))
        self.assertEqual(result.state, 'error')

    def test_all_audit_steps_run_even_after_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / 'plan.json'
            plan.write_text(
                json.dumps([{'name': name, 'command': ['true'], 'findingCodes': [1]} for name in ('one', 'two')])
            )
            with (
                patch.object(audit, 'run_step', side_effect=[Result('one', 'failed'), Result('two', 'passed')]) as run,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                code = audit.main(audit.Options(plan=plan, json=True))
            self.assertEqual(run.call_count, 2)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())['counts'], {'failed': 1, 'passed': 1})

    def test_gitleaks_rejects_and_redacts_a_seeded_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            step: audit.AuditStep = {
                'name': 'secrets',
                'findingCodes': [10],
                'command': [
                    'gitleaks',
                    'dir',
                    '--redact',
                    '--no-banner',
                    '--no-color',
                    '--exit-code',
                    '10',
                    '.',
                ],
            }
            self.assertEqual(audit.run_step(step, root).state, 'passed')
            token = 'ghp_' + secrets.token_hex(18)
            (root / 'credentials.txt').write_text(token)
            result = audit.run_step(step, root)
            self.assertEqual(result.state, 'failed')
            self.assertNotIn(token, result.detail)

    def test_only_an_unborn_repository_can_skip_history(self):
        with tempfile.TemporaryDirectory() as directory:
            step: audit.AuditStep = {'name': 'history', 'requiresHead': True, 'command': [], 'findingCodes': [10]}
            root = Path(directory)
            self.assertEqual(audit.run_step(step, root).state, 'error')
            subprocess.run(['git', 'init', '--quiet', directory], check=True)
            self.assertEqual(audit.run_step(step, root).state, 'skipped')


if __name__ == '__main__':
    unittest.main()
