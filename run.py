"""Запуск сервера: python run.py [--host 0.0.0.0] [--port 8000]."""
import argparse

import uvicorn

from app.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="АС «Планирование загрузки и расчёт стоимости»")
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 — доступ из сети участка")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
