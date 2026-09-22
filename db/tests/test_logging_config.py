import json
import logging

from db import logging_config as module


def test_configures_root_with_a_single_json_handler():
    module.configure_json_logging()
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, module.JsonFormatter)


def test_uvicorn_loggers_are_reconfigured_explicitly_not_only_root():
    module.configure_json_logging()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        assert logger.propagate is False
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, module.JsonFormatter)


def test_formatter_produces_valid_json_with_the_expected_keys():
    record = logging.makeLogRecord({"msg": "hola mundo", "levelname": "INFO", "name": "core"})
    payload = json.loads(module.JsonFormatter().format(record))
    assert payload["message"] == "hola mundo"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "core"
    assert "timestamp" in payload


def test_formatter_includes_exc_info_when_present():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.makeLogRecord({"msg": "fallo", "exc_info": __import__("sys").exc_info()})
    payload = json.loads(module.JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exc_info"]


def test_log_level_env_var_is_honored(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    module.configure_json_logging()
    assert logging.getLogger().level == logging.DEBUG
