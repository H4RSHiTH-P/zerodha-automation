import json
import logging

from zt.core import log


def test_secrets_are_redacted_and_lines_are_json(tmp_path):
    secret = "sEcReT" * 6
    token = "a1B2" * 9  # 36-char alphanumeric run after the word token
    logger = log.setup("test", tmp_path, secrets=(secret,))
    assert len(logger.handlers) == 2
    log.event("kite.login", access_token=token, note=f"secret {secret} inside")
    log.event("plain", level=logging.WARNING, symbol="DIXON")
    for h in logger.handlers:
        h.flush()
    lines = [json.loads(line) for line in (tmp_path / "test.jsonl").read_text().splitlines()]
    assert lines[0]["event"] == "kite.login" and lines[0]["service"] == "test"
    assert secret not in json.dumps(lines) and token not in json.dumps(lines)
    assert lines[1]["level"] == "WARNING" and lines[1]["symbol"] == "DIXON"


def test_setup_twice_keeps_one_handler_pair(tmp_path):
    log.setup("a", tmp_path)
    logger = log.setup("a", tmp_path)
    assert len(logger.handlers) == 2
