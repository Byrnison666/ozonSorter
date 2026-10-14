"""Сценарий синхронизации для интерфейса: проверка, выгрузка, скачивание,
выбор версии при конфликте, повтор при отсутствии интернета, работа без Диска,
когда он недоступен. Сеть работает в фоновом потоке; на время обмена окно
закрыто модальным индикатором — базу в это время трогать нельзя.
"""
import logging
import socket
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from PySide2.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide2.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from src.sync_config import STATE_PATH, SyncConfig, save_config
from src.sync_service import (
    SAVED_AT_FORMAT, CancelToken, RemoteDataError, SyncCancelled, SyncError, SyncService,
    SyncStatus,
)
from src.yadisk_api import YaDiskClient, YaDiskError
from src.yandex_oauth import OAuthError, refresh_tokens

logger = logging.getLogger(__name__)

# Служебные запросы маленькие; передача файла базы может идти долго. Оба
# таймаута — на одну сетевую операцию, а не на весь обмен.
_CHECK_TIMEOUT = 15
_TRANSFER_TIMEOUT = 300

# Потоки отменённых операций: ещё ждут ответа сети, результат уже не нужен.
_abandoned = set()

# Через VPN Яндекс.Диск обычно недоступен: запросы либо падают, либо висят минутами.
# Определить VPN надёжно нельзя, поэтому подсказка показывается при любом сбое
# связи и при слишком долгом обмене.
VPN_HINT = ("Если на компьютере включён VPN, отключите его: с включённым VPN "
            "Яндекс.Диск обычно недоступен.")
_SLOW_HINT_MS = 10_000
# Передача быстрее этого (байт/с) медленной не считается — подсказку про VPN не показываем.
_SLOW_SPEED = 64 * 1024
# Окно, по которому считается скорость передачи, и как часто обновлять индикатор.
_SPEED_WINDOW = 3.0
_PROGRESS_INTERVAL = 0.1
# Токен действует год; обновляем его за месяц до конца.
_REFRESH_BEFORE = 30 * 24 * 3600
# Любые два адреса, не связанные с Яндексом: если они отвечают, а Яндекс нет —
# проблема именно в доступе к Яндексу (чаще всего VPN).
_PROBE_HOSTS = (("1.1.1.1", 443), ("8.8.8.8", 443))


class SyncOutcome(str, Enum):
    UNCHANGED = "UNCHANGED"
    PUSHED = "PUSHED"
    PULLED = "PULLED"
    DECLINED = "DECLINED"    # пользователь отказался (конфликт, загрузка с Диска)


class SyncAttempt(str, Enum):
    """Итог синхронизации со всеми диалогами."""
    IN_SYNC = "IN_SYNC"            # база совпадает с Диском
    NOT_SYNCED = "NOT_SYNCED"      # отказ, отмена или ошибка (оператору показана)
    UNAVAILABLE = "UNAVAILABLE"    # Диск временно недоступен, работаем с локальной базой


@dataclass(frozen=True)
class SyncResult:
    outcome: SyncOutcome
    text: str

    @property
    def in_sync(self) -> bool:
        return self.outcome != SyncOutcome.DECLINED


def make_sync_service(config: SyncConfig, db_path: str) -> SyncService:
    client = YaDiskClient(config.token, config.api_url, timeout=_CHECK_TIMEOUT)
    return SyncService(db_path, client, config.remote_dir, STATE_PATH, config.device_name,
                       transfer_timeout=_TRANSFER_TIMEOUT, target_id=config.target_id)


def refresh_token_if_needed(config: SyncConfig, service: SyncService) -> None:
    """Обновить токен, если до конца его действия меньше месяца. Сбой не мешает
    синхронизации: старый токен ещё действует, обновим в следующий раз.
    Вызывается в фоновом потоке."""
    if not config.refresh_token or not config.token_expires_at:
        return
    if config.token_expires_at - time.time() > _REFRESH_BEFORE:
        return
    try:
        tokens = refresh_tokens(config.refresh_token)
    except (OAuthError, YaDiskError):
        return
    config.token, config.refresh_token = tokens.access_token, tokens.refresh_token
    config.token_expires_at = tokens.expires_at
    service.client.token = tokens.access_token
    try:
        save_config(config)
    except OSError:
        pass  # новый токен проработает до перезапуска, потом обновится снова


