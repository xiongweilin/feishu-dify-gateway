from __future__ import annotations

from cloud.watchdog import WatchdogState, transition


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
