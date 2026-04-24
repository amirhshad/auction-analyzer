"""FastAPI dependencies: DB session-per-request, task manager, and templates."""
from pathlib import Path

from fastapi.templating import Jinja2Templates

from execution.db_repository import Repository
from execution.webapp.task_manager import TaskManager

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_task_manager = TaskManager()


def get_repo():
    """Yield a Repository instance, closing it after the request."""
    repo = Repository()
    try:
        yield repo
    finally:
        repo.close()


def get_task_manager() -> TaskManager:
    return _task_manager
