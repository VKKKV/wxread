"""微信读书入口；import 不产生网络、日志配置或阅读副作用。"""
import copy
import hashlib
import json
import logging
import random
import time
import urllib.parse

import requests

import config
from log_utils import setup_logging
from push import push

KEY = "3c5c8717f3daf09iop3423zafeqoi"
READ_URL = "https://weread.qq.com/web/book/read"
RENEW_URL = "https://weread.qq.com/web/login/renewal"
FIX_SYNCKEY_URL = "https://weread.qq.com/web/book/chapterInfos"
COOKIE_DATA_VARIANTS = [
    {"rq": "%2Fweb%2Fbook%2Fread", "ql": False},
    {"rq": "%2Fweb%2Fbook%2Fread", "ql": True},
    {"rq": "%2Fweb%2Fbook%2Fread"},
]
REQUEST_TIMEOUT = (10, 30)
RECOVERY_LIMIT = 3
RECOVERY_DELAY = 5
AUTH_ERROR_CODES = {-2012, -2013}


def encode_data(data):
    return '&'.join(f"{k}={urllib.parse.quote(str(data[k]), safe='')}" for k in sorted(data))


def cal_hash(input_string):
    first = second = 0x15051505
    length = len(input_string)
    index = length - 1
    while index > 0:
        first = 0x7fffffff & (first ^ ord(input_string[index]) << (length - index) % 30)
        second = 0x7fffffff & (second ^ ord(input_string[index - 1]) << index % 30)
        index -= 2
    return hex(first + second)[2:].lower()


