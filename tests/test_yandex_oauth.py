"""Вход в Яндекс по коду устройства — против локального «Яндекса» (tests/yadisk_fake.py)."""
import sys
import time
import unittest
from unittest import mock

from src import yandex_oauth
from src.yadisk_api import Response, YaDiskError
from src.yandex_oauth import (
    DeviceCode, OAuthError, fetch_user, poll_token, refresh_tokens, request_device_code,
)
from yadisk_fake import FakeYaDiskServer


class YandexOAuthTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeYaDiskServer()
        for name, value in (("OAUTH_URL", self.server.oauth_url),
                            ("INFO_URL", self.server.info_url)):
            patcher = mock.patch.object(yandex_oauth, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(yandex_oauth, "_client_secret", return_value="secret")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.server.stop()

    def test_device_code_then_pending_then_tokens(self):
        code = request_device_code("Касса")
        self.assertEqual((code.user_code, code.verification_url), ("TESTCODE", "https://ya.ru/device"))
        self.assertGreater(code.expires_at, time.monotonic())
        self.assertIsNone(poll_token(code))
        self.server.approved = True
        tokens = poll_token(code)
        self.assertEqual((tokens.access_token, tokens.refresh_token),
                         (self.server.token, "refresh-1"))
        self.assertGreater(tokens.expires_at, time.time() + 300 * 86400)

    def test_expired_code_stops_polling(self):
        code = DeviceCode("device-code-1", "TESTCODE", "https://ya.ru/device", 1,
                          time.monotonic() - 1)
        with self.assertRaises(OAuthError) as ctx:
            poll_token(code)
        self.assertEqual(ctx.exception.code, "expired_token")

    def test_denied_code_reports_yandex_error(self):
        response = Response(400, {}, b'{"error": "access_denied"}')
        with mock.patch.object(yandex_oauth, "_post", return_value=response):
            with self.assertRaises(OAuthError) as ctx:
                poll_token(DeviceCode("d", "u", "https://ya.ru/device", 1, time.monotonic() + 60))
        self.assertEqual(ctx.exception.code, "access_denied")

    def test_device_code_with_insecure_verification_url_rejected(self):
        body = (b'{"device_code": "d", "user_code": "u", "verification_url": '
                b'"http://evil.example/device", "interval": 5, "expires_in": 300}')
        with mock.patch.object(yandex_oauth, "_post", return_value=Response(200, {}, body)):
            with self.assertRaises(OAuthError) as ctx:
                request_device_code("Касса")
        self.assertEqual(ctx.exception.code, "bad_response")

    def test_refresh_returns_new_tokens(self):
        tokens = refresh_tokens("refresh-1")
        self.assertEqual(tokens.access_token, self.server.token)

    def test_refresh_with_revoked_token_fails(self):
        with self.assertRaises(OAuthError) as ctx:
            refresh_tokens("revoked")
        self.assertEqual(ctx.exception.code, "invalid_grant")

    def test_refresh_keeps_old_refresh_token_if_none_returned(self):
        body = b'{"access_token": "new", "expires_in": 3600}'
        with mock.patch.object(yandex_oauth, "_post", return_value=Response(200, {}, body)):
            tokens = refresh_tokens("old-refresh")
        self.assertEqual((tokens.access_token, tokens.refresh_token), ("new", "old-refresh"))

    def test_malformed_token_response_rejected(self):
        with mock.patch.object(yandex_oauth, "_post",
                               return_value=Response(200, {}, b'{"access_token": 5}')):
            with self.assertRaises(OAuthError):
                refresh_tokens("refresh-1")

    def test_fetch_user(self):
        self.assertEqual(fetch_user(self.server.token), (self.server.uid, self.server.login))

    def test_fetch_user_with_bad_token_is_auth_error(self):
        with self.assertRaises(YaDiskError) as ctx:
            fetch_user("wrong")
        self.assertTrue(ctx.exception.is_auth_error)

    def test_network_failure_is_yadisk_network_error(self):
        self.server.offline = True
        with self.assertRaises(YaDiskError) as ctx:
            request_device_code("Касса")
        self.assertTrue(ctx.exception.is_network_error)


class ClientSecretTest(unittest.TestCase):
    def test_build_without_secret_reports_not_configured(self):
        # Публичный репозиторий без src/_yandex_secret.py: понятная ошибка, не падение.
        with mock.patch.dict(sys.modules, {"src._yandex_secret": None}):
            with self.assertRaises(OAuthError) as ctx:
                yandex_oauth._client_secret()
        self.assertEqual(ctx.exception.code, "not_configured")


if __name__ == "__main__":
    unittest.main()
