from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(RuntimeError):
    """Raised when runtime configuration is missing or unsafe."""


def _read_secret(directory: Path, name: str) -> str:
    path = directory / name
    if not path.is_file() or path.is_symlink():
        raise ConfigurationError(f"Required secret file is missing or unsafe: {name}")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ConfigurationError(f"Secret file permissions must be 600: {name}")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ConfigurationError(f"Required secret file is empty: {name}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid boolean environment variable: {name}")


@dataclass(frozen=True, slots=True)
class Settings:
    feishu_app_id: str
    feishu_app_secret: str
    feishu_allowed_open_id: str
    feishu_alert_recipient_open_id: str
    dify_api_key: str
    user_hmac_key: str
    notification_hmac_key: str
    state_db: Path
    dify_base_url: str = "http://docker-api-1:5001/v1"
    dify_health_url: str = "http://docker-api-1:5001/health"
    prometheus_base_url: str = "http://prometheus:9090"
    feishu_base_url: str = "https://open.feishu.cn"
    host: str = "0.0.0.0"
    port: int = 8082
    public_port: int = 8083
    ws_enabled: bool = True
    notification_ttl_seconds: int = 300
    event_retention_seconds: int = 604_800

    @classmethod
    def from_environment(cls) -> Settings:
        secrets_dir = Path(os.getenv("GATEWAY_SECRETS_DIR", "/run/secrets"))
        user_open_id = _read_secret(secrets_dir, "feishu_user_open_id")
        return cls(
            feishu_app_id=_read_secret(secrets_dir, "feishu_app_id"),
            feishu_app_secret=_read_secret(secrets_dir, "feishu_app_secret"),
            feishu_allowed_open_id=user_open_id,
            feishu_alert_recipient_open_id=user_open_id,
            dify_api_key=_read_secret(secrets_dir, "dify_api_key"),
            user_hmac_key=_read_secret(secrets_dir, "user_hmac_key"),
            notification_hmac_key=_read_secret(secrets_dir, "notification_hmac_key"),
            state_db=Path(os.getenv("GATEWAY_STATE_DB", "/var/lib/feishu-gateway/state.db")),
            dify_base_url=os.getenv("DIFY_BASE_URL", "http://docker-api-1:5001/v1"),
            dify_health_url=os.getenv("DIFY_HEALTH_URL", "http://docker-api-1:5001/health"),
            prometheus_base_url=os.getenv("PROMETHEUS_BASE_URL", "http://prometheus:9090"),
            feishu_base_url=os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn"),
            host=os.getenv("GATEWAY_HOST", "0.0.0.0"),
            port=int(os.getenv("GATEWAY_PORT", "8082")),
            public_port=int(os.getenv("GATEWAY_PUBLIC_PORT", "8083")),
            ws_enabled=_env_bool("FEISHU_WS_ENABLED", True),
        )
