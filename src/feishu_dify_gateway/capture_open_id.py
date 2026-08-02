from __future__ import annotations

import os
import tempfile
from pathlib import Path

import lark_oapi as lark  # type: ignore[import-untyped]
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1  # type: ignore[import-untyped]

from .config import ConfigurationError, _read_secret


def atomic_write_secret(directory: Path, name: str, value: str) -> None:
    if not value or not directory.is_dir() or directory.is_symlink():
        raise ConfigurationError("Secret directory or value is unsafe")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, directory / name)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    secrets_dir = Path(os.getenv("GATEWAY_SECRETS_DIR", "/run/secrets"))
    app_id = _read_secret(secrets_dir, "feishu_app_id")
    app_secret = _read_secret(secrets_dir, "feishu_app_secret")

    def on_message(data: P2ImMessageReceiveV1) -> None:
        event = data.event
        if event is None or event.message is None or event.sender is None:
            return
        if event.message.chat_type != "p2p":
            return
        sender_id = event.sender.sender_id
        if sender_id is None or not sender_id.open_id:
            return
        atomic_write_secret(secrets_dir, "feishu_user_open_id", sender_id.open_id)
        print("Feishu user identity captured without displaying its value.", flush=True)
        os._exit(0)

    dispatcher = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=dispatcher,
        log_level=lark.LogLevel.WARNING,
    )
    print("Send one private text message to the bot within five minutes.", flush=True)
    client.start()


if __name__ == "__main__":
    main()
