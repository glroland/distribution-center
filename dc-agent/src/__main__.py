import uvicorn

from . import settings


def main() -> None:
    uvicorn.run("src.app:app", host=settings.HOST, port=settings.PORT)


if __name__ == "__main__":
    main()