_WORK_LOCALLY = ("База сохранена на этом компьютере, работать можно — изменения "
                 "выложатся, когда Диск заработает.")


def is_disk_glitch(error: Exception) -> bool:
    """Сбой Диска, который проходит сам: недоступность либо испорченный ответ
    (так выглядят сбои в дата-центрах Яндекса). Тихие попытки считают его
    недоступностью: отметка, пауза автосинхронизации, без окон."""
    if isinstance(error, YaDiskError):
        return error.is_unavailable or error.is_bad_response
    return isinstance(error, RemoteDataError)


def describe_error(error: Exception) -> str:
    """Текст ошибки для оператора."""
    if isinstance(error, YaDiskError):
        if error.is_auth_error:
            return ("Яндекс.Диск не принял вход: доступ отозван или истёк. "
                    "Войдите в Яндекс заново в «Настройках».")
        if error.is_tls_error:
            return (
                "Не удалось проверить защищённое соединение с Яндекс.Диском. "
                "Проверьте дату и время на компьютере; если они верны — на компьютере "
                "устарели корневые сертификаты Windows."
            )
        if error.is_network_error:
            return f"Нет связи с Яндекс.Диском. Проверьте интернет и повторите.\n{VPN_HINT}"
        if error.status == 507:
            return "На Яндекс.Диске закончилось место."
        if error.is_unavailable:
            return (f"Яндекс.Диск сейчас не работает (сбой на стороне Яндекса, код {error.status}).\n"
                    + _WORK_LOCALLY)
        if error.is_bad_response:
            return "Яндекс.Диск прислал непонятный ответ (сбой на стороне Яндекса).\n" + _WORK_LOCALLY
        return f"Яндекс.Диск ответил ошибкой {error.status}. Повторите позже."
    if isinstance(error, RemoteDataError):
        return f"{error}\n{_WORK_LOCALLY}"
    if isinstance(error, SyncError):
        return str(error)
    return f"Не удалось выполнить синхронизацию:\n{error}"


