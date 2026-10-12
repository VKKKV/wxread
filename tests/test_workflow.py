"""Offline, stdlib-only guards for the Actions-only workflow contract.

These inspect the repository's deliberately simple YAML layout; they do not
execute workflows, resolve secrets/variables, or require a YAML dependency.
"""
from pathlib import Path
import re
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github/workflows'


def block(text, key, indent=0):
    """Extract one unique mapping block at the specified indentation."""
    pattern = r'^' + ' ' * indent + re.escape(key) + r':[^\n]*\n((?:(?: {' + str(indent + 1) + r',}\S[^\n]*|\s*)\n)*)'
    matches = re.findall(pattern, text, re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError(f'Expected one {key!r} block at indent {indent}')
    return textwrap.dedent(matches[0]).strip()


def active_lines(text):
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


class ActionsOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deploy = (WORKFLOWS / 'deploy.yml').read_text()
        cls.ci = (WORKFLOWS / 'tests.yml').read_text()

    def test_only_actions_workflows_and_no_retired_components(self):
        self.assertEqual({p.name for p in WORKFLOWS.glob('*.y*ml')},
                         {'deploy.yml', 'tests.yml'})
        for path in ('Dockerfile', 'docker-compose.yml', '.dockerignore',
                     '.env.example', 'scheduler.py', 'tests/test_scheduler.py',
                     '.github/workflows/docker-publish.yml'):
            with self.subTest(path=path):
                self.assertFalse((ROOT / path).exists())
        for workflow in (self.deploy, self.ci):
            self.assertNotRegex(workflow.lower(), r'docker|ghcr\.io|packages:\s*write')

    def test_deploy_has_one_cron_and_parameterless_manual_trigger(self):
        self.assertEqual(active_lines(block(self.deploy, 'on')),
                         ['schedule:', "- cron: '7 18 * * *'",
                          'workflow_dispatch:  # 手动触发'])
        self.assertEqual(re.findall(r'cron:\s*[\'"]([^\'"\n]+)', self.deploy),
                         ['7 18 * * *'])
        job = block(self.deploy, 'deploy', 2)
        self.assertRegex(job, r'(?m)^environment: AutoRead\s+#')
        self.assertRegex(job, r'(?m)^timeout-minutes: 180$')

    def test_main_is_the_only_live_entry_and_follows_offline_tests(self):
        runners = []
        for path in WORKFLOWS.glob('*.y*ml'):
            for command in re.findall(r'(?m)^\s*(python(?: -u)? \S+\.py)\s*$', path.read_text()):
                runners.append((path.name, command))
        self.assertEqual(runners, [('deploy.yml', 'python -u main.py')])
        self.assertLess(self.deploy.index('python -m unittest discover -s tests -v'),
                        self.deploy.index('python -u main.py'))

    def test_credentials_are_scoped_to_live_step_and_read_num_uses_vars(self):
        env = block(self.deploy, 'env', 6)
        values = dict(line.split(':', 1) for line in active_lines(env))
        expected = {name: '${{ secrets.' + name + ' }}' for name in (
            'WXREAD_CURL_BASH', 'PUSH_METHOD', 'PUSHPLUS_TOKEN', 'WXPUSHER_SPT',
            'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'SERVERCHAN_SPT',
            'http_proxy', 'https_proxy')}
        expected['READ_NUM'] = '${{ vars.READ_NUM }}'
        self.assertEqual({key: value.split('  #', 1)[0].strip()
                          for key, value in values.items()}, expected)
        live_step = self.deploy.index('- name: Run deployment script')
        self.assertNotIn('secrets.', self.deploy[:live_step])
        self.assertNotIn('secrets.', self.deploy[self.deploy.index('  keepalive-job:'):])
        self.assertEqual(self.deploy.count('secrets.'), len(expected) - 1)

    def test_default_read_permission_and_existing_keepalive(self):
        self.assertEqual(active_lines(block(self.deploy, 'permissions')), ['contents: read'])
        keepalive = block(self.deploy, 'keepalive-job', 2)
        self.assertIn('if: ${{ always() }}', keepalive)
        self.assertIn('needs: deploy', keepalive)
        self.assertEqual(active_lines(block(keepalive, 'permissions')), ['actions: write'])
        self.assertIn('uses: liskin/gh-workflow-keepalive@v1.2.1', keepalive)
        self.assertEqual(self.deploy.count('actions: write'), 1)

    def test_ci_is_useful_offline_and_has_no_live_credentials_or_entry(self):
        self.assertEqual(active_lines(block(self.ci, 'on')), ['push:', 'pull_request:'])
        self.assertEqual(active_lines(block(self.ci, 'permissions')), ['contents: read'])
        self.assertNotRegex(self.ci, r'secrets\.|environment:|pull_request_target|main\.py|scheduler\.py')
        self.assertEqual(re.findall(r'(?m)^\s+run: (.+)$', self.ci),
                         ['python -m pip install -r requirements.txt',
                          'python -m unittest discover -s tests -v'])
        self.assertEqual(re.findall(r'(?m)^\s+- uses: (.+)$', self.ci),
                         ['actions/checkout@v7', 'actions/setup-python@v7'])
        self.assertRegex(self.ci, r'(?m)^\s+timeout-minutes: 10$')
        self.assertIn("python-version: ['3.10', '3.14']", self.ci)
        self.assertEqual(re.findall(r'(?m)^\s+python-version: (.+)$', self.ci),
                         ["['3.10', '3.14']", '${{ matrix.python-version }}'])
        self.assertNotRegex(self.ci, r'(?m)^ +(?:env|permissions):')

    def test_dependencies_are_only_requests_and_pinned_transitive_dependencies(self):
        requirements = active_lines((ROOT / 'requirements.txt').read_text())
        self.assertEqual(requirements, [
            'certifi==2026.7.22', 'charset-normalizer==3.5.2', 'idna==3.20',
            'requests==2.34.2', 'urllib3==2.8.0'])


if __name__ == '__main__':
    unittest.main()
