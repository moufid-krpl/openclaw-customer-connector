from __future__ import annotations

from app.config import load_settings
from app.connector import ConnectorService
from app.logging_setup import setup_logging


def main() -> None:
    settings = load_settings()
    setup_logging(settings)
    service = ConnectorService(settings)
    service.run_forever()


if __name__ == "__main__":
    main()
