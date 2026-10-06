"""#161: el envío de la respuesta al lead se reintenta solo cuando el mensaje seguro no salió."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from app.channels import telegram
from app.channels.telegram import MAX_RETRY_AFTER_SECONDS, SEND_RETRY_DELAYS, send_with_retry


@pytest.fixture
def waits(monkeypatch):
    """Las esperas pedidas, sin dormir de verdad."""
    seen: list[float] = []

    async def fake_sleep(seconds):
        seen.append(seconds)

    monkeypatch.setattr(telegram.asyncio, "sleep", fake_sleep)
    return seen


class FlakySend:
    """Falla con los errores dados, en orden, y después envía."""

    def __init__(self, *errors):
        self.errors = list(errors)
        self.calls = 0
        self.sent = 0

    async def __call__(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        self.sent += 1


def run(send):
    asyncio.run(send_with_retry(send))


# --- se reintenta ----------------------------------------------------------------------------------------

def test_a_connection_failure_is_retried_and_the_message_goes_out(waits):
    send = FlakySend(NetworkError("httpx.ConnectError"))
    run(send)
    assert (send.calls, send.sent) == (2, 1)
    assert waits == [SEND_RETRY_DELAYS[0]]


def test_three_attempts_with_the_short_backoff(waits):
    send = FlakySend(NetworkError("caída"), NetworkError("caída"))
    run(send)
    assert (send.calls, send.sent) == (3, 1)
    assert waits == list(SEND_RETRY_DELAYS) == [1.0, 2.0]


def test_after_the_last_attempt_the_error_propagates(waits):
    send = FlakySend(*[NetworkError("caída")] * 3)
    with pytest.raises(NetworkError):
        run(send)
    assert (send.calls, send.sent) == (3, 0)
    assert waits == [1.0, 2.0]


@pytest.mark.parametrize("retry_after", [5, timedelta(seconds=5)])
def test_retry_after_waits_what_telegram_asks(waits, retry_after):
    error = RetryAfter(5)
    error.__dict__["retry_after"] = retry_after  # las versiones nuevas lo dan como timedelta
    send = FlakySend(error)
    run(send)
    assert (send.calls, send.sent) == (2, 1)
    assert waits == [5.0]


def test_retry_after_longer_than_the_cap_is_not_retried(waits):
    send = FlakySend(RetryAfter(int(MAX_RETRY_AFTER_SECONDS) + 1))
    with pytest.raises(RetryAfter):
        run(send)
    assert send.calls == 1 and waits == []


# --- no se reintenta ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("error", [
    TimedOut(),                      # el mensaje pudo llegar: reintentar lo duplicaría
    BadRequest("Message is too long"),  # subclase de NetworkError, pero no cambia al reintentar
    Forbidden("bot was blocked by the user"),
])
def test_errors_where_retrying_duplicates_or_changes_nothing_are_not_retried(waits, error):
    send = FlakySend(error)
    with pytest.raises(type(error)):
        run(send)
    assert send.calls == 1 and waits == []


def test_timed_out_is_a_network_error_so_it_must_be_excluded_explicitly():
    assert issubclass(TimedOut, NetworkError) and issubclass(BadRequest, NetworkError)


def test_an_unknown_error_is_not_retried(waits):
    send = FlakySend(ValueError("otra cosa"))
    with pytest.raises(ValueError):
        run(send)
    assert send.calls == 1 and waits == []


# --- en el manejador real -------------------------------------------------------------------------------

class FlakyMessage:
    def __init__(self, *errors):
        self.text = "hi"
        self.date = None
        self.errors = list(errors)
        self.attempts = 0
        self.replies = []

    async def reply_text(self, value, parse_mode=None):
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)
        self.replies.append(value)


def dispatch(engine, message, user_id):
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=user_id))
    application = SimpleNamespace(bot_data={"engine": engine, "catalog": StubCatalog(), "agent": None})
    asyncio.run(telegram._on_message(update, SimpleNamespace(application=application)))


class StubCatalog:
    async def call(self, tool, arguments=None):
        return {"properties": []}


def test_the_stop_confirmation_arrives_after_a_transient_connection_error(engine, waits):
    """El caso de OBS-6: la baja se guardaba, pero el lead no recibía la confirmación."""
    message = FlakyMessage(NetworkError("httpx.ConnectError"))
    message.text = "STOP"
    dispatch(engine, message, user_id=930)
    assert message.replies == [telegram.consent.OPT_OUT_CONFIRMATION] and message.attempts == 2


def test_the_turn_is_not_repeated_when_the_send_is_retried(engine, waits, monkeypatch):
    turns = []
    real = telegram.process_turn_reply

    async def spy(*args, **kwargs):
        turns.append(1)
        return await real(*args, **kwargs)

    monkeypatch.setattr(telegram, "process_turn_reply", spy)
    message = FlakyMessage(NetworkError("caída"), NetworkError("caída"))
    dispatch(engine, message, user_id=931)
    assert len(turns) == 1 and len(message.replies) == 1 and message.attempts == 3


def test_when_the_attempts_run_out_it_is_logged_and_the_bot_keeps_running(engine, waits, caplog):
    message = FlakyMessage(*[NetworkError("caída")] * 3)
    dispatch(engine, message, user_id=932)  # no lanza
    assert message.replies == [] and message.attempts == 3
    assert "no se pudo enviar la respuesta por Telegram" in caplog.text


def test_a_timeout_sending_is_not_retried_in_the_handler(engine, waits):
    message = FlakyMessage(TimedOut())
    dispatch(engine, message, user_id=933)
    assert message.attempts == 1 and waits == []
