"""WebDavClient против локального WebDAV-сервера (tests/webdav_fake.py)."""
import ssl
import unittest
import urllib.error
from unittest import mock

from src.webdav import WebDavClient, WebDavError
from webdav_fake import FakeWebDavServer


class WebDavClientTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeWebDavServer()
        self.client = WebDavClient(self.server.url, "user", "secret", timeout=5)

    def tearDown(self):
        self.server.stop()

    def test_put_get_roundtrip_binary(self):
        self.client.ensure_dir("OzonSorter")
        payload = bytes(range(256)) * 10
        self.client.put("OzonSorter/base.db.gz", payload)
        self.assertEqual(self.client.get("OzonSorter/base.db.gz"), payload)

    def test_get_missing_file_returns_none(self):
        self.assertIsNone(self.client.get("OzonSorter/nope.bin"))

    def test_ensure_dir_creates_nested_and_is_idempotent(self):
        self.client.ensure_dir("OzonSorter/backups")
        self.client.ensure_dir("OzonSorter/backups")
        self.assertIn("/OzonSorter/backups", self.server.dirs)

    def test_list_dir_returns_names_without_the_dir_itself(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/a.bin", b"1")
        self.client.put("OzonSorter/b.bin", b"2")
        self.assertEqual(sorted(self.client.list_dir("OzonSorter")), ["a.bin", "b.bin"])

    def test_list_dir_missing_returns_empty(self):
        self.assertEqual(self.client.list_dir("НетТакой"), [])

    def test_cyrillic_and_spaces_in_path(self):
        self.client.ensure_dir("Озон Сортер")
        self.client.put("Озон Сортер/база 1.bin", b"x")
        self.assertEqual(self.client.list_dir("Озон Сортер"), ["база 1.bin"])
        self.assertEqual(self.client.get("Озон Сортер/база 1.bin"), b"x")

    def test_delete_existing_and_missing(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/a.bin", b"1")
        self.client.delete("OzonSorter/a.bin")
        self.assertIsNone(self.client.get("OzonSorter/a.bin"))
        self.client.delete("OzonSorter/a.bin")  # повторно — не ошибка

    def test_wrong_password_is_auth_error(self):
        bad = WebDavClient(self.server.url, "user", "wrong", timeout=5)
        with self.assertRaises(WebDavError) as ctx:
            bad.list_dir("OzonSorter")
        self.assertTrue(ctx.exception.is_auth_error)
        self.assertFalse(ctx.exception.is_network_error)

    def test_put_into_missing_dir_raises_with_status(self):
        with self.assertRaises(WebDavError) as ctx:
            self.client.put("НетПапки/a.bin", b"1")
        self.assertEqual(ctx.exception.status, 409)

    def test_server_error_raises(self):
        self.client.ensure_dir("OzonSorter")
        self.server.fail_next["PUT"] = 507
        with self.assertRaises(WebDavError) as ctx:
            self.client.put("OzonSorter/a.bin", b"1")
        self.assertEqual(ctx.exception.status, 507)

    def test_unreachable_server_is_network_error(self):
        url = self.server.url
        self.server.stop()
        dead = WebDavClient(url, "user", "secret", timeout=2)
        try:
            with self.assertRaises(WebDavError) as ctx:
                dead.get("OzonSorter/a.bin")
            self.assertTrue(ctx.exception.is_network_error)
        finally:
            self.server = FakeWebDavServer()  # чтобы tearDown было что останавливать

    def test_plain_http_to_remote_host_rejected(self):
        with self.assertRaises(ValueError):
            WebDavClient("http://webdav.yandex.ru", "user", "secret")

    def test_https_url_accepted(self):
        WebDavClient("https://webdav.yandex.ru", "user", "secret")

    def test_non_http_schemes_rejected_even_for_loopback(self):
        for url in ("ftp://127.0.0.1", "file://localhost/tmp", "ws://127.0.0.1"):
            with self.assertRaises(ValueError, msg=url):
                WebDavClient(url, "user", "secret")

    # --- переадресации: пароль не должен уйти на другой адрес ---

    def test_password_not_sent_after_redirect(self):
        other = FakeWebDavServer(login="user", password="secret")
        try:
            self.client.ensure_dir("OzonSorter")
            self.client.put("OzonSorter/a.bin", b"1")
            self.server.redirects[("GET", "/OzonSorter/a.bin")] = (
                other.url.replace("127.0.0.1", "localhost") + "/OzonSorter/a.bin")
            with self.assertRaises(WebDavError):
                self.client.get("OzonSorter/a.bin")
            # На втором адресе запрос был, но без Authorization.
            self.assertEqual(other.auth_seen, [None])
        finally:
            other.stop()

    def test_redirect_to_insecure_remote_host_refused(self):
        self.server.redirects[("GET", "/OzonSorter/a.bin")] = "http://example.com/a.bin"
        with self.assertRaises(WebDavError) as ctx:
            self.client.get("OzonSorter/a.bin")
        self.assertEqual(ctx.exception.status, 302)

    def test_authorization_sent_on_normal_requests(self):
        self.client.ensure_dir("OzonSorter")
        self.assertTrue(all(a and a.startswith("Basic ") for a in self.server.auth_seen))

    # --- ошибки соединения ---

    def test_tls_failure_is_not_reported_as_network_error(self):
        # Ошибка проверки сертификата не лечится включением интернета, поэтому
        # отличается от обычного обрыва связи.
        error = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        with mock.patch.object(self.client._opener, "open", side_effect=error):
            with self.assertRaises(WebDavError) as ctx:
                self.client.get("OzonSorter/a.bin")
        self.assertTrue(ctx.exception.is_tls_error)
        self.assertFalse(ctx.exception.is_network_error)

    def test_tls_connection_drop_is_network_error_so_retry_is_offered(self):
        # Обрыв связи посреди TLS-передачи (Wi-Fi пропал) — не проблема сертификатов.
        for reason in (ssl.SSLEOFError("EOF occurred in violation of protocol"),
                       ssl.SSLZeroReturnError("TLS/SSL connection has been closed"),
                       ssl.SSLError("handshake failed")):
            with self.subTest(reason=type(reason).__name__):
                for error in (urllib.error.URLError(reason), reason):
                    with mock.patch.object(self.client._opener, "open", side_effect=error):
                        with self.assertRaises(WebDavError) as ctx:
                            self.client.get("OzonSorter/a.bin")
                    self.assertTrue(ctx.exception.is_network_error)
                    self.assertFalse(ctx.exception.is_tls_error)

    def test_truncated_response_is_network_error_not_short_data(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/a.bin", b"x" * 1000)
        self.server.truncate_gets = True
        with self.assertRaises(WebDavError) as ctx:
            self.client.get("OzonSorter/a.bin")
        self.assertTrue(ctx.exception.is_network_error)
        self.server.truncate_gets = False
        self.assertEqual(len(self.client.get("OzonSorter/a.bin")), 1000)

    def test_dropped_connection_is_network_error(self):
        self.server.offline = True
        with self.assertRaises(WebDavError) as ctx:
            self.client.get("OzonSorter/a.bin")
        self.assertTrue(ctx.exception.is_network_error)

    def test_http_protocol_error_is_wrapped(self):
        import http.client
        with mock.patch.object(self.client._opener, "open",
                               side_effect=http.client.IncompleteRead(b"x")):
            with self.assertRaises(WebDavError) as ctx:
                self.client.get("OzonSorter/a.bin")
        self.assertTrue(ctx.exception.is_network_error)

    # --- пределы размера ответа ---

    def test_response_over_limit_rejected(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/big.bin", b"x" * 5000)
        with self.assertRaises(WebDavError):
            self.client.get("OzonSorter/big.bin", max_bytes=1000)
        self.assertEqual(len(self.client.get("OzonSorter/big.bin", max_bytes=5000)), 5000)

    def test_default_response_limit_applies_to_small_requests(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/big.bin", b"x" * (9 * 1024 * 1024))
        with self.assertRaises(WebDavError):
            self.client.get("OzonSorter/big.bin")


if __name__ == "__main__":
    unittest.main()
