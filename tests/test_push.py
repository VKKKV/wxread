import io
import json
import logging
import os
import unittest
from unittest.mock import call, patch

import requests
from requests.adapters import HTTPAdapter

with patch.dict(os.environ, {"READ_NUM": "", "WXREAD_CURL_BASH": "", "PUSH_METHOD": ""}):
    import push


class PushTests(unittest.TestCase):
    def response(self, payload, status=200):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode("utf-8")
        response.encoding = "utf-8"
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
        post.return_value._content = b'FAKE_SECRET invalid JSON'
        self.assertFalse(push.PushNotification().push_pushplus('hi', 'token', True))

    @patch("push.PUSHPLUS_TOKEN", "")
    def test_public_push_signature_and_config_dispatch(self):
        self.assertFalse(push.push("x", "pushplus"))
        self.assertFalse(push.push("x", "unknown"))


class RequestsPushTests(unittest.TestCase):
    """Run real Requests preparation, env merging and redirect logic offline."""

    SECRET = "FAKE_TOKEN"
    CONTENT = "FAKE_PRIVATE_CONTENT /web/book/read?x=1#detail 中文"
    CHANNELS = ("pushplus", "wxpusher", "serverchan", "telegram", "telegram_direct")

    def start_patch(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    def setUp(self):
        self.start_patch(patch.dict(os.environ, {}, clear=True))
        self.socket_connect = self.start_patch(patch(
            "socket.socket.connect", side_effect=AssertionError("Network forbidden")
        ))
        self.socket_connect_ex = self.start_patch(patch(
            "socket.socket.connect_ex", side_effect=AssertionError("Network forbidden")
        ))
        self.sleep = self.start_patch(patch("push.time.sleep"))
        self.sent = []
        self.responses = []
        self.closed_sessions = []
        original_close = requests.Session.close

        def close(session):
            self.closed_sessions.append(session)
            original_close(session)

        self.start_patch(patch.object(requests.Session, "close", autospec=True, side_effect=close))
        self.start_patch(patch.object(
            HTTPAdapter, "send", autospec=True,
            side_effect=self.adapter_send,
        ))

    def tearDown(self):
        self.socket_connect.assert_not_called()
        self.socket_connect_ex.assert_not_called()

    def adapter_send(self, adapter, request, **settings):
        self.assertIsInstance(adapter, HTTPAdapter)
        self.assertIsInstance(request, requests.PreparedRequest)
        self.sent.append((request, settings))
        response = self.responder(request, len(self.sent))
        self.responses.append(response)
        return response

    def response(self, request, payload, status=200, location=None, body=None):
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response.encoding = "utf-8"
        response._content = json.dumps(payload).encode("utf-8") if body is None else body
        response.raw = io.BytesIO(response._content)
        if location is not None:
            response.headers["Location"] = location
        return response

    def invoke(self, channel, notifier=None):
        notifier = notifier or push.PushNotification()
        if channel == "pushplus":
            return notifier.push_pushplus(self.CONTENT, self.SECRET, True)
        if channel == "wxpusher":
            return notifier.push_wxpusher(self.CONTENT, self.SECRET)
        if channel == "serverchan":
            return notifier.push_serverChan(self.CONTENT, self.SECRET, True)
        return notifier.push_telegram(self.CONTENT, self.SECRET, "FAKE_CHAT")

    def success_payload(self, channel):
        codes = {"pushplus": 200, "wxpusher": 1000, "serverchan": 0}
        return {"code": codes[channel]} if channel in codes else {"ok": True}

    def reset_transport(self):
        self.sent.clear()
        self.responses.clear()
        self.closed_sessions.clear()
        self.sleep.reset_mock()

    def expected_attempts(self, channel):
        return 4 if channel.startswith("telegram") else 3

    def test_redirects_never_forward_token_or_body(self):
        for channel in self.CHANNELS:
            for status in (301, 302, 303, 307, 308):
                for scheme in ("https", "http"):
                    with self.subTest(channel=channel, status=status, scheme=scheme):
                        self.reset_transport()
                        target = f"{scheme}://redirect.invalid/notification"

                        def responder(request, index):
                            if channel == "telegram_direct" and index == 1:
                                raise requests.exceptions.ProxyError("FAKE_TOKEN proxy failure")
                            return self.response(
                                request, self.success_payload(channel),
                                200 if request.url == target else status,
                                None if request.url == target else target,
                            )

                        self.responder = responder
                        with self.assertLogs("push", level="ERROR") as logs:
                            accepted = self.invoke(channel)
                            self.assertTrue(all(req.url != target for req, _ in self.sent))
                            self.assertFalse(accepted)
                        self.assertEqual(len(self.sent), self.expected_attempts(channel))
                        self.assertTrue(all(req.method == "POST" for req, _ in self.sent))
                        for request, _ in self.sent:
                            payload = json.loads(request.body)
                            self.assertEqual(
                                payload.get("text", payload.get("content", payload.get("desp"))),
                                self.CONTENT,
                            )
                            if channel in ("pushplus", "wxpusher"):
                                self.assertIn(self.SECRET.encode(), request.body)
                            else:
                                self.assertIn(self.SECRET, request.url)
                        self.assertTrue(all(not resp.history for resp in self.responses))
                        self.assertNotIn(self.SECRET, " ".join(logs.output))
                        self.assertNotIn(self.CONTENT, " ".join(logs.output))
                        self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])

    def test_non_2xx_rejected_even_with_success_business_json(self):
        for channel in self.CHANNELS:
            for status in (100, 199, 300, 301, 302, 304, 307, 308, 399, 400, 429, 500):
                with self.subTest(channel=channel, status=status):
                    self.reset_transport()

                    def responder(request, index):
                        if channel == "telegram_direct" and index == 1:
                            raise requests.exceptions.ProxyError("FAKE_TOKEN")
                        return self.response(request, self.success_payload(channel), status)

                    self.responder = responder
                    with self.assertLogs("push", level="ERROR") as logs:
                        self.assertFalse(self.invoke(channel))
                    self.assertEqual(len(self.sent), self.expected_attempts(channel))
                    self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])
                    self.assertNotIn(self.SECRET, " ".join(logs.output))
                    self.assertNotIn(self.CONTENT, " ".join(logs.output))

    def test_2xx_still_requires_business_acceptance(self):
        for channel in self.CHANNELS:
            for status in (200, 201, 202, 204, 206, 299):
                with self.subTest(channel=channel, status=status):
                    self.reset_transport()

                    def responder(request, index):
                        if channel == "telegram_direct" and index == 1:
                            raise requests.ConnectionError("FAKE_TOKEN")
                        return self.response(request, self.success_payload(channel), status)

                    self.responder = responder
                    self.assertTrue(self.invoke(channel))
                    self.assertEqual(len(self.sent), 2 if channel == "telegram_direct" else 1)
                    self.sleep.assert_not_called()
            for body in (b"", b"FAKE_TOKEN invalid JSON", b'{"code": false, "ok": false}'):
                with self.subTest(channel=channel, body=body):
                    self.reset_transport()
                    self.responder = lambda req, index: self.response(req, None, body=body)
                    with self.assertLogs("push", level="ERROR") as logs:
                        self.assertFalse(self.invoke(channel))
                    self.assertEqual(len(self.sent), self.expected_attempts(channel))
                    self.assertNotIn(self.SECRET, " ".join(logs.output))

    def test_telegram_standard_environment_proxy_discovery(self):
        proxy = "http://proxy.invalid:8080"
        other = "http://other.invalid:8081"
        cases = (
            ({"HTTPS_PROXY": proxy}, proxy),
            ({"https_proxy": proxy}, proxy),
            ({"ALL_PROXY": proxy}, proxy),
            ({"all_proxy": proxy}, proxy),
            ({"HTTPS_PROXY": other, "https_proxy": proxy}, proxy),
            ({"HTTPS_PROXY": proxy, "ALL_PROXY": other}, proxy),
            ({"HTTPS_PROXY": proxy, "NO_PROXY": "api.telegram.org"}, None),
            ({"https_proxy": proxy, "no_proxy": "api.telegram.org"}, None),
            ({"ALL_PROXY": proxy, "NO_PROXY": "api.telegram.org"}, None),
            ({"HTTPS_PROXY": proxy, "NO_PROXY": "unrelated.invalid"}, proxy),
        )
        for env, expected in cases:
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                self.reset_transport()
                self.responder = lambda req, index: self.response(req, {"ok": True})
                self.assertTrue(self.invoke("telegram"))
                self.assertEqual(len(self.sent), 1)
                request, settings = self.sent[0]
                self.assertEqual(requests.utils.select_proxy(request.url, settings["proxies"]), expected)
                self.assertEqual(json.loads(request.body), {"chat_id": "FAKE_CHAT", "text": self.CONTENT})

    def test_telegram_reads_environment_at_request_time(self):
        notifier = push.PushNotification()
        self.responder = lambda req, index: self.response(req, {"ok": True})
        with patch.dict(os.environ, {"https_proxy": "http://late.invalid:8080"}):
            self.assertTrue(self.invoke("telegram", notifier))
        request, settings = self.sent[0]
        self.assertEqual(requests.utils.select_proxy(request.url, settings["proxies"]), "http://late.invalid:8080")

    def test_telegram_fallback_is_bounded_direct_and_closes_session(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid:8080"}):
            def responder(request, index):
                if index == 1:
                    raise requests.exceptions.ProxyError("FAKE_TOKEN proxy failure")
                return self.response(request, {"ok": True}, 503 if index == 2 else 201)

            self.responder = responder
            with self.assertLogs("push", level="ERROR") as logs:
                self.assertTrue(self.invoke("telegram"))
        self.assertEqual(len(self.sent), 3)
        first_request, first_settings = self.sent[0]
        self.assertEqual(requests.utils.select_proxy(first_request.url, first_settings["proxies"]), "http://proxy.invalid:8080")
        for _, settings in self.sent[1:]:
            self.assertEqual(settings["proxies"], {})
        self.assertEqual(self.sleep.call_args_list, [call(1)])
        self.assertEqual([session.trust_env for session in self.closed_sessions], [True, False])
        self.assertNotIn(self.SECRET, " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
