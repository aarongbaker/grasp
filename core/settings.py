"""
core/settings.py
Pydantic-settings from .env, cached with @lru_cache (reads once).

Two database URLs because LangGraph's PostgresSaver uses psycopg3 (sync)
directly, not through SQLAlchemy. FastAPI routes use asyncpg through
SQLAlchemy. These are different drivers with different URL schemes:
  asyncpg: postgresql+asyncpg://...
  psycopg3: postgresql://...    (or postgresql+psycopg://...)
Keeping them separate avoids driver confusion and connection pool collisions.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    # FastAPI / SQLAlchemy (asyncpg driver)
    database_url: str = "postgresql+asyncpg://grasp:grasp@localhost:5432/grasp"
    # LangGraph PostgresSaver (psycopg3 driver — different from above)
    langgraph_checkpoint_url: str = "postgresql://grasp:grasp@localhost:5432/grasp"

    # Test databases
    test_database_url: str = "postgresql+asyncpg://grasp:grasp@localhost:5432/grasp_test"
    test_langgraph_checkpoint_url: str = "postgresql://grasp:grasp@localhost:5432/grasp_test"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # LLM Providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_index_name: str = "grasp-cookbooks"
    pinecone_environment: str = "us-east-1-aws"

    # RAG
    rag_retrieval_top_k: int = 5

    # Pipeline
    celery_task_timeout: int = 600
    celery_worker_concurrency: int = 4

    # Phase 3 test flag
    simulate_interrupt: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
