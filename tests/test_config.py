import importlib.util
from pathlib import Path
import os
import unittest
from unittest.mock import patch


class ConfigTests(unittest.TestCase):
    def load_config(self, **env):
        with patch.dict(os.environ, env, clear=True):
            spec = importlib.util.spec_from_file_location("isolated_config", Path(__file__).resolve().parents[1] / "config.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

    def test_read_num_default_for_missing_and_blank(self):
        self.assertEqual(self.load_config().READ_NUM, 40)
        self.assertEqual(self.load_config(READ_NUM="   ").READ_NUM, 40)

    def test_read_num_positive_and_invalid(self):
        self.assertEqual(self.load_config(READ_NUM="12").READ_NUM, 12)
        for value in ("0", "-1", "oops"):
            with self.subTest(value=value):
                if value == "oops":
                    with self.assertRaisesRegex(ValueError, "positive integer"):
                        self.load_config(READ_NUM=value).validate_config()
                else:
                    config = self.load_config(READ_NUM=value)
                    with self.assertRaisesRegex(ValueError, "positive integer"):
                        config.validate_config()

    def test_convert_header_cookie_variants(self):
        config = self.load_config()
        headers, cookies = config.convert(
            "curl -H=\"Accept: application/json\" --header 'cOoKiE: wr_skey=secret; wr_vid=id; a=b=c'"
        )
        self.assertEqual(headers, {"Accept": "application/json"})
        self.assertEqual(cookies, {"wr_skey": "secret", "wr_vid": "id", "a": "b=c"})
        self.assertEqual(config.convert("curl -b\"x=1;y=2\""), ({}, {"x": "1", "y": "2"}))
        self.assertEqual(config.convert("curl --cookie='x=1; y=2'"), ({}, {"x": "1", "y": "2"}))

    def test_ansi_c_quoting_and_continuation(self):
        config = self.load_config()
        _, cookies = config.convert("curl \\\n -b $'x=hello\\x20world; y=a\\'b'")
        self.assertEqual(cookies, {"x": "hello world", "y": "a'b"})

    def test_ansi_preserves_unicode_and_utf8_escapes(self):
        config = self.load_config()
        headers, cookies = config.convert(r"curl -H $'X-Name: 中文' -b $'a=\xe4\xb8\xad;wr_skey=key;wr_vid=id'")
        self.assertEqual(headers['X-Name'], '中文')
        self.assertEqual(cookies['a'], '中')

    def test_ansi_like_text_inside_double_quotes_is_literal(self):
        config = self.load_config()
        self.assertEqual(config.convert('curl -b "a=$\'hello\';wr_skey=k;wr_vid=v"')[1]['a'], "$'hello'")
        self.assertEqual(config.convert("curl -b='a=1;b=2'")[1], {'a': '1', 'b': '2'})

    def test_curl_overrides_template_cookies(self):
        config = self.load_config(WXREAD_CURL_BASH="curl -b 'wr_skey=k;wr_vid=v'")
        self.assertEqual(config.cookies, {'wr_skey': 'k', 'wr_vid': 'v'})

    def test_unsupported_format_rejected_without_echoing_secret(self):
        config = self.load_config()
        with self.assertRaises(ValueError) as caught:
            config.convert("curl -b 'private-secret'")
        self.assertNotIn("private-secret", str(caught.exception))

    def test_validation_required_cookies(self):
        config = self.load_config()
        with self.assertRaisesRegex(ValueError, "wr_skey"):
            config.validate_config()
        config.cookies.update(wr_skey="key", wr_vid="vid")
        config.validate_config()
        config.cookies["wr_vid"] = " "
        with self.assertRaisesRegex(ValueError, "wr_vid"):
            config.validate_config()


if __name__ == "__main__":
    unittest.main()
