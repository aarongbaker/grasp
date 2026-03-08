"""
main.py
FastAPI application entry point with lifespan hook.

Lifespan hook runs at startup:
  1. Create Postgres tables (SQLModel)
  2. Initialise Pinecone client
  3. Build and compile the LangGraph graph with PostgresSaver

The compiled graph is stored as a module-level variable and accessed
via get_graph() by route handlers that need status_projection().
This avoids circular imports while keeping the graph a singleton.

V2 SSE streaming: the graph instance here also becomes the SSE event
source. The polling route's two-tier read becomes the SSE push logic.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI

_graph = None


def get_graph():
    """Returns the compiled LangGraph graph. Raises if not initialised."""
    if _graph is None:
        raise RuntimeError("LangGraph graph not initialised. Is the app running?")
    return _graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph

    # ── 1. Create DB tables ───────────────────────────────────────────────────
    from db.session import create_db_and_tables
    await create_db_and_tables()

    # ── 2. Initialise Pinecone ────────────────────────────────────────────────
    try:
        from pinecone import Pinecone
        from core.settings import get_settings
        settings = get_settings()
        if settings.pinecone_api_key:
            pc = Pinecone(api_key=settings.pinecone_api_key)
            app.state.pinecone = pc
    except Exception as e:
        print(f"Warning: Pinecone init failed ({e}). Ingestion will not work.")

    # ── 3. Build LangGraph graph ──────────────────────────────────────────────
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from graph.graph import build_grasp_graph
        from core.settings import get_settings
        settings = get_settings()

        checkpointer = await AsyncPostgresSaver.from_conn_string(
            settings.langgraph_checkpoint_url
        ).__aenter__()
        await checkpointer.setup()
        _graph = build_grasp_graph(checkpointer)
        app.state.graph = _graph

    except Exception as e:
        print(f"Warning: LangGraph init failed ({e}). Using MemorySaver fallback.")
        from langgraph.checkpoint.memory import MemorySaver
        from graph.graph import build_grasp_graph
        _graph = build_grasp_graph(MemorySaver())
        app.state.graph = _graph

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    from db.session import engine
    await engine.dispose()


app = FastAPI(
    title="GRASP",
    description="Generative Retrieval-Augmented Scheduling & Planning",
    version="1.6.0",
    lifespan=lifespan,
)

# ── Register routers ──────────────────────────────────────────────────────────
from api.routes.health import router as health_router
from api.routes.users import router as users_router
from api.routes.sessions import router as sessions_router
from api.routes.ingest import router as ingest_router

app.include_router(health_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(ingest_router, prefix="/api/v1")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
