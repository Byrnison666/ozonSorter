"""YaDiskClient против локального «Яндекс.Диска» (tests/yadisk_fake.py)."""
import http.client
import ssl
import unittest
from unittest import mock

from src import yadisk_api
from src.yadisk_api import YaDiskClient, YaDiskError, http_request, is_secure_url
from yadisk_fake import FakeYaDiskServer

BIG = 200 * 1024        # больше порога прямой выгрузки — пойдёт через .tmp


class YaDiskClientTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeYaDiskServer()
        self.client = YaDiskClient(self.server.token, self.server.api_url, timeout=5)

    def tearDown(self):
        self.server.stop()

    def _paths(self, method):
        return [path for m, path in self.server.requests if m == method]

    # --- основные операции ---

    def test_put_get_roundtrip_binary(self):
        self.client.ensure_dir("OzonSorter")
        payload = bytes(range(256)) * 10
        self.client.put("OzonSorter/base.db.gz", payload)
        self.assertEqual(self.client.get("OzonSorter/base.db.gz"), payload)

    def test_small_file_is_uploaded_under_its_own_name(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/meta.json", b"{}")
        self.assertNotIn("/v1/disk/resources/move", self._paths("POST"))
        self.assertEqual(self.server.files["/OzonSorter/meta.json"], b"{}")

    def test_big_file_goes_through_temp_name_and_server_move(self):
        self.client.ensure_dir("OzonSorter")
        payload = b"z" * BIG
        self.client.put("OzonSorter/base.db.gz", payload)
        self.assertIn("/v1/disk/resources/move", self._paths("POST"))
        self.assertEqual(self.server.files, {"/OzonSorter/base.db.gz": payload})

    def test_big_file_replaces_existing(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/base.db.gz", b"a" * BIG)
        self.client.put("OzonSorter/base.db.gz", b"b" * BIG)
        self.assertEqual(self.client.get("OzonSorter/base.db.gz"), b"b" * BIG)

    def test_failed_move_leaves_real_name_untouched(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/base.db.gz", b"a" * BIG)
        self.server.fail_next["POST"] = 500
        with self.assertRaises(YaDiskError) as ctx:
            self.client.put("OzonSorter/base.db.gz", b"b" * BIG)
        self.assertEqual(ctx.exception.status, 500)
        self.assertEqual(self.server.files["/OzonSorter/base.db.gz"], b"a" * BIG)

    def test_async_move_and_copy_are_awaited(self):
        self.server.async_ops = True
        self.client.ensure_dir("OzonSorter/backups")
        self.client.put("OzonSorter/base.db.gz", b"c" * BIG)
        self.client.copy("OzonSorter/base.db.gz", "OzonSorter/backups/copy.db.gz")
        self.assertEqual(self.server.files["/OzonSorter/backups/copy.db.gz"], b"c" * BIG)
        self.assertTrue(any(p.startswith("/v1/disk/operations/") for p in self._paths("GET")))

    def test_copy_does_not_replace_existing_target(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/a.bin", b"new")
        self.client.put("OzonSorter/b.bin", b"old")
        self.client.copy("OzonSorter/a.bin", "OzonSorter/b.bin")
        self.assertEqual(self.client.get("OzonSorter/b.bin"), b"old")

    def test_get_missing_file_returns_none(self):
        self.assertIsNone(self.client.get("OzonSorter/nope.bin"))

    def test_ensure_dir_creates_nested_and_is_idempotent(self):
        self.client.ensure_dir("OzonSorter/backups")
        self.client.ensure_dir("OzonSorter/backups")
        self.assertIn("/OzonSorter/backups", self.server.dirs)

    def test_list_dir_returns_names(self):
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

    def test_app_folder_root_paths(self):
        # Боевая папка синхронизации — корень app:/.
        self.client.put("meta.json", b"{}")
        self.assertEqual(self.client.list_dir(""), ["meta.json"])

    def test_delete_existing_and_missing(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/a.bin", b"1")
        self.client.delete("OzonSorter/a.bin")
        self.assertIsNone(self.client.get("OzonSorter/a.bin"))
        self.client.delete("OzonSorter/a.bin")  # повторно — не ошибка

    # --- ошибки API ---

    def test_bad_token_is_auth_error(self):
        bad = YaDiskClient("wrong", self.server.api_url, timeout=5)
        with self.assertRaises(YaDiskError) as ctx:
            bad.list_dir("OzonSorter")
        self.assertTrue(ctx.exception.is_auth_error)
        self.assertFalse(ctx.exception.is_network_error)

    def test_forbidden_is_auth_error(self):
        self.server.fail_next["GET"] = 403
        with self.assertRaises(YaDiskError) as ctx:
            self.client.list_dir("OzonSorter")
        self.assertTrue(ctx.exception.is_auth_error)

    def test_put_into_missing_dir_raises_with_status(self):
        with self.assertRaises(YaDiskError) as ctx:
            self.client.put("НетПапки/a.bin", b"1")
        self.assertEqual(ctx.exception.status, 409)

    def test_insufficient_storage_keeps_status(self):
        self.client.ensure_dir("OzonSorter")
        self.server.fail_next["GET"] = 507
        with self.assertRaises(YaDiskError) as ctx:
            self.client.put("OzonSorter/a.bin", b"1")
        self.assertEqual(ctx.exception.status, 507)

    def test_unreachable_server_is_network_error(self):
        api_url = self.server.api_url
        self.server.stop()
        dead = YaDiskClient("t", api_url, timeout=2)
        try:
            with self.assertRaises(YaDiskError) as ctx:
                dead.get("OzonSorter/a.bin")
            self.assertTrue(ctx.exception.is_network_error)
        finally:
            self.server = FakeYaDiskServer()  # чтобы tearDown было что останавливать

    # --- адреса и токен ---

    def test_plain_http_to_remote_host_rejected(self):
        with self.assertRaises(ValueError):
            YaDiskClient("t", "http://cloud-api.yandex.net/v1/disk")

    def test_https_and_loopback_urls_accepted(self):
        YaDiskClient("t", "https://cloud-api.yandex.net/v1/disk")
        self.assertTrue(is_secure_url("http://localhost:8080/x"))
        self.assertTrue(is_secure_url("http://[::1]:8080/x"))

    def test_non_http_schemes_rejected_even_for_loopback(self):
        for url in ("ftp://127.0.0.1", "file://localhost/tmp", "ws://127.0.0.1"):
            with self.assertRaises(ValueError, msg=url):
                YaDiskClient("t", url)

    def test_token_sent_to_api_only(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/a.bin", b"x" * BIG)
        self.assertEqual(self.client.get("OzonSorter/a.bin"), b"x" * BIG)
        # Выгрузка, ссылка скачивания и хранилище после переадресации — без токена.
        self.assertEqual(len(self.server.auth_seen), 3)
        self.assertEqual(set(self.server.auth_seen), {None})

    def test_operation_on_foreign_host_does_not_get_token(self):
        response = yadisk_api.Response(202, {}, b'{"href": "https://evil.example/op/1"}')
        with mock.patch.object(yadisk_api, "http_request", return_value=response) as request:
            with self.assertRaises(YaDiskError):
                self.client.copy("a.bin", "b.bin")
        request.assert_called_once()        # только сам запрос copy, без опроса чужого адреса

    def test_download_redirect_to_insecure_host_refused(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/a.bin", b"1")
        self.server.download_location = "http://example.com/a.bin"
        with self.assertRaises(YaDiskError) as ctx:
            self.client.get("OzonSorter/a.bin")
        self.assertIn("insecure", str(ctx.exception))

    def test_api_redirect_is_not_followed(self):
        # Токен не должен уйти по переадресации — запросы к API её не выполняют.
        response = http_request("GET", self.server.url + "/download/1", {}, None, 5)
        self.assertEqual(response.status, 302)
        self.assertEqual(self.server.requests[-1], ("GET", "/download/1"))

    # --- ошибки соединения ---

    def test_tls_failure_is_not_reported_as_network_error(self):
        # Ошибка проверки сертификата не лечится включением интернета, поэтому
        # отличается от обычного обрыва связи.
        error = ssl.SSLCertVerificationError("certificate verify failed")
        with mock.patch.object(yadisk_api, "_exchange", side_effect=error):
            with self.assertRaises(YaDiskError) as ctx:
                self.client.get("OzonSorter/a.bin")
        self.assertTrue(ctx.exception.is_tls_error)
        self.assertFalse(ctx.exception.is_network_error)

    def test_tls_connection_drop_is_network_error_so_retry_is_offered(self):
        # Обрыв связи посреди TLS-передачи (Wi-Fi пропал) — не проблема сертификатов.
        for reason in (ssl.SSLEOFError("EOF occurred in violation of protocol"),
                       ssl.SSLZeroReturnError("TLS/SSL connection has been closed"),
                       ssl.SSLError("handshake failed")):
            with self.subTest(reason=type(reason).__name__):
                with mock.patch.object(yadisk_api, "_exchange", side_effect=reason):
                    with self.assertRaises(YaDiskError) as ctx:
                        self.client.get("OzonSorter/a.bin")
                self.assertTrue(ctx.exception.is_network_error)
                self.assertFalse(ctx.exception.is_tls_error)

    def test_http_protocol_error_is_wrapped(self):
        with mock.patch.object(yadisk_api, "_exchange",
                               side_effect=http.client.IncompleteRead(b"x")):
            with self.assertRaises(YaDiskError) as ctx:
                self.client.get("OzonSorter/a.bin")
        self.assertTrue(ctx.exception.is_network_error)

    def test_truncated_download_is_network_error_not_short_data(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/a.bin", b"x" * 1000)
        self.server.truncate_gets = True
        with self.assertRaises(YaDiskError) as ctx:
            self.client.get("OzonSorter/a.bin")
        self.assertTrue(ctx.exception.is_network_error)
        self.server.truncate_gets = False
        self.assertEqual(len(self.client.get("OzonSorter/a.bin")), 1000)

    def test_dropped_connection_is_network_error(self):
        self.server.offline = True
        with self.assertRaises(YaDiskError) as ctx:
            self.client.get("OzonSorter/a.bin")
        self.assertTrue(ctx.exception.is_network_error)

    # --- пределы размера ответа ---

    def test_response_over_limit_rejected(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/big.bin", b"x" * 5000)
        with self.assertRaises(YaDiskError):
            self.client.get("OzonSorter/big.bin", max_bytes=1000)
        self.assertEqual(len(self.client.get("OzonSorter/big.bin", max_bytes=5000)), 5000)

    def test_default_response_limit_applies(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/big.bin", b"x" * (9 * 1024 * 1024))
        with self.assertRaises(YaDiskError):
            self.client.get("OzonSorter/big.bin")

    # --- ход передачи ---

    def test_upload_progress_reaches_total(self):
        self.client.ensure_dir("OzonSorter")
        calls = []
        self.client.put("OzonSorter/a.bin", b"u" * BIG, progress=lambda d, t: calls.append((d, t)))
        self.assertGreater(len(calls), 1)
        self.assertEqual(calls[-1], (BIG, BIG))
        self.assertEqual([d for d, _ in calls], sorted(d for d, _ in calls))

    def test_download_progress_reaches_total(self):
        self.client.ensure_dir("OzonSorter")
        self.client.put("OzonSorter/a.bin", b"d" * BIG)
        calls = []
        self.client.get("OzonSorter/a.bin", progress=lambda d, t: calls.append((d, t)))
        self.assertGreater(len(calls), 1)
        self.assertEqual(calls[-1], (BIG, BIG))

    def test_service_requests_do_not_report_progress(self):
        # Ход показывается только для самого файла, а не для ответов API.
        self.client.ensure_dir("OzonSorter")
        calls = []
        self.client.put("OzonSorter/a.bin", b"s" * 10, progress=lambda d, t: calls.append(t))
        self.assertEqual(set(calls), {10})


if __name__ == "__main__":
    unittest.main()
