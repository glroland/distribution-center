import uvicorn

from .logging_config import configure_logging
from .settings import settings


def main() -> None:
    configure_logging(settings.LOG_LEVEL)
    uvicorn.run("src.app:app", host=settings.HOST, port=settings.PORT)


if __name__ == "__main__":
    main()