class ReadingRunner:
    """每次运行拥有独立状态；所有恢复分支共享连续失败预算。"""

    def __init__(self, *, headers, cookies, payload, read_num, books, chapters,
                 client=None, sleep=None, clock=None, progress=None):
        if not isinstance(read_num, int) or isinstance(read_num, bool) or read_num <= 0:
            raise ValueError("READ_NUM 必须为正整数。")
        if not books or not chapters:
            raise ValueError("书籍和章节列表不能为空。")
        self.headers = dict(headers)
        self.cookies = dict(cookies)
        self.payload = copy.deepcopy(payload)
        self.read_num = read_num
        self.books = books
        self.chapters = chapters
        self.client = requests if client is None else client
        self.sleep = time.sleep if sleep is None else sleep
        self.clock = time.time if clock is None else clock
        self.progress = progress or (lambda message: None)
        self.accepted_count = 0
        self.accepted_seconds = 0

    def post(self, url, payload):
        try:
            # Fixed authenticated endpoints must never forward cookies to a redirect target.
            response = self.client.post(url, headers=self.headers, cookies=self.cookies,
                                        data=json.dumps(payload, separators=(',', ':')),
                                        timeout=REQUEST_TIMEOUT, allow_redirects=False)
            # Reject informational/redirect responses before their bodies or cookies are used.
            # Keep 4xx/5xx handling in callers so existing auth-recovery semantics remain intact.
            if response.status_code < 200 or 300 <= response.status_code < 400:
                raise requests.HTTPError("Unexpected HTTP status", response=response)
            return response
        except ValueError:
            raise RuntimeError("请求参数格式无效，已隐藏敏感内容。") from None

    def refresh_cookie(self, strict=True):
        logging.info("刷新 cookie")
        for payload in COOKIE_DATA_VARIANTS:
            try:
                response = self.post(RENEW_URL, payload)
                response.raise_for_status()
                new_skey = response.cookies.get('wr_skey')
                if new_skey:
                    self.cookies.update(response.cookies.get_dict())
                    # 保留既有逆向协议的八字符 skey，勿凭猜测改变签名协议。
                    self.cookies['wr_skey'] = new_skey[:8]
                    logging.info("cookie 刷新成功。")
                    return True
            except requests.RequestException as exc:
                logging.warning("cookie 刷新请求失败（%s）。", type(exc).__name__)
        if strict:
            raise RuntimeError("无法获取新密钥，当前登录状态可能已失效。")
        logging.warning("启动刷新未成功，保留现有 cookie 尝试阅读。")
        return False

    def run(self):
        self.accepted_count = self.accepted_seconds = 0
        self.refresh_cookie(strict=False)
        last_time = int(self.clock()) - 30
        completed = recoveries = 0
        logging.info("目标受理 %d 次；实际入账未验证。", self.read_num)
        while completed < self.read_num:
            payload = self.payload
            payload.pop('s', None)
            payload['b'] = random.choice(self.books)
            payload['c'] = random.choice(self.chapters)
            this_time = int(self.clock())
            payload['ct'] = this_time
            payload['rt'] = this_time - last_time
            payload['ts'] = this_time * 1000 + random.randint(0, 1000)
            payload['rn'] = random.randint(0, 1000)
            payload['sg'] = hashlib.sha256(f"{payload['ts']}{payload['rn']}{KEY}".encode()).hexdigest()
            payload['s'] = cal_hash(encode_data(payload))
            submitted_rt = payload['rt']
            self.progress(f"请求进度: 第 {completed + 1}/{self.read_num} 次，已受理 {completed} 次，已受理请求提交的 rt 合计 {self.accepted_seconds} 秒；实际入账未验证。")
            try:
                response = self.post(READ_URL, payload)
                result = response.json()
            except requests.RequestException as exc:
                raise RuntimeError(f"阅读请求失败（{type(exc).__name__}）。") from None
            except ValueError:
                raise RuntimeError("read 接口返回非 JSON 响应。") from None
            if not isinstance(result, dict):
                raise RuntimeError("read 接口返回无效 JSON 结构。")
            # 不回显上游 errMsg 或响应体，避免反射凭据到日志/通知。
            err_code = result.get('errCode')
            is_auth_error = isinstance(err_code, int) and err_code in AUTH_ERROR_CODES
            if is_auth_error:
                recovery = 'auth'
            else:
                try:
                    response.raise_for_status()
                except requests.RequestException:
                    raise RuntimeError(f"read 接口 HTTP 错误（{response.status_code}）。") from None
                if result.get('succ') != 1:
                    safe_code = err_code if isinstance(err_code, int) else 'unknown'
                    raise RuntimeError(f"read 接口业务失败（errCode={safe_code}），不刷新 cookie。")
                recovery = 'synckey' if result.get('synckey') is None else None
            if recovery is not None:
                recoveries += 1
                if recoveries > RECOVERY_LIMIT:
                    raise RuntimeError(f"连续恢复 {RECOVERY_LIMIT} 次未成功，终止运行。")
                logging.warning("尝试恢复 %s（%d/%d）。", recovery, recoveries, RECOVERY_LIMIT)
                self.sleep(RECOVERY_DELAY * recoveries)
                if recovery == 'auth':
                    self.refresh_cookie()
                else:
                    try:
                        self.post(FIX_SYNCKEY_URL, {"bookIds": ["3300060341"]}).raise_for_status()
                    except requests.RequestException as exc:
                        raise RuntimeError(f"synckey 修复请求失败（{type(exc).__name__}）。") from None
                continue
            recoveries = 0
            last_time = this_time
            completed += 1
            self.accepted_count = completed
            # 仅记录已受理请求提交的合法 rt；这不是服务端入账统计。
            if type(submitted_rt) is int and submitted_rt >= 0:
                self.accepted_seconds += submitted_rt
            self.sleep(30)
            self.progress(f"请求进度: 第 {completed}/{self.read_num} 次，已受理 {completed} 次，已受理请求提交的 rt 合计 {self.accepted_seconds} 秒；实际入账未验证。")
        logging.info("阅读脚本已完成：已受理 %d 次，已受理请求提交的 rt 合计 %d 秒；实际入账未验证。",
                     self.accepted_count, self.accepted_seconds)
        return completed


def main():
    progress = setup_logging()
    method = config.PUSH_METHOD
    try:
        config.validate_config()
        runner = ReadingRunner(
            headers=config.headers, cookies=config.cookies, payload=config.data,
            read_num=config.READ_NUM, books=config.book, chapters=config.chapter,
            progress=progress,
        )
        completed = runner.run()
    except (ValueError, RuntimeError) as exc:
        # 自有异常只含固定诊断，底层请求错误必须先脱敏。
        message = str(exc)
        logging.error("%s", message)
        if method and not push(message, method, is_success=False):
            logging.error("阅读失败，且失败通知未被推送服务受理。")
        return 1
    if method:
        accepted = push(f"微信读书阅读请求已完成。\n已受理 {completed} 次，已受理请求提交的 rt 合计 {runner.accepted_seconds} 秒。\n实际入账未验证。", method, is_success=True)
        if not accepted:
            logging.error("阅读已完成，但完成通知未被推送服务受理。")
            return 2
        logging.info("完成通知已被推送服务受理（不代表最终送达）。")
    else:
        logging.info("未配置推送渠道，跳过推送。")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
