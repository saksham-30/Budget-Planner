import sqlite3

from fastapi.testclient import TestClient
from argon2 import PasswordHasher

from backend import main, migrations, public_access
from backend.persistence import TripStore

OWNER = "public-test-owner"


def _public_client(tmp_path, monkeypatch):
    monkeypatch.setattr(migrations, "_restrict_backup_permissions", lambda *_: None)
    database = tmp_path / "public.sqlite3"
    store = TripStore(database)
    backup = migrations.create_verified_backup(database, tmp_path / "backups")
    migrations.migrate_local_owner(backup, OWNER, PasswordHasher().hash("unused-legacy-password"))
    monkeypatch.setattr(public_access, "DATABASE_PATH", database)
    monkeypatch.setattr(main, "TripStore", lambda: store)
    return TestClient(main.app), store


def test_application_endpoints_are_public_without_session_or_csrf(tmp_path, monkeypatch):
    client, store = _public_client(tmp_path, monkeypatch)

    assert client.get("/api/destinations").status_code == 200
    trips = client.get("/api/trips")
    assert trips.status_code == 200
    assert trips.json() == []

    saved = client.put(
        "/api/trips/public-trip/preferences",
        json={
            "destination": "Goa", "start_date": "2026-12-01", "end_date": "2026-12-05",
            "travelers": 2, "budget": 20000, "currency": "INR",
            "budget_style": "moderate", "preferred_accommodation_type": "hotel",
            "food_preferences": [], "interests": [], "hard_constraints": [],
        },
    )
    assert saved.status_code == 200, saved.text
    assert client.get("/api/trips").json()[0]["trip_id"] == "public-trip"
    assert store.get_preferences_for_owner("public-trip", OWNER) is not None


def test_auth_routes_are_not_part_of_public_api(tmp_path, monkeypatch):
    client, _ = _public_client(tmp_path, monkeypatch)
    assert client.get("/api/auth/me").status_code == 404
    assert client.post("/api/auth/login", json={"password": "anything"}).status_code == 404


def test_public_cors_does_not_enable_credentials():
    response = TestClient(main.app).options(
        "/api/trips",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-credentials" not in response.headers
