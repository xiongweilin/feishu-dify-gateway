from __future__ import annotations

import uvicorn

from .app import create_app
from .config import Settings
from .json_logging import configure_logging


def main() -> None:
    configure_logging()
    settings = Settings.from_environment()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
