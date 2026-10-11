import logging
import time

import requests

from config import (
    PUSHPLUS_TOKEN,
    SERVERCHAN_SPT,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    WXPUSHER_SPT,
)

logger = logging.getLogger(__name__)


class PushNotification:
    ATTEMPTS = 3
    RETRY_DELAYS = (1, 2)

    def __init__(self):
        self.pushplus_url = "https://www.pushplus.plus/send"
        self.telegram_url = "https://api.telegram.org/bot{}/sendMessage"
        self.server_chan_url = "https://sctapi.ftqq.com/{}.send"
        self.wxpusher_simple_url = "https://wxpusher.zjiecode.com/api/send/message/simple-push"
        self.headers = {"Content-Type": "application/json"}

    @staticmethod
    def _failure(provider, exc):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.error("%s 推送失败 (exception=%s, status=%s)", provider, type(exc).__name__, status)

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _check_status(response):
        response.raise_for_status()
        # Requests does not reject 1xx/3xx; only 2xx means HTTP acceptance.
        if not 200 <= response.status_code < 300:
            raise requests.HTTPError("Notification HTTP status rejected", response=response)

    def _request_with_retries(self, provider, request, success):
        for attempt in range(self.ATTEMPTS):
            try:
                response = request()
                self._check_status(response)
                if success(self._json(response)):
                    return True
                logger.error("%s 推送失败 (business response rejected)", provider)
            except (requests.exceptions.RequestException, ValueError) as exc:
                self._failure(provider, exc)
            if attempt < self.ATTEMPTS - 1:
                time.sleep(self.RETRY_DELAYS[attempt])
        return False

    def push_pushplus(self, content, token, is_success):
        if not token:
            return False
        title = f"微信阅读-{'成功' if is_success else '失败'}"
        payload = {"token": token, "title": title, "content": content}
        return self._request_with_retries(
            "PushPlus",
            lambda: requests.post(self.pushplus_url, json=payload, headers=self.headers, timeout=10, allow_redirects=False),
            lambda data: isinstance(data, dict) and type(data.get("code")) is int and data["code"] == 200,
        )

    def push_telegram(self, content, bot_token, chat_id):
        if not bot_token or not chat_id:
            return False
        url = self.telegram_url.format(bot_token)
        payload = {"chat_id": chat_id, "text": content}

        try:
            # Let Requests discover HTTPS_PROXY/ALL_PROXY/NO_PROXY normally.
            response = requests.post(url, json=payload, timeout=10, allow_redirects=False)
            self._check_status(response)
            data = self._json(response)
            if isinstance(data, dict) and data.get("ok") is True:
                return True
            logger.error("Telegram 推送失败 (business response rejected)")
        except (requests.exceptions.RequestException, ValueError) as exc:
            self._failure("Telegram proxy", exc)

        session = requests.Session()
        session.trust_env = False
        try:
            return self._request_with_retries(
                "Telegram direct",
                lambda: session.post(url, json=payload, timeout=10, allow_redirects=False),
                lambda data: isinstance(data, dict) and data.get("ok") is True,
            )
        finally:
            session.close()

    def push_wxpusher(self, content, spt):
        if not spt:
            return False
        payload = {"spt": spt, "content": content, "contentType": 1}
        return self._request_with_retries(
            "WxPusher",
            lambda: requests.post(self.wxpusher_simple_url, json=payload, headers=self.headers, timeout=10, allow_redirects=False),
            lambda data: isinstance(data, dict) and type(data.get("code")) is int and data["code"] == 1000,
        )

    def push_serverChan(self, content, spt, is_success):
        if not spt:
            return False
        title = f"微信阅读-{'成功' if is_success else '失败'}"
        payload = {"title": title, "desp": content}
        return self._request_with_retries(
            "ServerChan",
            lambda: requests.post(self.server_chan_url.format(spt), json=payload, headers=self.headers, timeout=10, allow_redirects=False),
            lambda data: isinstance(data, dict) and type(data.get("code")) is int and data["code"] == 0,
        )


def push(content, method, is_success=True):
    notifier = PushNotification()
    if method in (None, ""):
        logger.warning("未配置推送渠道，跳过推送。")
        return False
    method = str(method).strip().lower()
    if method == "pushplus":
        return notifier.push_pushplus(content, PUSHPLUS_TOKEN, is_success)
    if method == "telegram":
        return notifier.push_telegram(content, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    if method == "wxpusher":
        return notifier.push_wxpusher(content, WXPUSHER_SPT)
    if method == "serverchan":
        return notifier.push_serverChan(content, SERVERCHAN_SPT, is_success)
    logger.warning("无效的通知渠道，已跳过推送。支持：pushplus、wxpusher、telegram、serverchan")
    return False
