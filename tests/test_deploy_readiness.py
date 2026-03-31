from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_frontend_client_supports_configurable_api_base() -> None:
    client_source = (REPO_ROOT / "frontend" / "src" / "api" / "client.ts").read_text()

    assert "import.meta.env.VITE_API_URL" in client_source
    assert "const API_BASE = normalizedApiUrl ? `${normalizedApiUrl}/api/v1` : '/api/v1';" in client_source


def test_production_settings_accept_cross_origin_frontend_env() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret_key="not-default-secret",
        cors_allowed_origins=["https://grasp.example.com"],
    )

    assert settings.app_env == "production"
    assert settings.jwt_secret_is_default is False
    assert settings.cors_allowed_origins == ["https://grasp.example.com"]


def test_settings_default_to_development_localhost_contract() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.jwt_secret_is_default is True
    assert settings.cors_allowed_origins == [
        "http://localhost:3000",
        "http://localhost:8501",
        "http://localhost:5173",
    ]


def test_settings_reject_blank_cors_env_string() -> None:
    with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS must be a JSON array string"):
        Settings(_env_file=None, cors_allowed_origins="   ")


def test_main_fails_fast_on_checkpoint_init_in_production() -> None:
    main_source = (REPO_ROOT / "app" / "main.py").read_text()

    assert "LangGraph checkpoint initialisation failed in production." in main_source
    assert "Check LANGGRAPH_CHECKPOINT_URL and Postgres connectivity." in main_source
    assert 'if settings.app_env == "production":' in main_source
    assert "async def get_graph():" in main_source
    assert "Graph initialisation is lazy" in main_source
    assert 'command.upgrade(alembic_cfg, "head")' not in main_source
    assert "Migrations run in deploy/pre-deploy, not app startup" in main_source


def test_main_uses_shared_localhost_dev_origins_contract() -> None:
    main_source = (REPO_ROOT / "app" / "main.py").read_text()
    settings_source = (REPO_ROOT / "app" / "core" / "settings.py").read_text()

    assert "from app.core.settings import _DEV_ORIGINS" in main_source
    assert 'http://localhost:5173' in settings_source
    assert "configured_origins == dev_origins or configured_origins <= dev_origins" in main_source


def test_readme_documents_vite_frontend_local_flow() -> None:
    readme = (REPO_ROOT / "README.md").read_text()

    assert "npm --prefix frontend install" in readme
    assert "npm --prefix frontend run dev" in readme
    assert "http://localhost:5173" in readme
    assert "Development startup should not require shell overrides" in readme
    assert "Production-only values belong in deploy environments" in readme


def test_readme_documents_deploy_step_migrations_instead_of_startup() -> None:
    readme = (REPO_ROOT / "README.md").read_text()

    assert "Migrations are **not** run by app startup anymore." in readme
    assert "alembic upgrade head" in readme


def test_railway_docs_use_real_worker_module_path() -> None:
    guide = (REPO_ROOT / "docs" / "RAILWAY_CLOUDFLARE_DEPLOY_GUIDE.md").read_text()
    checklist = (REPO_ROOT / "docs" / "RAILWAY_DEPLOY_CHECKLIST.md").read_text()
    readme = (REPO_ROOT / "README.md").read_text()

    expected = "celery -A app.workers.celery_app worker"

    assert expected in guide
    assert expected in checklist
    assert expected in readme
    assert "celery -A workers.celery_app worker" not in guide


def test_deploy_docs_reference_frontend_api_base_env() -> None:
    guide = (REPO_ROOT / "docs" / "RAILWAY_CLOUDFLARE_DEPLOY_GUIDE.md").read_text()
    checklist = (REPO_ROOT / "docs" / "RAILWAY_DEPLOY_CHECKLIST.md").read_text()

    assert "VITE_API_URL" in guide
    assert "VITE_API_URL" in checklist


def test_celery_app_makes_worker_startup_retry_explicit() -> None:
    from app.workers.celery_app import celery_app

    assert celery_app.conf.broker_connection_retry is True
    assert celery_app.conf.broker_connection_retry_on_startup is True
    assert celery_app.conf.task_max_retries == 0


def test_fresh_db_migrations_guard_missing_sessionstatus_enum() -> None:
    add_celery_migration = (
        REPO_ROOT / "alembic" / "versions" / "a1b2c3d4e5f6_add_celery_task_id_to_sessions.py"
    ).read_text()
    fix_case_migration = (
        REPO_ROOT / "alembic" / "versions" / "c4d5e6f7a8b9_fix_cancelled_enum_case.py"
    ).read_text()

    assert "IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'sessionstatus')" in add_celery_migration
    assert "ALTER TYPE sessionstatus ADD VALUE IF NOT EXISTS 'CANCELLED'" in add_celery_migration
    assert "IF EXISTS (" in fix_case_migration
    assert "typname = 'sessionstatus'" in fix_case_migration
