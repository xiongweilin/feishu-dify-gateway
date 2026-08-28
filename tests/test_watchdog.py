from __future__ import annotations

from cloud.watchdog import WatchdogState, env_flag, transition


def test_env_flag_defaults_and_parses_common_values(monkeypatch) -> None:
    monkeypatch.delenv("WATCHDOG_EMAIL_ENABLED", raising=False)
    assert env_flag("WATCHDOG_EMAIL_ENABLED", True) is True
    monkeypatch.setenv("WATCHDOG_EMAIL_ENABLED", "false")
    assert env_flag("WATCHDOG_EMAIL_ENABLED", True) is False
    monkeypatch.setenv("WATCHDOG_EMAIL_ENABLED", "ON")
    assert env_flag("WATCHDOG_EMAIL_ENABLED", False) is True


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
