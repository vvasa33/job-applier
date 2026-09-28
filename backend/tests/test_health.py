import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from jobhunter.api.app import create_app


def test_health_reports_database_ok(settings) -> None:
    client = TestClient(create_app(settings))

    direct = client.get("/health")
    prefixed = client.get("/api/health")

    assert direct.status_code == 200
    assert direct.json() == {"status": "ok", "database": "ok", "version": "0.1.0"}
    assert prefixed.json() == direct.json()


def test_startup_fails_when_database_path_is_unusable(settings) -> None:
    (settings.data_dir / "jobhunter.db").mkdir()
    with pytest.raises(SQLAlchemyError):
        create_app(settings)


def test_non_local_host_header_is_rejected(settings) -> None:
    client = TestClient(create_app(settings))
    response = client.get("/health", headers={"host": "example.com"})
    assert response.status_code == 400
