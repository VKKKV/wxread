import os
import stat
import tempfile
import unittest

from cookie_store import load_cookies, save_cookies


class CookieStoreTests(unittest.TestCase):
    def test_empty_path_performs_no_io(self):
        base = {"wr_skey": "base"}
        self.assertEqual(load_cookies("", base), base)
        save_cookies("", base, {"wr_skey": "new"})

    def test_save_load_roundtrip_and_restrictive_mode(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "nested", "state.json")
            base = {"wr_skey": "initial", "wr_vid": "vid"}
            current = {**base, "wr_skey": "renewed", "extra": "value"}
            save_cookies(path, base, current)
            self.assertEqual(load_cookies(path, base), current)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_changed_base_does_not_reuse_state(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "state.json")
            base = {"wr_skey": "one", "wr_vid": "vid"}
            changed = {"wr_skey": "two", "wr_vid": "vid"}
            save_cookies(path, base, {"wr_skey": "renewed", "wr_vid": "vid"})
            self.assertEqual(load_cookies(path, changed), changed)

    def test_corrupt_state_fails_without_exposing_content(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "state.json")
            base = {"wr_skey": "base"}
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("not-json-and-secret")
            with self.assertRaises(RuntimeError) as caught:
                load_cookies(path, base)
            self.assertNotIn("secret", str(caught.exception))

    def test_missing_state_uses_base(self):
        with tempfile.TemporaryDirectory() as root:
            base = {'wr_skey': 'key', 'wr_vid': 'vid'}
            self.assertEqual(load_cookies(os.path.join(root, 'missing'), base), base)

    def test_invalid_state_identity_fails(self):
        import json
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'state.json')
            base = {'wr_skey': 'base', 'wr_vid': 'vid'}
            save_cookies(path, base, {'wr_skey': 'next', 'wr_vid': 'other'})
            with self.assertRaises(RuntimeError):
                load_cookies(path, base)
            with open(path, encoding='utf-8') as stream:
                state = json.load(stream)
            state['cookies'] = {'wr_vid': 'vid'}
            with open(path, 'w', encoding='utf-8') as stream:
                json.dump(state, stream)
            with self.assertRaises(RuntimeError):
                load_cookies(path, base)

    def test_failed_atomic_replace_preserves_prior_state(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'state.json')
            base = {'wr_skey': 'base', 'wr_vid': 'vid'}
            save_cookies(path, base, base)
            with patch('cookie_store.os.replace', side_effect=OSError('FAKE_SECRET')):
                with self.assertRaises(RuntimeError):
                    save_cookies(path, base, {**base, 'wr_skey': 'new'})
            self.assertEqual(load_cookies(path, base), base)
            self.assertEqual(os.listdir(root), ['state.json'])

    def test_save_error_does_not_leak_cookie(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "state-dir", "state.json")
            with open(os.path.join(root, "state-dir"), "w", encoding="utf-8") as stream:
                stream.write("block")
            with self.assertRaises(RuntimeError) as caught:
                save_cookies(path, {"wr_skey": "private"}, {"wr_skey": "private"})
            self.assertNotIn("private", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
