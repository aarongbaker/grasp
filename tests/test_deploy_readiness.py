from pathlib import Path

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


def test_main_fails_fast_on_checkpoint_init_in_production() -> None:
    main_source = (REPO_ROOT / "app" / "main.py").read_text()

    assert "LangGraph checkpoint initialisation failed in production." in main_source
    assert "Check LANGGRAPH_CHECKPOINT_URL and Postgres connectivity." in main_source
    assert 'if settings.app_env == "production":' in main_source
    assert "async def get_graph():" in main_source
    assert "Graph initialisation is lazy" in main_source
    assert 'command.upgrade(alembic_cfg, "head")' not in main_source
    assert "Migrations run in deploy/pre-deploy, not app startup" in main_source


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