def message_box(parent, icon, title: str, text: str) -> QMessageBox:
    """QMessageBox, который показывает текст как есть. По умолчанию Qt угадывает
    формат и отрисовал бы HTML из строк, пришедших с Диска или из базы."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.PlainText)
    box.setText(text)
    return box


def show_message(parent, icon, title: str, text: str) -> None:
    message_box(parent, icon, title, text).exec_()


def ask_yes_no(parent, title: str, text: str, default_yes: bool) -> bool:
    box = message_box(parent, QMessageBox.Question, title, text)
    yes = box.addButton("Да", QMessageBox.YesRole)
    no = box.addButton("Нет", QMessageBox.NoRole)
    box.setDefaultButton(yes if default_yes else no)
    box.setEscapeButton(no)
    box.exec_()
    return box.clickedButton() is yes


def format_size(size: float) -> str:
    """Размер для оператора: «850 КБ», «3,4 МБ»."""
    if size < 1024 * 1024:
        return f"{max(size, 0) / 1024:.0f} КБ"
    return f"{size / (1024 * 1024):.1f} МБ".replace(".", ",")


class _ProgressRelay(QObject):
    """Ход передачи из фонового потока в окно: сигнал доставляется в поток
    окна. Чаще _PROGRESS_INTERVAL не сообщаем — иначе очередь событий забьётся."""
    changed = Signal(object, object)

    def __init__(self):
        super().__init__()
        self._last = 0.0

    def report(self, done: int, total: int) -> None:
        now = time.monotonic()
        if now - self._last >= _PROGRESS_INTERVAL or (total and done >= total):
            self._last = now
            self.changed.emit(done, total)


class _BusyDialog(QDialog):
    def __init__(self, parent, text, token):
        super().__init__(parent)
        self._token = token
        self._finished = False
        self._samples = deque()     # (время, передано байт) за последние _SPEED_WINDOW с
        self.cancelled = False
        self.setWindowTitle("Синхронизация")
        self.setWindowModality(Qt.WindowModal)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint
                            & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self._label = QLabel(text)
        layout.addWidget(self._label)
        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        self._bar.setTextVisible(False)
        layout.addWidget(self._bar)
        self._transfer_label = QLabel("")
        self._transfer_label.setObjectName("muted")
        self._transfer_label.setVisible(False)
        layout.addWidget(self._transfer_label)
        self._slow_hint = QLabel(f"Связь с Яндекс.Диском медленная. {VPN_HINT}")
        self._slow_hint.setObjectName("muted")
        self._slow_hint.setWordWrap(True)
        self._slow_hint.setVisible(False)
        layout.addWidget(self._slow_hint)
        self._slow_timer = QTimer(self)
        self._slow_timer.setSingleShot(True)
        self._slow_timer.timeout.connect(self._show_slow_hint)
        self._slow_timer.start(_SLOW_HINT_MS)
        row = QHBoxLayout()
        row.addStretch(1)
        self._cancel_btn = QPushButton("Отмена")
        self._cancel_btn.clicked.connect(self.reject)
        row.addWidget(self._cancel_btn)
        layout.addLayout(row)

    def _speed(self) -> float:
        """Байт в секунду за последние _SPEED_WINDOW с; 0 — данных мало."""
        if len(self._samples) < 2:
            return 0.0
        (t0, d0), (t1, d1) = self._samples[0], self._samples[-1]
        return (d1 - d0) / (t1 - t0) if t1 > t0 else 0.0

    def on_progress(self, done, total):
        now = time.monotonic()
        self._samples.append((now, done))
        while len(self._samples) > 2 and now - self._samples[0][0] > _SPEED_WINDOW:
            self._samples.popleft()
        if total:
            self._bar.setRange(0, 1000)
            self._bar.setValue(min(1000, done * 1000 // total))
            text = f"{format_size(done)} из {format_size(total)}"
        else:
            text = format_size(done)
        speed = self._speed()
        if speed > 0:
            text += f" · {format_size(speed)}/с"
        self._transfer_label.setText(text)
        self._transfer_label.setVisible(True)

    def _show_slow_hint(self):
        if self._finished:
            return
        if self._samples and self._speed() >= _SLOW_SPEED:
            # Передача идёт с нормальной скоростью — проверим ещё раз позже.
            self._slow_timer.start(_SLOW_HINT_MS)
            return
        self._slow_hint.setVisible(True)
        self.adjustSize()

    def finish(self):
        self._finished = True
        self._slow_timer.stop()
        self.accept()

    def reject(self):
        # Esc и кнопка «Отмена». Если необратимый шаг уже идёт, дожидаемся конца.
        if self._finished:
            super().reject()
        elif self._token.cancel():
            self.cancelled = True
            super().reject()
        else:
            self._cancel_btn.setEnabled(False)
            self._label.setText("Завершается…")


class _Worker(QThread):
    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self._fn()
        except Exception as e:  # передаём в главный поток, там покажут
            self.error = e

    def abandon(self):
        """Поток не прервать, пока он ждёт сеть: держим ссылку до его завершения.
        Слот — метод самого потока, поэтому сигнал приходит в главный поток."""
        _abandoned.add(self)
        self.finished.connect(self._forget)
        if self.isFinished():
            self._forget()

    def _forget(self):
        _abandoned.discard(self)
        self.deleteLater()


def stop_pending_work() -> None:
    """Перед выходом из программы: отменённые потоки ждут только ответа сети и
    локальную базу уже не тронут, их можно прервать."""
    for worker in list(_abandoned):
        if not worker.wait(1500):
            worker.terminate()
            worker.wait()
        _abandoned.discard(worker)


def run_with_progress(parent, text, fn, transfer=False):
    """Выполнить fn(token) в фоновом потоке под модальным индикатором с кнопкой
    «Отмена». Возвращает результат fn, поднимает его исключение либо
    SyncCancelled, если пользователь отменил.

    transfer — fn передаёт файл: вызывается как fn(token, progress), и окно
    показывает объём, долю и скорость передачи."""
    if _abandoned:
        raise SyncError("Предыдущая операция ещё завершается. Повторите через минуту.")
    token = CancelToken()
    dialog = _BusyDialog(parent, text, token)
    if transfer:
        relay = _ProgressRelay()
        relay.changed.connect(dialog.on_progress)
        worker = _Worker(lambda: fn(token, relay.report))
    else:
        worker = _Worker(lambda: fn(token))
    worker.finished.connect(dialog.finish)
    worker.start()
    dialog.exec_()
    cancelled = dialog.cancelled
    dialog.deleteLater()
    if cancelled:
        worker.abandon()
        raise SyncCancelled()
    worker.wait()
    error, result = worker.error, worker.result
    worker.deleteLater()
    if error is not None:
        raise error
    return result


def _format_time(value: str) -> str:
    """'2026-09-30 14:05:00' → '30.09.2026 14:05'; пустое или чужой формат — '—'."""
    try:
        return datetime.strptime(value, SAVED_AT_FORMAT).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return "—"


def _describe_remote(meta) -> str:
    return f"устройство «{meta.device or '—'}», сохранена {_format_time(meta.saved_at)}"


def _ask_conflict_never_synced(parent, check) -> str:
    """Конфликт на устройстве, ни разу не загружавшем базу с Диска: его база
    не продолжает базу на Диске, и заменить ею Диск — стереть данные."""
    box = message_box(
        parent, QMessageBox.Warning, "На Диске уже есть база",
        "На Яндекс.Диске уже есть база, а этот компьютер ещё ни разу её не загружал.\n"
        f"Версия на Диске: {_describe_remote(check.meta)}.\n\n"
        "Заменить базу на Диске базой этого компьютера нельзя — так можно по ошибке "
        "стереть все данные.\n\n"
        "«Взять с Диска» — база этого компьютера заменится; прежняя сохранится в "
        "папке backups/presync.",
    )
    keep_remote = box.addButton("Взять с Диска", QMessageBox.DestructiveRole)
    cancel = box.addButton("Отмена", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec_()
    return "remote" if box.clickedButton() is keep_remote else ""


def _ask_conflict(parent, check) -> str:
    """Какую версию оставить: 'local', 'remote' или '' (отмена)."""
    if check.never_synced:
        return _ask_conflict_never_synced(parent, check)
    older =("\nВнимание: версия на Диске не продолжает ту, с которой этот компьютер "
             "работал (номер меньше либо версию заменили другой базой).\n"
             if check.remote_is_older else "")
    box = message_box(
        parent, QMessageBox.Warning, "Изменения с двух сторон",
        "База изменена и на этом компьютере, и на Яндекс.Диске.\n\n"
        "Версия этого компьютера: последняя синхронизация "
        f"{_format_time(check.local_synced_at)}, после неё есть изменения.\n"
        f"Версия на Диске: {_describe_remote(check.meta)}.\n{older}\n"
        "Объединить их программа не может. Какую версию оставить?\n\n"
        "«Оставить эту» — база этого компьютера станет текущей на Диске; версия с "
        "Диска останется там среди пяти последних.\n"
        "«Взять с Диска» — база этого компьютера заменится; прежняя сохранится в "
        "папке backups/presync.",
    )
    keep_local = box.addButton("Оставить эту", QMessageBox.AcceptRole)
    keep_remote = box.addButton("Взять с Диска", QMessageBox.DestructiveRole)
    cancel = box.addButton("Отмена", QMessageBox.RejectRole)
    # Оба выбора необратимо вытесняют одну из версий — по Enter ничего не делаем.
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec_()
    if box.clickedButton() is keep_local:
        return "local"
    if box.clickedButton() is keep_remote:
        return "remote"
    return ""


def _ask_pull(parent, check) -> bool:
    if check.remote_is_older:
        return ask_yes_no(
            parent, "На Диске более старая база",
            "На Яндекс.Диске лежит база, которая не продолжает ту, с которой этот "
            f"компьютер работал ({_describe_remote(check.meta)}): номер версии меньше "
            "либо та же версия заменена другой базой.\n"
            "Так бывает, если папку на Диске восстановили из старой копии или два "
            "устройства выложили базу одновременно.\n\n"
            "Заменить базу этого компьютера ею?",
            default_yes=False,
        )
    return ask_yes_no(
        parent, "На Диске более новая база",
        f"На Яндекс.Диске есть более новая база ({_describe_remote(check.meta)}).\n"
        "Загрузить её?",
        default_yes=True,
    )


def _ask_push_to_empty_disk(parent) -> bool:
    return ask_yes_no(
        parent, "На Диске нет базы",
        "Этот компьютер уже синхронизировался с Яндекс.Диском, но сейчас базы там нет: "
        "папку удалили или очистили.\n\n"
        "Выложить базу этого компьютера заново?",
        default_yes=False,
    )


def internet_reachable(timeout: float = 3.0) -> bool:
    """Отвечает ли интернет вообще (адреса, не связанные с Яндексом)."""
    for host, port in _PROBE_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _probe_internet(main_window) -> Optional[bool]:
    """True/False — результат проверки; None — проверить не удалось или отменили."""
    try:
        return run_with_progress(main_window, "Проверка связи…",
                                 lambda token: internet_reachable())
    except (SyncCancelled, SyncError):
        return None


def _ask_retry_offline(parent, has_local_changes: bool,
                       internet_ok: Optional[bool] = None) -> bool:
    if has_local_changes:
        head = "Изменения сохранены на этом компьютере, но на Яндекс.Диск не выложены."
    else:
        head = "Не удалось связаться с Яндекс.Диском."
    if internet_ok is True:
        reason = "Интернет на компьютере есть, а Яндекс.Диск недоступен."
    elif internet_ok is False:
        reason = "Нет связи с интернетом. Проверьте подключение."
    else:
        reason = "Нет связи с Яндекс.Диском."
    box = message_box(
        parent, QMessageBox.Warning, "Нет связи с Яндекс.Диском",
        f"{head}\n\n{reason}\n{VPN_HINT}\n\nНажмите «Повторить», когда связь появится.")
    retry = box.addButton("Повторить", QMessageBox.AcceptRole)
    later = box.addButton("Позже", QMessageBox.RejectRole)
    box.setDefaultButton(retry)
    box.setEscapeButton(later)
    box.exec_()
    return box.clickedButton() is retry


def synchronize(main_window, config: SyncConfig, confirm_pull: bool = False) -> SyncResult:
    """Один проход синхронизации: проверить Диск и выложить либо загрузить базу.

    confirm_pull — спрашивать перед загрузкой с Диска (для автоматических
    запусков; при ручном нажатии кнопки пользователь уже согласился).
    Поднимает YaDiskError, SyncError, SyncCancelled.
    """
    service = make_sync_service(config, main_window.db_manager.db_path)
    main_window.release_db()

    def check_disk(token):
        refresh_token_if_needed(config, service)
        return service.check()

    check = run_with_progress(main_window, "Проверка Яндекс.Диска…", check_disk)
    status = check.status

    if status == SyncStatus.UP_TO_DATE:
        return SyncResult(SyncOutcome.UNCHANGED, "База совпадает с Яндекс.Диском.")

    if status == SyncStatus.CONFLICT:
        choice = _ask_conflict(main_window, check)
        if not choice:
            return SyncResult(SyncOutcome.DECLINED, "Версии различаются, выбор отложен.")
        status = SyncStatus.LOCAL_AHEAD if choice == "local" else SyncStatus.REMOTE_AHEAD
    elif status == SyncStatus.REMOTE_MISSING:
        if not _ask_push_to_empty_disk(main_window):
            return SyncResult(SyncOutcome.DECLINED, "На Диске нет базы, выгрузка отложена.")
        status = SyncStatus.LOCAL_AHEAD
    elif status == SyncStatus.REMOTE_AHEAD and (confirm_pull or check.remote_is_older):
        if not _ask_pull(main_window, check):
            return SyncResult(SyncOutcome.DECLINED, "Загрузка с Диска отложена.")

    if status == SyncStatus.LOCAL_AHEAD:
        rev = run_with_progress(
            main_window, "Выгрузка базы на Яндекс.Диск…",
            lambda token, progress: service.push(check.meta, token, progress), transfer=True,
        )
        return SyncResult(SyncOutcome.PUSHED,
                          f"Изменения выложены на Яндекс.Диск (ревизия {rev}).")

    rev = run_with_progress(
        main_window, "Загрузка базы с Яндекс.Диска…",
        lambda token, progress: service.pull(check.meta, token, progress), transfer=True,
    )
    try:
        main_window.reload_db()
    except Exception as e:
        # Загруженная база не открылась — возвращаем прежнюю, программа работает дальше.
        main_window.release_db()
        try:
            service.rollback_pull()
        except Exception as rollback_error:
            raise SyncError(
                "База с Диска не открылась, и прежнюю базу вернуть не удалось. "
                "Её копия лежит в папке backups\\presync рядом с базой."
            ) from rollback_error
        main_window.reload_db()
        raise SyncError(
            "База с Диска не открылась в программе, возвращена прежняя база этого компьютера."
        ) from e
    return SyncResult(SyncOutcome.PULLED, f"База загружена с Яндекс.Диска (ревизия {rev}).")


def sync_interactive(main_window, config: SyncConfig, announce: bool = False,
                     confirm_pull: bool = False, quiet: bool = False) -> SyncAttempt:
    """Синхронизация со всеми диалогами.

    Без интернета предлагает включить его и повторить; после такого повтора об
    успехе сообщается всегда. При сбое на стороне Яндекса объясняет, что работать
    можно. announce — сообщать об успехе и без сбоев (ручной запуск). quiet —
    недоступность Диска без окон (автоматические попытки): база остаётся
    локальной, изменения выложит следующая удачная синхронизация.
    """
    was_offline = False
    while True:
        try:
            result = synchronize(main_window, config, confirm_pull=confirm_pull)
        except SyncCancelled:
            return SyncAttempt.NOT_SYNCED
        except (YaDiskError, RemoteDataError) as e:
            if is_disk_glitch(e) and quiet:
                logger.warning("Quiet sync: Disk is unavailable: %s", e)
                return SyncAttempt.UNAVAILABLE
            if isinstance(e, YaDiskError) and e.is_network_error:
                if _ask_retry_offline(main_window, has_local_changes(main_window, config) is True,
                                      _probe_internet(main_window)):
                    was_offline = True
                    continue
                return SyncAttempt.UNAVAILABLE
            if is_disk_glitch(e):
                show_message(main_window, QMessageBox.Warning, "Яндекс.Диск недоступен",
                             describe_error(e))
                return SyncAttempt.UNAVAILABLE
            show_message(main_window, QMessageBox.Critical, "Ошибка синхронизации",
                         describe_error(e))
            return SyncAttempt.NOT_SYNCED
        except Exception as e:
            show_message(main_window, QMessageBox.Critical, "Ошибка синхронизации",
                         describe_error(e))
            return SyncAttempt.NOT_SYNCED
        if result.in_sync and (announce or was_offline):
            show_message(main_window, QMessageBox.Information, "Синхронизация", result.text)
        return SyncAttempt.IN_SYNC if result.in_sync else SyncAttempt.NOT_SYNCED


def has_local_changes(main_window, config: SyncConfig) -> Optional[bool]:
    """Есть ли невыложенные правки. Без сети. None — проверить не удалось
    (повреждённый файл, незавершённая запись): это не то же самое, что «нет»."""
    try:
        return make_sync_service(config, main_window.db_manager.db_path).local_changed()
    except Exception:
        return None


def last_sync_time(main_window, config: SyncConfig) -> str:
    """Время последнего обмена с Диском для оператора: «30.09.2026 14:05»;
    пусто — обмена не было или узнать не удалось. Без сети."""
    try:
        value = make_sync_service(config, main_window.db_manager.db_path).last_synced_at()
    except Exception:
        return ""
    return _format_time(value) if value else ""


class BackgroundCheck(QThread):
    """Проверка Диска без блокировки окна (при запуске программы). Только читает."""

    def __init__(self, config: SyncConfig, db_path: str):
        super().__init__()
        self._service = make_sync_service(config, db_path)
        self.check = None
        self.error = None

    def run(self):
        try:
            self.check = self._service.check()
        except Exception as e:
            self.error = e
