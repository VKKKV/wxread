import logging
import unittest
from unittest.mock import MagicMock, patch

import requests

import os

with patch.dict(os.environ, {"READ_NUM": "", "WXREAD_CURL_BASH": "", "PUSH_METHOD": ""}):
    import push


class PushTests(unittest.TestCase):
    def response(self, payload, status=200):
        response = MagicMock()
        response.status_code = status
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    @patch("push.time.sleep")
    @patch("push.requests.post")
    def test_pushplus_business_success_and_failure(self, post, _sleep):
        post.return_value = self.response({"code": 200})
        self.assertTrue(push.PushNotification().push_pushplus("text", "secret", True))
        post.return_value = self.response({"code": 500, "msg": "secret"})
        self.assertFalse(push.PushNotification().push_pushplus("text", "secret", True))
        self.assertEqual(post.call_count, 4)

    @patch("push.time.sleep")
    @patch("push.requests.post")
    def test_wxpusher_uses_official_json_api(self, post, _sleep):
        post.return_value = self.response({"code": 1000})
        self.assertTrue(push.PushNotification().push_wxpusher("hello", "app-secret"))
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://wxpusher.zjiecode.com/api/send/message/simple-push")
        self.assertEqual(kwargs["json"], {"spt": "app-secret", "content": "hello", "contentType": 1})

    @patch("push.time.sleep")
    @patch("push.requests.post")
    def test_serverchan_business_code(self, post, _sleep):
        post.return_value = self.response({"code": 1})
        self.assertFalse(push.PushNotification().push_serverChan("x", "key", True))
        self.assertEqual(post.call_count, 3)

    @patch("push.time.sleep")
    @patch("push.requests.Session")
    @patch("push.requests.post")
    def test_telegram_proxy_then_direct_session_no_env(self, post, session_cls, _sleep):
        post.side_effect = requests.ConnectionError("https://api.telegram.org/botsecret")
        session = session_cls.return_value
        session.post.return_value = self.response({"ok": True})
        self.assertTrue(push.PushNotification().push_telegram("hi", "secret", "chat"))
        self.assertFalse(session.trust_env)
        session.post.assert_called_once()

    @patch("push.requests.post")
    def test_missing_credentials_do_not_call_network(self, post):
        notifier = push.PushNotification()
        self.assertFalse(notifier.push_pushplus("x", "", True))
        self.assertFalse(notifier.push_wxpusher("x", ""))
        self.assertFalse(notifier.push_serverChan("x", "", True))
        self.assertFalse(notifier.push_telegram("x", "", "chat"))
        post.assert_not_called()

    @patch("push.time.sleep")
    @patch("push.requests.post")
    def test_logs_do_not_include_secret_or_provider_body(self, post, _sleep):
        post.return_value = self.response({"code": 403, "msg": "secret"})
        with self.assertLogs("push", level=logging.ERROR) as captured:
            self.assertFalse(push.PushNotification().push_pushplus("x", "secret", True))
        text = " ".join(captured.output)
        self.assertNotIn("secret", text)
        self.assertNotIn("403", text)

    @patch("push.time.sleep")
    @patch("push.requests.post")
    def test_wxpusher_preserves_reserved_chars_and_rejects_business_failure(self, post, _sleep):
        post.return_value = self.response({'code': 1001})
        content = 'read /web/book/read?x=1#detail 中文'
        self.assertFalse(push.PushNotification().push_wxpusher(content, 'SPT_fake'))
        self.assertEqual(post.call_args.kwargs['json']['content'], content)
        self.assertEqual(post.call_count, 3)

    @patch("push.time.sleep")
    @patch("push.requests.post")
    def test_http_exception_urls_are_redacted_all_channels(self, post, _sleep):
        for method, args in (("push_pushplus", ('hi', 'FAKE_SECRET', True)),
                             ("push_wxpusher", ('hi', 'FAKE_SECRET')),
                             ("push_serverChan", ('hi', 'FAKE_SECRET', True))):
            post.side_effect = requests.HTTPError('https://server/FAKE_SECRET')
            with self.assertLogs('push', level='ERROR') as logs:
                self.assertFalse(getattr(push.PushNotification(), method)(*args))
            self.assertNotIn('FAKE_SECRET', ' '.join(logs.output))

    @patch("push.time.sleep")
    @patch("push.requests.post")
    def test_invalid_json_and_false_business_code_are_rejected(self, post, _sleep):
        post.return_value = self.response({'code': False})
        self.assertFalse(push.PushNotification().push_serverChan('hi', 'key', True))
        post.return_value.json.side_effect = ValueError('FAKE_SECRET')
        self.assertFalse(push.PushNotification().push_pushplus('hi', 'token', True))

    @patch("push.PUSHPLUS_TOKEN", "")
    def test_public_push_signature_and_config_dispatch(self):
        self.assertFalse(push.push("x", "pushplus"))
        self.assertFalse(push.push("x", "unknown"))


if __name__ == "__main__":
    unittest.main()
