from __future__ import annotations

import json
import logging

from feishu_dify_gateway.json_logging import JsonFormatter


def test_formatter_ignores_sensitive_extra_fields() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "safe", (), None)
    record.event = "test_event"
    record.message_body = "private message"
    record.open_id = "private user"
    rendered = JsonFormatter().format(record)
    parsed = json.loads(rendered)
    assert parsed["event"] == "test_event"
    assert "private message" not in rendered
    assert "private user" not in rendered
