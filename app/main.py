"""CloudTasks: FastAPI app serving the task API, VM status API and the single-page UI.

Invite-only: every /api route needs a signed-in session (see auth.py); /health stays public.
"""
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from fastapi.staticfiles import StaticFiles

from . import auth, db, system
from .ratelimit import RateLimitMiddleware, parse_networks
from .samples import sample_tasks
from .schemas import LoginRequest, Me, Task, TaskCreate, TaskUpdate

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_TASKS = int(os.environ.get("MAX_TASKS", "300"))  # per user: caps storage growth
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


def create_app() -> FastAPI:
    app = FastAPI(title="CloudTasks", version=os.environ.get("APP_VERSION", "dev"),
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RateLimitMiddleware)
    app.state.trusted_proxies = parse_networks(os.environ.get("TRUSTED_PROXIES", ""))
    app.state.throttle = auth.Throttle()
    db.init_db()
    auth.reset_secret_cache()
    auth.secret()  # create/load the session secret at start-up, not on the first login

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/") or request.url.path in ("/health", "/", "/login"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # Never leak stack traces or internals to the browser.
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    # ---- health (public, minimal) --------------------------------------------
    @app.get("/health")
    def health():
        try:
            ok = db.ping()
        except Exception:
            ok = False
        if not ok:
            return JSONResponse({"status": "error"}, status_code=503)
        return {"status": "ok"}

    # ---- auth ------------------------------------------------------------------
    @app.post("/api/auth/login", response_model=Me)
    async def login(body: LoginRequest, request: Request):
        auth.check_origin(request)  # blocks login CSRF from other sites
        user = await auth.login(request, body.username, body.password)
        token, csrf = auth.new_session(user["id"])
        response = JSONResponse(Me(username=user["username"], role=user["role"], csrf_token=csrf).model_dump())
        auth.set_session_cookie(response, token)
        return response

    @app.post("/api/auth/logout", status_code=204)
    def logout(s: dict = Depends(auth.current_user)):
        db.delete_session(auth.token_hash(s["token"]))
        response = Response(status_code=204)
        auth.clear_session_cookie(response)
        return response

    @app.get("/api/auth/me", response_model=Me)
    def me(s: dict = Depends(auth.current_user)):
        return Me(username=s["username"], role=s["role"], csrf_token=s["csrf_token"])

    # ---- tasks (always scoped to the signed-in user) --------------------------
    @app.get("/api/tasks", response_model=list[Task])
    def list_tasks(s: dict = Depends(auth.current_user)):
        return db.list_tasks(s["user_id"])

    @app.post("/api/tasks", response_model=Task, status_code=201)
    def create_task(body: TaskCreate, s: dict = Depends(auth.writer)):
        if db.count_tasks(s["user_id"]) >= MAX_TASKS:
            raise HTTPException(409, f"Task limit reached ({MAX_TASKS}). Delete some tasks first.")
        return db.create_task(s["user_id"], body.model_dump(mode="json"))

    @app.patch("/api/tasks/{task_id}", response_model=Task)
    def update_task(task_id: int, body: TaskUpdate, s: dict = Depends(auth.writer)):
        changes = body.model_dump(mode="json", exclude_unset=True)
        bad = [k for k in NOT_NULLABLE if k in changes and changes[k] is None]
        if bad:
            raise HTTPException(422, f"Field(s) cannot be null: {', '.join(sorted(bad))}")
        task = db.update_task(s["user_id"], task_id, changes)
        if task is None:
            raise HTTPException(404, "Task not found")
        return task

    @app.post("/api/tasks/{task_id}/toggle", response_model=Task)
    def toggle_task(task_id: int, s: dict = Depends(auth.writer)):
        task = db.toggle_task(s["user_id"], task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        return task

    @app.delete("/api/tasks/{task_id}", status_code=204)
    def delete_task(task_id: int, s: dict = Depends(auth.writer)):
        if not db.delete_task(s["user_id"], task_id):
            raise HTTPException(404, "Task not found")

    @app.post("/api/tasks/sample", response_model=list[Task], status_code=201)
    def load_sample_tasks(s: dict = Depends(auth.writer)):
        samples = sample_tasks()
        if db.count_tasks(s["user_id"]) + len(samples) > MAX_TASKS:
            raise HTTPException(409, f"Task limit reached ({MAX_TASKS}).")
        # Oldest first, so the list (newest first) shows them in the order above.
        created = [db.create_task(s["user_id"], TaskCreate(**t).model_dump(mode="json")) for t in reversed(samples)]
        return created[::-1]

    # ---- VM status (signed-in users only) ---------------------------------------
    @app.get("/api/metrics")
    def metrics(s: dict = Depends(auth.current_user)):
        return system.metrics()

    @app.get("/api/info")
    def info(s: dict = Depends(auth.current_user)):
        return system.info()

    # ---- pages ------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index(request: Request):
        if not auth.session_from_request(request):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if auth.session_from_request(request):
            return RedirectResponse("/", status_code=303)
        return FileResponse(STATIC_DIR / "login.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
