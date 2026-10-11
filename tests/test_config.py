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

    def test_double_quoted_bash_backslash_rules(self):
        config = self.load_config()
        headers, cookies = config.convert(
            r'''curl -H "X-Literal: \$cash\`tick\`\q\\\"" -b "wr_skey=\$key\`id\`;wr_vid=v"'''
        )
        self.assertEqual(headers, {'X-Literal': '$cash`tick`\\q\\"'})
        self.assertEqual(cookies, {'wr_skey': '$key`id`', 'wr_vid': 'v'})

    def test_option_arguments_cannot_override_cookies(self):
        config = self.load_config()
        for option in ('--data-raw', '-d', '--data', '--data-binary', '--url',
                       '-X', '--request', '-A', '--user-agent', '-e', '--referer'):
            with self.subTest(option=option):
                headers, cookies = config.convert(
                    "curl -b 'wr_skey=GOOD;wr_vid=real' " + option +
                    " '-bwr_skey=BAD;wr_vid=evil' --compressed"
                )
                self.assertEqual(headers, {})
                self.assertEqual(cookies, {'wr_skey': 'GOOD', 'wr_vid': 'real'})

    def test_option_arguments_cannot_inject_headers(self):
        config = self.load_config()
        for option in ('--data-raw', '-d', '--data', '--data-binary', '--url',
                       '-X', '--request', '-A', '--user-agent', '-e', '--referer'):
            for payload in ("-HCookie: wr_skey=BAD;wr_vid=evil", "--cookie=wr_skey=BAD;wr_vid=evil"):
                with self.subTest(option=option, payload=payload):
                    self.assertEqual(
                        config.convert("curl " + option + " '" + payload + "' -H 'X-Good: yes'"),
                        ({'X-Good': 'yes'}, {})
                    )

    def test_joined_quotes_and_contextual_continuations(self):
        config = self.load_config()
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=repr(newline)):
                headers, cookies = config.convert(
                    "curl \\" + newline + " -H 'X-Name: pre'\"mid\"$'\\x21' "
                    "-b \"wr_skey=ab\\" + newline + "cd;wr_vid=v\""
                )
                self.assertEqual(headers, {'X-Name': 'premid!'})
                self.assertEqual(cookies, {'wr_skey': 'abcd', 'wr_vid': 'v'})
        self.assertEqual(config._shell_tokens("curl 'a\\\nb'"), ['curl', 'a\\\nb'])
        self.assertEqual(config._shell_tokens("curl a\\\nb"), ['curl', 'ab'])
        self.assertEqual(config._shell_tokens("curl '' \"\" $''"), ['curl', '', '', ''])

    def test_attached_options_and_switches(self):
        config = self.load_config()
        command = (
            "curl --url=https://example.invalid -XPOST --request=POST -Aagent "
            "--user-agent=agent -ehttps://example.invalid --referer=ignored "
            "-d'-bBAD=body' --data='-HCookie: BAD=body' --data-binary=body "
            "--data-raw=body --compressed --location --insecure -s "
            "--header='X-Good: yes' -b'wr_skey=k;wr_vid=v'"
        )
        self.assertEqual(config.convert(command), ({'X-Good': 'yes'}, {'wr_skey': 'k', 'wr_vid': 'v'}))
        self.assertEqual(config.convert("curl -- '-bBAD=body' '-HCookie: BAD=body'"), ({}, {}))

    def test_unknown_and_missing_options_are_redacted(self):
        config = self.load_config()
        for command in (
            "curl --unknown-private-secret '-bBAD=body'", "curl -zprivate-secret",
            "curl --compressed=private-secret", "curl -sS", "private-secret -b x=1",
            "curl -b 'x=private-secret' ; curl -b 'BAD=body'",
        ):
            with self.subTest(command=command), self.assertRaises(ValueError) as caught:
                config.convert(command)
            self.assertNotIn('private-secret', str(caught.exception))
        for option in ('-H', '--header', '-b', '--cookie', '--data-raw', '-d',
                       '--data', '--data-binary', '--url', '-X', '--request', '-A',
                       '--user-agent', '-e', '--referer'):
            with self.subTest(option=option), self.assertRaises(ValueError):
                config.convert('curl ' + option)

    def test_malformed_quotes_and_limits_are_redacted(self):
        config = self.load_config()
        for command in ("curl -b 'private-secret", 'curl -b "private-secret',
                        "curl -b $'private-secret\\q'", "curl private-secret\\",
                        "curl private-secret\x00", 'curl ' + 'private-secret' * 6000,
                        'curl ' + 'x ' * 4096):
            with self.subTest(command=command[:30]), self.assertRaises(ValueError) as caught:
                config.convert(command)
            self.assertNotIn('private-secret', str(caught.exception))
        config = self.load_config(WXREAD_CURL_BASH="curl --unknown-private-secret")
        with self.assertRaises(ValueError) as caught:
            config.validate_config()
        self.assertEqual(str(caught.exception), 'Unsupported curl configuration format')

    def test_token_limits_accept_boundaries(self):
        config = self.load_config()
        self.assertEqual(config._shell_tokens('x' * 65536), ['x' * 65536])
        self.assertEqual(len(config._shell_tokens('x ' * 4096)), 4096)

    def test_quoted_control_characters_still_fail_validation(self):
        for value in ('private\\nsecret', 'private\\rsecret'):
            config = self.load_config(WXREAD_CURL_BASH="curl -b $'wr_skey=" + value + ";wr_vid=v'")
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                config.validate_config()
            self.assertNotIn('private-secret', str(caught.exception))

    def test_expansion_text_is_literal_and_never_executed(self):
        config = self.load_config()
        with patch('os.system', side_effect=AssertionError('must not execute')):
            headers, cookies = config.convert(
                '''curl -H "X-Literal: $(private-command) `private-command` $HOME" -b 'x=$HOME' '''
            )
        self.assertEqual(headers['X-Literal'], '$(private-command) `private-command` $HOME')
        self.assertEqual(cookies, {'x': '$HOME'})

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
