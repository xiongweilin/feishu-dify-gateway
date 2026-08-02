from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from feishu_dify_gateway.capture_open_id import atomic_write_secret


def test_atomic_write_secret_does_not_echo_or_leave_temp_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    atomic_write_secret(tmp_path, "identity", "sensitive-open-id")
    target = tmp_path / "identity"
    assert target.read_text(encoding="utf-8") == "sensitive-open-id"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [path.name for path in tmp_path.iterdir()] == ["identity"]
    captured = capsys.readouterr()
    assert "sensitive-open-id" not in captured.out
    assert "sensitive-open-id" not in captured.err
