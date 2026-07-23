"""Точка входа для Vercel (serverless).

Vercel ожидает ASGI-приложение в переменной ``app``. Добавляем каталог
``backend`` в путь импорта и отдаём то же приложение FastAPI, что и локально.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.main import app  # noqa: E402,F401
