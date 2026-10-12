import copy
import importlib
import json
import unittest
from unittest.mock import Mock, call, patch
from requests.adapters import HTTPAdapter
from requests.cookies import cookiejar_from_dict

import requests

import os

with patch.dict(os.environ, {"READ_NUM": "", "WXREAD_CURL_BASH": "", "PUSH_METHOD": ""}):
    import main


def response(body, status=200, cookies=None):
    result = Mock(status_code=status)
    result.cookies = cookiejar_from_dict(cookies or {})
    result.json.return_value = body
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError('FAKE_SECRET_URL')
    return result


class ReadingTests(unittest.TestCase):
    def make_runner(self, reads):
        reads = iter(reads)
        client = Mock()
        def post(url, **kwargs):
            if url == main.RENEW_URL:
                return response({}, cookies={'wr_skey': 'NEWKEY12long', 'wr_vid': '1'})
            if url == main.FIX_SYNCKEY_URL:
                return response({})
            return next(reads)
        client.post.side_effect = post
        runner = main.ReadingRunner(headers={}, cookies={'wr_skey': 'OLD', 'wr_vid': '1'},
            payload=main.config.data, read_num=1, books=['book'], chapters=['chapter'],
            client=client, sleep=Mock(), clock=lambda: 1000)
        return runner, client

    def test_authenticated_endpoints_reject_redirects_without_forwarding_cookies(self):
        session = requests.Session()
        session.trust_env = False
        runner, _ = self.make_runner([])
        runner.client = session
        canary = 'FAKE_COOKIE_ONLY'
        runner.cookies = {'wr_skey': canary, 'wr_vid': 'FAKE_ID'}
        seen = []

        def send(adapter, request, **kwargs):
            seen.append(request)
            result = requests.Response()
            result.request = request
            result.url = request.url
            result.status_code = 302 if request.url != 'http://capture.invalid/collect' else 200
            if result.status_code == 302:
                result.headers['Location'] = 'http://capture.invalid/collect'
            result._content = b'{}'
            return result

        try:
            for url in (main.READ_URL, main.RENEW_URL, main.FIX_SYNCKEY_URL):
                with self.subTest(url=url), patch.object(HTTPAdapter, 'send', new=send):
                    before = len(seen)
                    with self.assertRaises(requests.HTTPError):
                        runner.post(url, {})
                    self.assertEqual(len(seen), before + 1)
                    self.assertEqual(seen[-1].url, url)
                    self.assertIn(canary, seen[-1].headers['Cookie'])
        finally:
            session.close()

    def test_authenticated_endpoints_require_http_2xx(self):
        runner, client = self.make_runner([])
        for status in (199, 300, 304):
            with self.subTest(status=status):
                client.post.return_value = None
                result = requests.Response()
                result.status_code = status
                result._content = b'{}'
                client.post.side_effect = None
                client.post.return_value = result
                with self.assertRaises(requests.HTTPError):
                    runner.post(main.READ_URL, {})

    def test_import_has_no_network_or_logging_side_effects(self):
        with patch('requests.post') as post, patch('main.setup_logging') as logging:
            importlib.reload(main)
        post.assert_not_called()
        logging.assert_not_called()

    def test_non_auth_error_stops_without_extra_renewal(self):
        runner, client = self.make_runner([response({'errCode': -999})])
        with self.assertRaisesRegex(RuntimeError, '不刷新'):
            runner.run()
        urls = [call.args[0] for call in client.post.call_args_list]
        self.assertEqual(urls, [main.RENEW_URL, main.READ_URL])
        runner.sleep.assert_not_called()

    def test_auth_retry_bounded_with_backoff(self):
        runner, client = self.make_runner([response({'errCode': -2012})] * 10)
        with self.assertRaisesRegex(RuntimeError, '连续恢复'):
            runner.run()
        self.assertEqual(runner.sleep.call_args_list, [call(5), call(10), call(15)])
        self.assertEqual(sum(call.args[0] == main.READ_URL for call in client.post.call_args_list), 4)

    def test_mixed_recoveries_share_budget(self):
        runner, _ = self.make_runner([
            response({'errCode': -2012}), response({'succ': 1}),
            response({'errCode': -2013}), response({'succ': 1}),
        ])
        with self.assertRaisesRegex(RuntimeError, '连续恢复'):
            runner.run()

    def test_synckey_retry_bounded(self):
        runner, client = self.make_runner([response({'succ': 1})] * 10)
        with self.assertRaisesRegex(RuntimeError, '连续恢复'):
            runner.run()
        self.assertEqual(sum(call.args[0] == main.FIX_SYNCKEY_URL for call in client.post.call_args_list), 3)

    def test_success_uses_copy_and_renews_cookies_in_memory(self):
        before = copy.deepcopy(main.config.data)
        runner, _ = self.make_runner([response({'succ': 1, 'synckey': 123})])
        self.assertEqual(runner.run(), 1)
        self.assertEqual(main.config.data, before)
        self.assertEqual(runner.cookies['wr_skey'], 'NEWKEY12')
        runner.sleep.assert_called_once_with(30)

    def test_accepted_rt_tracks_submitted_seconds_not_half_minutes(self):
        runner, client = self.make_runner([response({'succ': 1, 'synckey': 123})] * 2)
        runner.read_num = 2
        runner.clock = Mock(side_effect=[1000, 1012, 1057])
        runner.progress = Mock()
        submitted = []
        original_post = client.post.side_effect
        def post(url, **kwargs):
            if url == main.READ_URL:
                submitted.append(json.loads(kwargs['data'])['rt'])
            return original_post(url, **kwargs)
        client.post.side_effect = post
        with self.assertLogs(level='INFO') as logs:
            self.assertIs(type(runner.run()), int)
        self.assertEqual(submitted, [42, 45])
        self.assertEqual(runner.accepted_count, 2)
        self.assertEqual(runner.accepted_seconds, 87)
        self.assertIn('已受理 2 次', logs.output[-1])
        self.assertIn('rt 合计 87 秒；实际入账未验证', logs.output[-1])
        self.assertEqual(runner.sleep.call_args_list, [call(30), call(30)])
        messages = [item.args[0] for item in runner.progress.call_args_list]
        self.assertEqual(len(messages), 4)
        for message, count, seconds in zip(messages, (0, 1, 1, 2), (0, 42, 42, 87)):
            self.assertIn(f'已受理 {count} 次', message)
            self.assertIn(f'rt 合计 {seconds} 秒', message)
            self.assertIn('实际入账未验证', message)
            self.assertNotIn('分钟', message)

    def test_recovery_requests_do_not_count_as_accepted_rt(self):
        runner, _ = self.make_runner([
            response({'errCode': -2012}), response({'succ': 1, 'synckey': 123}),
            response({'succ': 1}), response({'succ': 1, 'synckey': 456}),
        ])
        runner.read_num = 2
        runner.clock = Mock(side_effect=[1000, 1010, 1025, 1040, 1070])
        self.assertEqual(runner.run(), 2)
        self.assertEqual(runner.accepted_count, 2)
        self.assertEqual(runner.accepted_seconds, 100)
        self.assertEqual(runner.sleep.call_args_list, [call(5), call(30), call(5), call(30)])

    def test_rejected_requests_leave_accepted_records_empty(self):
        for result in (response({'succ': 0, 'synckey': 123}),
                       response({'succ': 1, 'synckey': 123}, 500),
                       response({'errCode': -999}), response([])):
            with self.subTest(result=result):
                runner, _ = self.make_runner([result])
                with self.assertRaises(RuntimeError):
                    runner.run()
                self.assertEqual(runner.accepted_count, 0)
                self.assertEqual(runner.accepted_seconds, 0)

    def test_partial_failure_preserves_accepted_records(self):
        runner, _ = self.make_runner([
            response({'succ': 1, 'synckey': 123}), response({'succ': 0}),
        ])
        runner.read_num = 2
        runner.clock = Mock(side_effect=[1000, 1012, 1057])
        with self.assertRaises(RuntimeError):
            runner.run()
        self.assertEqual(runner.accepted_count, 1)
        self.assertEqual(runner.accepted_seconds, 42)

    def test_clock_rollback_does_not_subtract_accepted_seconds_or_change_rt(self):
        for this_time, rt in ((970, 0), (960, -10)):
            with self.subTest(rt=rt):
                runner, client = self.make_runner([response({'succ': 1, 'synckey': 123})])
                runner.clock = Mock(side_effect=[1000, this_time])
                self.assertEqual(runner.run(), 1)
                self.assertEqual(runner.accepted_count, 1)
                self.assertEqual(runner.accepted_seconds, 0)
                self.assertEqual(json.loads(client.post.call_args.kwargs['data'])['rt'], rt)

    def test_repeated_run_resets_accepted_records(self):
        runner, _ = self.make_runner([response({'succ': 1, 'synckey': 123})] * 2)
        self.assertEqual(runner.accepted_count, 0)
        self.assertEqual(runner.accepted_seconds, 0)
        runner.clock = Mock(side_effect=[1000, 1012, 2000, 2001])
        self.assertEqual(runner.run(), 1)
        self.assertEqual(runner.accepted_seconds, 42)
        self.assertEqual(runner.run(), 1)
        self.assertEqual(runner.accepted_count, 1)
        self.assertEqual(runner.accepted_seconds, 31)

    def test_succ_zero_is_not_success(self):
        runner, _ = self.make_runner([response({'succ': 0, 'synckey': 123})])
        with self.assertRaisesRegex(RuntimeError, '业务失败'):
            runner.run()

    def test_http_error_not_accepted_as_success(self):
        runner, _ = self.make_runner([response({'succ': 1, 'synckey': 123}, 500)])
        with self.assertRaisesRegex(RuntimeError, 'HTTP 错误'):
            runner.run()

    def test_non_object_and_invalid_json_fail(self):
        for body in ([], None, 'string'):
            with self.subTest(body=body):
                runner, _ = self.make_runner([response(body)])
                with self.assertRaisesRegex(RuntimeError, '无效 JSON'):
                    runner.run()
        bad = response(None)
        bad.json.side_effect = ValueError('FAKE_SECRET')
        runner, _ = self.make_runner([bad])
        with self.assertRaisesRegex(RuntimeError, '非 JSON'):
            runner.run()

    def test_request_error_redacted(self):
        runner, client = self.make_runner([])
        client.post.side_effect = requests.ConnectionError('FAKE_SECRET_URL')
        with self.assertRaises(RuntimeError) as exc:
            runner.run()
        self.assertNotIn('FAKE_SECRET', str(exc.exception))

    def run_entry(self, completed=1, error=None, method='telegram', accepted=False, seconds=42):
        with patch('main.setup_logging'), patch('main.config.validate_config'), \
             patch('main.config.PUSH_METHOD', method), \
             patch('main.ReadingRunner') as cls, patch('main.push', return_value=accepted) as push:
            cls.return_value.run.return_value = completed
            cls.return_value.run.side_effect = error
            cls.return_value.accepted_seconds = seconds
            result = main.main()
        return result, push

    def test_notification_failure_has_distinct_exit(self):
        status, push = self.run_entry()
        self.assertEqual(status, 2)
        self.assertTrue(push.call_args.kwargs['is_success'])

    def test_completion_notification_reports_requests_not_credited_duration(self):
        status, push = self.run_entry(completed=2, seconds=87, accepted=True)
        self.assertEqual(status, 0)
        push.assert_called_once_with(
            '微信读书阅读请求已完成。\n已受理 2 次，已受理请求提交的 rt 合计 87 秒。\n实际入账未验证。',
            'telegram', is_success=True,
        )

    def test_reading_failure_notifies_once(self):
        status, push = self.run_entry(error=RuntimeError('fixed diagnostic'))
        self.assertEqual(status, 1)
        push.assert_called_once_with('fixed diagnostic', 'telegram', is_success=False)

    def test_real_runner_completion_reaches_notification_offline(self):
        runner, _ = self.make_runner([response({'succ': 1, 'synckey': 123})])
        runner.clock = Mock(side_effect=[1000, 1012])
        with patch('main.setup_logging'), patch('main.config.validate_config'), \
             patch('main.config.PUSH_METHOD', 'telegram'), \
             patch('main.ReadingRunner', return_value=runner), \
             patch('main.push', return_value=True) as notify:
            self.assertEqual(main.main(), 0)
        notify.assert_called_once_with(
            '微信读书阅读请求已完成。\n已受理 1 次，已受理请求提交的 rt 合计 42 秒。\n实际入账未验证。',
            'telegram', is_success=True,
        )

    def test_two_runs_start_from_config_and_use_renewal_in_memory(self):
        initial = {'wr_skey': 'initial', 'wr_vid': 'vid'}
        observed = []
        def request(url, **kwargs):
            observed.append((url, dict(kwargs['cookies'])))
            if url == main.RENEW_URL:
                return response({}, cookies={'wr_skey': 'renewed_key'})
            return response({'succ': 1, 'synckey': 1})
        with patch.object(main.config, 'cookies', initial), \
             patch.object(main.config, 'READ_NUM', 1), \
             patch.object(main.config, 'PUSH_METHOD', ''), \
             patch.object(main, 'setup_logging', return_value=Mock()), \
             patch.object(main.requests, 'post', side_effect=request), \
             patch.object(main.time, 'sleep'):
            for _ in range(2):
                self.assertEqual(main.main(), 0)
        self.assertEqual(initial, {'wr_skey': 'initial', 'wr_vid': 'vid'})
        self.assertEqual(observed, [
            (main.RENEW_URL, initial),
            (main.READ_URL, {'wr_skey': 'renewed_', 'wr_vid': 'vid'}),
        ] * 2)

    def test_entrypoint_invalid_config_fails_without_network(self):
        import os
        import subprocess
        import sys
        env = {**os.environ, 'WXREAD_CURL_BASH': '', 'PUSH_METHOD': '', 'READ_NUM': '1'}
        result = subprocess.run([sys.executable, main.__file__], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Required cookie', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_real_request_preparation_error_is_redacted(self):
        runner, _ = self.make_runner([])
        runner.client = requests
        runner.cookies = {'wr_skey': 'SECRET_CANARY\nBAD', 'wr_vid': '1'}
        from http.client import HTTPConnection
        def validate_headers(session, prepared, **kwargs):
            connection = HTTPConnection('localhost')
            connection.putrequest('POST', '/')
            for name, value in prepared.headers.items():
                connection.putheader(name, value)
            raise AssertionError('invalid cookie must be rejected before connecting')
        with patch('requests.sessions.Session.send', new=validate_headers):
            with self.assertRaises(RuntimeError) as caught:
                runner.post(main.RENEW_URL, {})
        self.assertNotIn('SECRET_CANARY', str(caught.exception))

    def test_control_cookie_never_leaks_to_log_or_notification(self):
        with patch.object(main.config, 'cookies', {'wr_skey': 'SECRET_CANARY\nBAD', 'wr_vid': '1'}), patch.object(main.config, 'PUSH_METHOD', 'telegram'), patch.object(main, 'push', return_value=True) as notify, patch.object(main, 'setup_logging', return_value=Mock()):
            with self.assertLogs(level='ERROR') as logs:
                self.assertEqual(main.main(), 1)
            self.assertNotIn('SECRET_CANARY', ' '.join(logs.output))
            self.assertNotIn('SECRET_CANARY', str(notify.call_args))

    def test_startup_bad_environment_is_handled_without_traceback(self):
        import os
        import subprocess
        import sys
        for values in ({'READ_NUM': 'oops'}, {'WXREAD_CURL_BASH': 'curl -b "BROKEN"'}):
            env = {**os.environ, 'WXREAD_CURL_BASH': '', 'READ_NUM': '1', 'PUSH_METHOD': '', **values}
            result = subprocess.run([sys.executable, main.__file__], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('Traceback', result.stderr)

    def test_no_channel_success(self):
        status, push = self.run_entry(method='')
        self.assertEqual(status, 0)
        push.assert_not_called()


if __name__ == '__main__':
    unittest.main()
