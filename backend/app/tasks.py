"""导入任务管理：异步执行、状态跟踪、取消支持（内存实现，重启清空）。"""

import asyncio
import time
import uuid
from typing import Awaitable, Callable


class ImportTask:
    def __init__(self, filename: str, size: int):
        self.id = uuid.uuid4().hex[:12]
        self.filename = filename
        self.size = size
        self.status = "pending"  # pending / processing / completed / failed / cancelled
        self.stage = "queued"  # queued / parsing / embedding / storing
        self.parsed_chars = 0
        self.chunks = 0
        self.error: str | None = None
        self.created_at = time.time()
        self.finished_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "size": self.size,
            "status": self.status,
            "stage": self.stage,
            "parsed_chars": self.parsed_chars,
            "chunks": self.chunks,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class ImportManager:
    def __init__(self):
        self._tasks: dict[str, ImportTask] = {}
        self._runners: dict[str, asyncio.Task] = {}

    def create(self, filename: str, size: int) -> ImportTask:
        task = ImportTask(filename, size)
        self._tasks[task.id] = task
        return task

    def start(self, task_id: str, runner: Callable[[str], Awaitable[None]]):
        task = self._tasks[task_id]

        async def _wrap():
            task.status = "processing"
            try:
                await runner(task_id)
                if task.status != "cancelled":
                    task.status = "completed"
                    task.finished_at = time.time()
            except asyncio.CancelledError:
                task.status = "cancelled"
                task.stage = "cancelled"
                task.finished_at = time.time()
            except Exception as exc:
                task.status = "failed"
                task.error = str(exc)
                task.finished_at = time.time()
            finally:
                self._runners.pop(task_id, None)

        self._runners[task_id] = asyncio.create_task(_wrap())

    def get(self, task_id: str) -> ImportTask | None:
        return self._tasks.get(task_id)

    def list(self) -> list[dict]:
        return [t.to_dict() for t in sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)]

    def cancel(self, task_id: str) -> str:
        """返回 cancelled / not_found / not_cancellable。"""
        task = self._tasks.get(task_id)
        if task is None:
            return "not_found"
        if task.status in ("completed", "failed", "cancelled"):
            return "not_cancellable"
        runner = self._runners.get(task_id)
        if runner and not runner.done():
            runner.cancel()
        else:
            task.status = "cancelled"
            task.stage = "cancelled"
            task.finished_at = time.time()
        return "cancelled"


import_manager = ImportManager()
