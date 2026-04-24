"""Background task runner with SSE progress streaming."""
import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class TaskState:
    task_id: str
    status: str = "pending"  # pending | running | done | error
    progress: int = 0
    message: str = ""
    result: Any = None
    error: Optional[str] = None


class TaskManager:
    def __init__(self, max_workers: int = 2):
        self._tasks: dict[str, TaskState] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def create_task_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def submit(self, task_id: str, fn: Callable, *args, **kwargs) -> TaskState:
        state = TaskState(task_id=task_id)
        self._tasks[task_id] = state

        def _wrapper():
            state.status = "running"
            try:
                result = fn(*args, **kwargs)
                state.result = result
                state.status = "done"
                state.progress = 100
                state.message = "Done!"
            except Exception as e:
                state.error = str(e)
                state.status = "error"
                state.message = f"Error: {e}"

        self._executor.submit(_wrapper)
        return state

    def make_progress_callback(self, task_id: str) -> Callable:
        state = self._tasks[task_id]

        def callback(current, total, message):
            if total > 0:
                state.progress = min(int((current / total) * 90) + 10, 99)
            else:
                state.progress = 10
            state.message = message

        return callback

    def get_state(self, task_id: str) -> Optional[TaskState]:
        return self._tasks.get(task_id)

    async def stream(self, task_id: str):
        """Async generator yielding SSE events for a task."""
        state = self._tasks.get(task_id)
        if not state:
            yield f"data: {json.dumps({'status': 'error', 'message': 'Task not found'})}\n\n"
            return

        while state.status in ("pending", "running"):
            yield f"data: {json.dumps({'progress': state.progress, 'message': state.message, 'status': state.status})}\n\n"
            await asyncio.sleep(0.5)

        # Final event
        payload = {
            "progress": state.progress,
            "message": state.message,
            "status": state.status,
        }
        if state.error:
            payload["error"] = state.error
        yield f"data: {json.dumps(payload)}\n\n"

    def cleanup(self, task_id: str):
        self._tasks.pop(task_id, None)
