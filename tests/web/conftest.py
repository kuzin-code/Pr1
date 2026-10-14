from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.web.app import create_app

SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"


@pytest.fixture
def app(tmp_path):
    settings = Settings(db_url="sqlite://", upload_dir=tmp_path / "uploads", seed_dir=SEED_DIR,
                        secret_key="test-secret")
    return create_app(settings)


@pytest.fixture
def login(app):
    clients = {}

    def _login(username: str) -> TestClient:
        if username not in clients:
            client = TestClient(app, follow_redirects=False)
            resp = client.post("/login", data={"username": username, "password": "demo"})
            assert resp.status_code == 303, resp.text
            clients[username] = client
        return clients[username]

    return _login
