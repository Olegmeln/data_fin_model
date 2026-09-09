"""PostToolUse-хук: напоминание о миграции при правке ORM-моделей.

Читает JSON события со stdin, смотрит на путь изменённого файла. Если тронуты
ORM-модели или конфигурация Alembic, печатает напоминание — вывод хука попадает
в контекст Claude, и сигнал конца блока получится честным.

Хук ничего не запускает и ничего не блокирует: он не знает, где venv у
пользователя, и не должен гадать. Всегда завершается кодом 0.
"""
import json
import sys

WATCHED = (
    "backend/app/models.py",
    "backend/app/db.py",
    "backend/alembic/env.py",
)

REMINDER = (
    "[dfm-dev] Затронута схема БД ({path}).\n"
    "Прежде чем закрывать блок:\n"
    "  1. alembic revision --autogenerate -m \"...\"  — если изменился состав полей\n"
    "  2. DATABASE_URL=\"sqlite:////tmp/test.db\" alembic upgrade head && alembic check\n"
    "  3. закрыть блок сигналом \"Требуется миграция\", а не \"Ничего не требуется\""
)


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    path = ""
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        path = str(tool_input.get("file_path") or "")

    if not path:
        return 0

    normalized = path.replace("\\", "/")
    for watched in WATCHED:
        if normalized.endswith(watched):
            print(REMINDER.format(path=watched))
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
