import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import Base, SessionLocal, engine
from app.models import User
from app.routers.auth import router as auth_router
from app.routers.dialogue import router as dialogue_router
from app.routers.handoff import router as handoff_router

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # First boot on a fresh database (e.g. a new deployment): seed the synthetic demo data.
    if os.getenv("AUTO_SEED", "true").lower() == "true":
        db = SessionLocal()
        try:
            empty = db.query(User).first() is None
        finally:
            db.close()
        if empty:
            from app.seed import seed_demo_data
            seed_demo_data(quiet=True)
    yield


app = FastAPI(title=os.getenv("APP_NAME", "Voice Banking Agent"),
              description="A safe, simulated voice banking application (synthetic data only)",
              version="2.0.0", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "microphone=(self)")
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'")
    if request.url.path.startswith(("/auth", "/dialogue", "/agent", "/account")):
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/agent", include_in_schema=False)
def agent_page():
    return FileResponse(FRONTEND_DIR / "agent.html")


@app.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(dialogue_router)
app.include_router(handoff_router)
