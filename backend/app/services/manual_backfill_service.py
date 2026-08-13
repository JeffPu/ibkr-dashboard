from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4


@dataclass(slots=True)
class ManualBackfillTask:
    task_id: str
    status: str
    start_date: str
    end_date: str
    requested_dates: list[str]
    succeeded_dates: list[str]
    failed_dates: list[str]
    succeeded_days: int
    failed_days: int
    total_days: int
    processed_days: int
    progress: float
    created_order: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration_ms: int | None


class ManualBackfillService:
    def __init__(self, *, max_task_logs: int = 100) -> None:
        self.tasks: dict[str, ManualBackfillTask] = {}
        self.max_task_logs = min(max(max_task_logs, 1), 100)
        self._created_counter = 0

    def enqueue(self, *, start_date: date, end_date: date) -> str:
        task_id = uuid4().hex
        self._created_counter += 1
        while len(self.tasks) >= self.max_task_logs:
            oldest_task_id = next(iter(self.tasks))
            self.tasks.pop(oldest_task_id, None)
        requested_dates = self._build_date_range(start_date, end_date)
        self.tasks[task_id] = ManualBackfillTask(
            task_id=task_id,
            status="pending",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            requested_dates=requested_dates,
            succeeded_dates=[],
            failed_dates=[],
            succeeded_days=0,
            failed_days=0,
            total_days=len(requested_dates),
            processed_days=0,
            progress=0.0,
            created_order=self._created_counter,
            created_at=datetime.now(timezone.utc).isoformat(),
            started_at=None,
            finished_at=None,
            duration_ms=None,
        )
        return task_id

    def get_task(self, task_id: str) -> ManualBackfillTask | None:
        return self.tasks.get(task_id)

    def run_task(self, task_id: str, runner) -> ManualBackfillTask | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        started = datetime.now(timezone.utc)
        task.started_at = started.isoformat()
        task.finished_at = None
        task.duration_ms = None
        task.status = "running"
        for task_date in task.requested_dates:
            try:
                runner()
                task.succeeded_dates.append(task_date)
                task.succeeded_days += 1
            except Exception:  # pragma: no cover - defensive task bookkeeping
                task.failed_dates.append(task_date)
                task.failed_days += 1
            finally:
                task.processed_days += 1
                if task.total_days > 0:
                    task.progress = task.processed_days / task.total_days
        task.status = "failed" if task.failed_days > 0 else "completed"
        if task.total_days == 0:
            task.progress = 1.0
        finished = datetime.now(timezone.utc)
        task.finished_at = finished.isoformat()
        task.duration_ms = max(int((finished - started).total_seconds() * 1000), 0)
        return task

    def list_tasks(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> list[ManualBackfillTask]:
        normalized_page = max(page, 1)
        normalized_page_size = min(max(page_size, 1), 100)
        ordered = sorted(
            self.tasks.values(),
            key=lambda task: task.created_order,
            reverse=True,
        )
        start = (normalized_page - 1) * normalized_page_size
        end = start + normalized_page_size
        return ordered[start:end]

    def _build_date_range(self, start_date: date, end_date: date) -> list[str]:
        dates: list[str] = []
        current = start_date
        while current <= end_date:
            dates.append(current.isoformat())
            current += timedelta(days=1)
        return dates
