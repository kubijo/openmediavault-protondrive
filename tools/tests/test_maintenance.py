import contextlib
import io
import json
import os
import secrets
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import audit
import console
import outdated
import qa_report
from qa_report import Result
from rich.console import Console


class TerminalTests(unittest.TestCase):
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
            code = qa_report.report([Result('python', 'outdated', '3.14.7', '3.14.8')], in_clanker=True)
        self.assertEqual(code, 1)
        self.assertNotIn('\x1b', output.getvalue())
        self.assertEqual(output.getvalue().splitlines()[-1], 'OUTDATED CHECK: OUTDATED (1 outdated)')

    def test_errors_cannot_be_reported_as_current_or_only_outdated(self):
        state, code, counts = qa_report.summary([Result('python', 'outdated'), Result('proton', 'error')])
        self.assertEqual((state, code), ('ERROR', 2))
        self.assertEqual(counts, {'error': 1, 'outdated': 1})
        self.assertEqual(qa_report.summary([])[:2], ('ERROR', 2))

    def test_json_preserves_all_diagnostics_without_colors(self):
        output = io.StringIO()
        diagnostic = '\n'.join(map(str, range(50)))
        with contextlib.redirect_stdout(output):
            qa_report.report(
                [Result('tool', 'failed', detail=diagnostic)], audit=True, json_output=True, in_clanker=True
            )
        data = json.loads(output.getvalue())
        self.assertEqual(data['state'], 'FAILED')
        self.assertEqual(data['components'][0]['detail'], diagnostic)

    def test_rich_output_respects_color_controls_and_literal_markup(self):
        for environment, colored in (
            ({'FORCE_COLOR': '1'}, True),
            ({'FORCE_COLOR': '1', 'NO_COLOR': ''}, False),
            ({'FORCE_COLOR': '1', 'TERM': 'dumb'}, False),
        ):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    qa_report.report([Result('[red]literal', 'outdated', '1', '2')])
                self.assertEqual('\x1b[' in output.getvalue(), colored)
                self.assertIn('[red]literal', output.getvalue())
                self.assertIn('OUTDATED CHECK:', output.getvalue())

    def test_domain_tables_have_stripes_separators_and_a_final_summary(self):
        results = [
            Result('uv', 'outdated', '1', '2', domain='Toolchain'),
            Result('jinja2', 'up-to-date', '3', '3', domain='Python dependencies'),
            Result('ruff', 'up-to-date', '4', '4', domain='Toolchain'),
            Result('nix-tools', 'blocked', detail='Unpublished source', domain='Nix inputs'),
        ]
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            terminal = Console(file=output, width=120, force_terminal=True, color_system='256', record=True)
            with patch.object(qa_report, 'new_console', return_value=terminal):
                qa_report.rich_report(
                    results, 'OUTDATED CHECK', 'BLOCKED', '1 blocked, 1 outdated, 2 up-to-date', False
                )
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
        self.assertEqual(text.splitlines()[-1], 'OUTDATED CHECK: BLOCKED (1 blocked, 1 outdated, 2 up-to-date)')

    def test_tables_fit_contents_and_cap_width_without_redundant_prefixes(self):
        for width in (80, 200):
            output = io.StringIO()
            terminal = Console(file=output, width=width, color_system=None)
            qa_report.rich_section(
                terminal,
                'Nix inputs',
                [
                    Result('flake input: nixpkgs', 'outdated', 'a' * 40, 'b' * 40, domain='Nix inputs'),
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
                terminal = Mock(is_terminal=True)
                terminal.file.isatty.return_value = tty
                self.assertEqual(console.live_output(terminal), allowed)
                self.assertFalse(console.live_output(terminal, in_clanker=True))
                self.assertFalse(console.live_output(terminal, no_color=True))

    def test_live_sections_appear_immediately_and_completed_tasks_are_removed(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch.object(output, 'isatty', return_value=True):
            terminal = Console(file=output, force_terminal=True, color_system='256', width=100)
            with patch.object(qa_report, 'new_console', return_value=terminal):
                with qa_report.SectionReport({'Toolchain': 1, 'Python dependencies': 1}) as reporter:
                    self.assertIn('Toolchain', output.getvalue())
                    self.assertIn('Python dependencies', output.getvalue())
                    reporter.advance('Toolchain')
                    reporter.section_ready('Toolchain', [Result('uv', 'passed', domain='Toolchain')])
                    self.assertEqual([task.description for task in reporter.progress.tasks], ['Python dependencies'])
                    self.assertIn('Component', output.getvalue())
                    reporter.section_ready(
                        'Python dependencies', [Result('rich', 'passed', domain='Python dependencies')]
                    )
                    self.assertEqual(reporter.progress.tasks, [])
                reporter.finish([Result('uv', 'up-to-date')])
        self.assertIn('OUTDATED CHECK: UP-TO-DATE', output.getvalue())

    def test_json_stream_emits_only_the_final_document(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with qa_report.SectionReport({'Toolchain': 1}, json_output=True) as reporter:
                self.assertIsNone(reporter.progress)
                reporter.section_ready('Toolchain', [Result('uv', 'up-to-date')])
                self.assertEqual(output.getvalue(), '')
            reporter.finish([Result('uv', 'up-to-date')])
        self.assertEqual(json.loads(output.getvalue())['state'], 'UP-TO-DATE')
        self.assertNotIn('\x1b', output.getvalue())


class CollectionTests(unittest.TestCase):
    def test_fast_sections_finish_before_slow_ones_with_stable_result_order(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow():
            started.set()
            if not release.wait(5):
                raise AssertionError('Fast section was starved by the slow domain')
            return Result('slow', 'up-to-date')

        def fast():
            self.assertTrue(started.wait(5))
            return Result('fast', 'outdated')

        sections = []

        def completed(domain, results):
            sections.append((domain, results))
            if domain == 'Python dependencies':
                release.set()

        output = Mock()
        output.section_ready.side_effect = completed
        jobs = [('slow', slow, 'Toolchain')] * 7 + [('fast', fast, 'Python dependencies')]
        fixed = [Result('NAS', 'compatibility-pinned', domain='Runtime compatibility')]
        results = outdated.collect(jobs, fixed, output)
        self.assertEqual(
            [domain for domain, _ in sections], ['Runtime compatibility', 'Python dependencies', 'Toolchain']
        )
        self.assertEqual([result.name for result in results], ['slow'] * 7 + ['fast', 'NAS'])
        self.assertEqual(output.advance.call_count, 8)

    def test_failed_lookup_completes_its_section_and_sets_error_summary(self):
        output = Mock()
        lookup = Mock(side_effect=ValueError('offline'))
        results = outdated.collect([('broken', lookup, 'Nix inputs')], [], output)
        output.section_ready.assert_called_once_with('Nix inputs', results)
        self.assertEqual(qa_report.summary(results)[:2], ('ERROR', 2))


class LookupTests(unittest.TestCase):
    def test_lookup_domain_is_preserved_on_success_and_failure(self):
        result = outdated.guarded('uv', lambda: Result('uv', 'up-to-date'), 'Toolchain')
        self.assertEqual(result.domain, 'Toolchain')
        with patch.object(outdated, 'host_nix', side_effect=OSError('offline')):
            result = outdated.guarded('nix', outdated.host_nix, 'Toolchain')
        self.assertEqual((result.state, result.domain), ('error', 'Toolchain'))

    def test_version_comparison_does_not_suggest_downgrading_snapshots(self):
        for current, latest, expected in (
            ('3.14.8', 'v3.14.8', 'up-to-date'),
            ('3.9.0', '3.14.8', 'outdated'),
            ('3.14.8', '3.14.7', 'ahead'),
            ('0.5.8-unstable-2026-07-17', 'v0.5.8', 'ahead'),
            ('0.5.8-unstable-2026-07-17', 'v0.5.9', 'outdated'),
        ):
            with self.subTest(current=current, latest=latest):
                self.assertEqual(outdated.compare('tool', current, latest).state, expected)

    def test_stable_tag_selection_skips_prereleases_and_sorts_numerically(self):
        tags = [{'name': value} for value in ('php-8.6.0beta1', 'php-8.5.11', 'php-8.5.9', 'security-audit')]
        with patch.object(outdated, 'github', return_value=tags):
            self.assertEqual(outdated.latest_tag('php/php-src'), 'php-8.5.11')

    def test_no_releases_falls_back_to_tags_but_network_failure_does_not(self):
        for status in (404, 403, 500):
            with self.subTest(status=status):
                error = subprocess.CalledProcessError(1, ['gh'], stderr=f'gh: Not Found (HTTP {status})')
                with (
                    patch.object(outdated, 'github', side_effect=error),
                    patch.object(outdated, 'latest_tag', return_value='v1.0') as tags,
                ):
                    if status == 404:
                        self.assertEqual(outdated.latest_release('owner/repo'), 'v1.0')
                        tags.assert_called_once()
                    else:
                        with self.assertRaises(subprocess.CalledProcessError):
                            outdated.latest_release('owner/repo')
                        tags.assert_not_called()

    def test_action_major_tag_is_compared_by_resolved_commit(self):
        with patch.object(outdated, 'latest_release', return_value='v7.0.1'):
            with patch.object(outdated, 'github', return_value={'sha': 'a' * 40}):
                self.assertEqual(outdated.action('actions/checkout', 'v7').state, 'up-to-date')
            with patch.object(outdated, 'github', side_effect=[{'sha': 'a' * 40}, {'sha': 'b' * 40}]):
                result = outdated.action('actions/checkout', 'v6')
                self.assertEqual((result.state, result.current, result.latest), ('outdated', 'v6', 'v7.0.1'))

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
            fetch.assert_called_once_with(source['updates']['url'])
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
            plan.write_text(json.dumps([{'name': 'one'}, {'name': 'two'}]))
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
            step = {
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
            step = {'name': 'history', 'requiresHead': True, 'command': [], 'findingCodes': [10]}
            root = Path(directory)
            self.assertEqual(audit.run_step(step, root).state, 'error')
            subprocess.run(['git', 'init', '--quiet', directory], check=True)
            self.assertEqual(audit.run_step(step, root).state, 'skipped')


if __name__ == '__main__':
    unittest.main()
