"""CloudTasks: FastAPI app serving the task API, VM status API and the single-page UI."""
import os
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, system
from .ratelimit import RateLimitMiddleware
from .schemas import Task, TaskCreate, TaskUpdate

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_TASKS = int(os.environ.get("MAX_TASKS", "300"))  # public VM: cap storage growth
NOT_NULLABLE = {"title", "priority", "color", "done"}

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _sample_tasks() -> list[dict]:
    today = date.today()
    d = lambda days: (today + timedelta(days=days)).isoformat()  # noqa: E731
    return [
        {"title": "Provision the Azure VM", "note": "Ubuntu 24.04, B1s, SSH key only. Check the student policy for allowed regions first.", "priority": "high", "due_date": d(0)},
        {"title": "Lock down the NSG", "note": "Port 22 only from my IP /32, port 80 open to the world.", "priority": "high", "due_date": d(1)},
        {"title": "Write the Dockerfile", "priority": "medium", "due_date": d(2)},
        {"title": "Set up nginx reverse proxy", "note": "App stays on the internal Docker network. Only nginx publishes port 80.", "priority": "medium"},
        {"title": "Water the plants", "priority": "low", "due_date": d(-1)},
        {"title": "Read about cloud-init", "note": "First-boot scripts: install Docker before I even SSH in.", "priority": "low", "due_date": d(5)},
        {"title": "Configure auto-shutdown at 02:00 IST", "note": "Saves credits if I forget to pause the VM after a late-night session.", "priority": "medium", "due_date": d(1)},
        {"title": "Screenshot the portal for the report", "priority": "medium", "due_date": d(4)},
        {"title": "Buy oat milk", "note": "And maybe a croissant.", "priority": "low"},
        {"title": "Prepare PBL viva slides", "note": "Architecture diagram, cost model, security choices, live demo, what I learned.", "priority": "high", "due_date": d(7)},
    ]


def create_app() -> FastAPI:
    app = FastAPI(title="CloudTasks", version=os.environ.get("APP_VERSION", "dev"),
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RateLimitMiddleware)
    db.init_db()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/") or request.url.path == "/health":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # Never leak stack traces or internals to the browser.
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    # ---- health -------------------------------------------------------------
    @app.get("/health")
    def health():
        try:
            ok = db.ping()
        except Exception:
            ok = False
        if not ok:
            return JSONResponse({"status": "error", "database": "unavailable"}, status_code=503)
        return {"status": "ok", "database": "ok", "version": app.version}

    # ---- tasks --------------------------------------------------------------
    @app.get("/api/tasks", response_model=list[Task])
    def list_tasks():
        return db.list_tasks()

    @app.post("/api/tasks", response_model=Task, status_code=201)
    def create_task(body: TaskCreate):
        if db.count_tasks() >= MAX_TASKS:
            raise HTTPException(409, f"Task limit reached ({MAX_TASKS}). Delete some tasks first.")
        return db.create_task(body.model_dump(mode="json"))

    @app.patch("/api/tasks/{task_id}", response_model=Task)
    def update_task(task_id: int, body: TaskUpdate):
        changes = body.model_dump(mode="json", exclude_unset=True)
        bad = [k for k in NOT_NULLABLE if k in changes and changes[k] is None]
        if bad:
            raise HTTPException(422, f"Field(s) cannot be null: {', '.join(sorted(bad))}")
        task = db.update_task(task_id, changes)
        if task is None:
            raise HTTPException(404, "Task not found")
        return task

    @app.post("/api/tasks/{task_id}/toggle", response_model=Task)
    def toggle_task(task_id: int):
        task = db.toggle_task(task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        return task

    @app.delete("/api/tasks/{task_id}", status_code=204)
    def delete_task(task_id: int):
        if not db.delete_task(task_id):
            raise HTTPException(404, "Task not found")

    @app.post("/api/tasks/sample", response_model=list[Task], status_code=201)
    def load_sample_tasks():
        samples = _sample_tasks()
        if db.count_tasks() + len(samples) > MAX_TASKS:
            raise HTTPException(409, f"Task limit reached ({MAX_TASKS}).")
        # Oldest first, so the list (newest first) shows them in the order above.
        return [db.create_task(TaskCreate(**s).model_dump(mode="json")) for s in reversed(samples)][::-1]

    # ---- VM status ----------------------------------------------------------
    @app.get("/api/metrics")
    def metrics():
        return system.metrics()

    @app.get("/api/info")
    def info():
        return system.info()

    # ---- UI -----------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
