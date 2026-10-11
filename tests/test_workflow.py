"""Execute the real promotion shell against authored, offline git/registry fakes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class PromotionTests(unittest.TestCase):
    def promote(self, source, aliases, refs, *, stale=False, fail_tags=False):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/docker-publish.yml').read_text()
        body = workflow.split('          set -euo pipefail\n', 1)[1]
        body = '\n'.join(line[10:] if line.startswith('          ') else line
                         for line in body.splitlines())
        fakes = '''
        git() {
          if [[ "$*" == 'ls-remote --tags origin' ]]; then
            [[ "$FAIL_TAGS" == 0 ]] || return 1
            printf '%s\n' "$FAKE_REFS"
          else
            printf '%s\t%s\n' "$CURRENT_SHA" "$SOURCE_REF"
          fi
        }
        docker() {
          if [[ "$3" == create ]]; then
            printf '%s\n' "$5" >> "$WRITES"
          elif [[ "$3" == inspect ]]; then
            printf '{"digest":"%s"}\n' "$SOURCE_DIGEST"
          else
            return 1
          fi
        }
        '''
        # No repository or credential data enters these subprocesses.
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            log = Path(tmp) / 'writes'
            env = {'PATH': os.environ['PATH'], 'TMPDIR': tmp,
                   'SOURCE_REF': 'refs/tags/' + source,
                   'SOURCE_IMAGE': 'ghcr.io/example/offline',
                   'SOURCE_DIGEST': 'sha256:fake', 'EXPECTED_SHA': 'abc',
                   'CURRENT_SHA': 'old' if stale else 'abc',
                   'IMMUTABLE_TAG': 'ghcr.io/example/offline:sha-abc-1-1',
                   'TAGS': '\n'.join('ghcr.io/example/offline:' + a for a in aliases),
                   'FAKE_REFS': '\n'.join('abc\trefs/tags/' + r for r in refs),
                   'FAIL_TAGS': str(int(fail_tags)), 'WRITES': str(log)}
            result = subprocess.run(['bash', '-c', fakes + '\nset -euo pipefail\n' + body],
                                    env=env, capture_output=True, text=True, timeout=10)
            writes = log.read_text().splitlines() if log.exists() else []
        return result, writes

    def test_delayed_older_release_cannot_overwrite_shared_alias(self):
        result, writes = self.promote('v1.0.0', ['1.0.0', '1.0', '1'],
                                     ['v1.0.0', 'v1.1.0'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(writes, ['ghcr.io/example/offline:1.0.0',
                                 'ghcr.io/example/offline:1.0'])

    def test_newer_patch_blocks_minor_and_major_alias(self):
        result, writes = self.promote('v1.0.0', ['1.0.0', '1.0', '1'],
                                     ['v1.0.0', 'v1.0.1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(writes, ['ghcr.io/example/offline:1.0.0'])

    def test_highest_release_promotes_all_its_aliases(self):
        result, writes = self.promote('v1.1.0', ['1.1.0', '1.1', '1'],
                                     ['v1.0.0', 'v1.1.0', 'v2.0.0'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(writes), 3)

    def test_prerelease_cannot_promote_stable_alias(self):
        result, writes = self.promote('v1.2.0-rc.1', ['1.2.0-rc.1', '1.2', '1'],
                                     ['v1.2.0-rc.1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(writes, ['ghcr.io/example/offline:1.2.0-rc.1'])

    def test_stale_ref_is_skipped(self):
        result, writes = self.promote('v1.0.0', ['1'], ['v1.0.0'], stale=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(writes, [])

    def test_tag_query_failure_closes_gate(self):
        result, writes = self.promote('v1.0.0', ['1'], ['v1.0.0'], fail_tags=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])


if __name__ == '__main__':
    unittest.main()
