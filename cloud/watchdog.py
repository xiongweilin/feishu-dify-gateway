#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import smtplib
import ssl
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from pathlib import Path


@dataclass(slots=True)
class WatchdogState:
    failures: int = 0
    first_failure_at: int = 0
    notified: bool = False
    last_notice_at: int = 0


def env_flag(name: str, default: bool) -> bool:
    """Read a boolean environment flag without exposing configuration values."""
    value: str | None = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def read_secret(directory: Path, name: str) -> str:
    path = directory / name
    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError(f"Watchdog secret file is missing or unsafe: {name}")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"Watchdog secret is empty: {name}")
    return value


def load_state(path: Path) -> WatchdogState:
    try:
        return WatchdogState(**json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, TypeError, ValueError, json.JSONDecodeError):
        return WatchdogState()


def save_state(path: Path, state: WatchdogState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
        json.dump(asdict(state), file, separators=(",", ":"))
        file.flush()
        os.fsync(file.fileno())
        temp_path = Path(file.name)
    temp_path.chmod(0o600)
    os.replace(temp_path, path)


def transition(state: WatchdogState, ready: bool, now: int) -> str:
    if ready:
        action = "recovery" if state.notified else "none"
        state.failures = 0
        state.first_failure_at = 0
        state.notified = False
        return action
    state.failures += 1
    if not state.first_failure_at:
        state.first_failure_at = now
    old_enough = now - state.first_failure_at >= 300
    threshold_reached = state.failures >= 3 or old_enough
    reminder_due = not state.notified or now - state.last_notice_at >= 21_600
    if threshold_reached and reminder_due:
        return "failure"
    return "none"


def gateway_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return int(response.status) == 200
    except (OSError, urllib.error.URLError):
        return False


def send_mail(secret_dir: Path, subject: str, body: str) -> None:
    username = read_secret(secret_dir, "smtp_username")
    password = read_secret(secret_dir, "smtp_app_password")
    recipient = read_secret(secret_dir, "smtp_recipient")
    host = os.getenv("WATCHDOG_SMTP_HOST", "smtp.example.internal")
    port = int(os.getenv("WATCHDOG_SMTP_PORT", "587"))
    message = EmailMessage()
    message["From"] = username
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=15) as client:
        client.starttls(context=context)
        client.login(username, password)
        client.send_message(message)


def main() -> None:
    state_path = Path(
        os.getenv("WATCHDOG_STATE_FILE", "/var/lib/feishu-gateway-watchdog/state.json")
    )
    secret_dir = Path(os.getenv("WATCHDOG_SECRETS_DIR", "/etc/feishu-gateway-watchdog"))
    ready_url = os.getenv("WATCHDOG_READY_URL", "http://gateway.example.internal:8082/readyz")
    email_enabled = env_flag("WATCHDOG_EMAIL_ENABLED", True)
    now = int(time.time())
    state = load_state(state_path)
    action = transition(state, gateway_ready(ready_url), now)
    if not email_enabled:
        # Keep readiness tracking active while suppressing outbound SMTP.  Reset
        # notification markers so re-enabling the flag can notify immediately
        # if the gateway is still failing.
        state.notified = False
        state.last_notice_at = 0
    elif action == "failure":
        send_mail(
            secret_dir,
            "Feishu gateway unavailable",
            "The Feishu gateway has failed readiness checks for at least five minutes.",
        )
        state.notified = True
        state.last_notice_at = now
    elif action == "recovery":
        if now - state.last_notice_at >= 21_600:
            send_mail(
                secret_dir,
                "Feishu gateway recovered",
                "The Feishu gateway is ready again.",
            )
            state.last_notice_at = now
    save_state(state_path, state)


if __name__ == "__main__":
    main()
