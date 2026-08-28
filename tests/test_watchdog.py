from __future__ import annotations

import json

from cloud.watchdog import WatchdogState, env_flag, main, transition


def test_env_flag_defaults_and_parses_common_values(monkeypatch) -> None:
    monkeypatch.delenv("WATCHDOG_EMAIL_ENABLED", raising=False)
    assert env_flag("WATCHDOG_EMAIL_ENABLED", True) is True
    monkeypatch.setenv("WATCHDOG_EMAIL_ENABLED", "false")
    assert env_flag("WATCHDOG_EMAIL_ENABLED", True) is False
    monkeypatch.setenv("WATCHDOG_EMAIL_ENABLED", "ON")
    assert env_flag("WATCHDOG_EMAIL_ENABLED", False) is True


def test_main_skips_smtp_when_email_is_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WATCHDOG_EMAIL_ENABLED", "false")
    monkeypatch.setenv("WATCHDOG_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr("cloud.watchdog.gateway_ready", lambda _url: False)

    def fail_send_mail(*_args, **_kwargs) -> None:
        raise AssertionError("SMTP must be skipped")

    monkeypatch.setattr(
        "cloud.watchdog.send_mail",
        fail_send_mail,
    )
    times = iter((1000, 1100, 1200))
    monkeypatch.setattr("cloud.watchdog.time.time", lambda: next(times))

    for _ in range(3):
        main()

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["failures"] == 3
    assert state["notified"] is False


def test_watchdog_triggers_after_three_consecutive_failures() -> None:
    state = WatchdogState()
    assert transition(state, False, 1000) == "none"
    assert transition(state, False, 1100) == "none"
    assert transition(state, False, 1200) == "failure"


def test_watchdog_triggers_after_five_minutes_even_with_fewer_checks() -> None:
    state = WatchdogState()
    assert transition(state, False, 1000) == "none"
    assert transition(state, False, 1300) == "failure"


def test_watchdog_sends_recovery_once() -> None:
    state = WatchdogState(failures=4, first_failure_at=1000, notified=True, last_notice_at=1300)
    assert transition(state, True, 1400) == "recovery"
    assert transition(state, True, 1500) == "none"
