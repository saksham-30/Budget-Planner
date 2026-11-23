import sqlite3
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
import pytest

from backend import auth, main, migrations
from backend.persistence import TripNotFoundError, TripStore

ORIGIN = "http://localhost:5173"
PASSWORD = "a-long-test-password"
OWNER = "test-owner"


def _prepared_client(tmp_path, monkeypatch):
    monkeypatch.setattr(migrations, "_restrict_backup_permissions", lambda *_: None)
    database = tmp_path / "auth.sqlite3"
    store = TripStore(database)
    backup = migrations.create_verified_backup(database, tmp_path / "backups")
    migrations.migrate_local_owner(
        backup, OWNER, PasswordHasher().hash(PASSWORD)
    )

    def connect(_path=None):
        connection = sqlite3.connect(database, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(auth, "_connect", connect)
    monkeypatch.setattr(main, "TripStore", lambda: store)
    return TestClient(main.app), database, store


def test_trip_routes_require_a_session(tmp_path, monkeypatch):
    client, _, _ = _prepared_client(tmp_path, monkeypatch)
    requests = [
        ("GET", "/api/trips"),
        ("GET", "/api/trips/x/preferences"),
        ("GET", "/api/trips/x/conversation"),
        ("GET", "/api/trips/x/plan"),
        ("PUT", "/api/trips/x/preferences"),
        ("POST", "/api/trips/x/plan"),
        ("POST", "/api/trips/x/plan/stream"),
    ]
    for method, path in requests:
        response = client.request(method, path, json={})
        assert response.status_code == 401, (method, path, response.text)


def test_exact_origin_csrf_hash_and_owner_checks(tmp_path, monkeypatch):
    client, _, store = _prepared_client(tmp_path, monkeypatch)
    assert client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"Origin": f"{ORIGIN}.attacker.test"},
    ).status_code == 403

    login = client.post(
        "/api/auth/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
    )
    assert login.status_code == 200
    csrf = client.cookies.get(auth.CSRF_COOKIE)
    assert csrf

    payload = {
        "destination": "Goa", "start_date": "2026-12-01", "end_date": "2026-12-05",
        "travelers": 2, "budget": 20000, "currency": "INR",
        "budget_style": "moderate", "preferred_accommodation_type": "hotel",
        "food_preferences": [], "interests": [], "hard_constraints": [],
    }
    path = "/api/trips/private-trip/preferences"
    assert client.put(path, json=payload, headers={"Origin": ORIGIN}).status_code == 403
    assert client.put(
        path, json=payload,
        headers={"Origin": f"{ORIGIN}.evil", "X-CSRF-Token": csrf},
    ).status_code == 403
    assert client.put(
        path, json=payload,
        headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
    ).status_code == 403
    assert client.put(
        path, json=payload,
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    ).status_code == 200

    with pytest.raises(TripNotFoundError):
        store.get_preferences_for_owner("private-trip", "different-owner")


def test_login_hashes_password_rotates_session_and_expires(tmp_path, monkeypatch):
    client, database, _ = _prepared_client(tmp_path, monkeypatch)
    headers = {"Origin": ORIGIN}
    first = client.post("/api/auth/login", json={"password": PASSWORD}, headers=headers)
    assert first.status_code == 200
    first_token = client.cookies.get(auth.SESSION_COOKIE)

    connection = sqlite3.connect(database)
    stored_hash = connection.execute("SELECT password_hash FROM local_owner").fetchone()[0]
    assert stored_hash.startswith("$argon2id$")
    assert PASSWORD not in stored_hash
    connection.close()

    second = client.post("/api/auth/login", json={"password": PASSWORD}, headers=headers)
    assert second.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert first_token != client.cookies.get(auth.SESSION_COOKIE)
    old_session_client = TestClient(main.app)
    old_session_client.cookies.set(auth.SESSION_COOKIE, first_token)
    assert old_session_client.get("/api/auth/me").status_code == 401

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE auth_sessions SET expires_at = ?",
        ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),),
    )
    connection.commit()
    connection.close()
    assert client.get("/api/auth/me").status_code == 401


def test_logout_requires_csrf_and_revokes_current_session(tmp_path, monkeypatch):
    client, _, _ = _prepared_client(tmp_path, monkeypatch)
    login = client.post(
        "/api/auth/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
    )
    assert login.status_code == 200
    csrf = client.cookies.get(auth.CSRF_COOKIE)

    assert client.post("/api/auth/logout", headers={"Origin": ORIGIN}).status_code == 403
    logout = client.post(
        "/api/auth/logout",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )
    assert logout.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_login_throttle_blocks_after_fifth_bad_attempt(tmp_path, monkeypatch):
    client, database, _ = _prepared_client(tmp_path, monkeypatch)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE local_owner SET failed_login_count = 4")
    connection.commit()
    connection.close()

    headers = {"Origin": ORIGIN}
    failed = client.post("/api/auth/login", json={"password": "wrong"}, headers=headers)
    blocked = client.post("/api/auth/login", json={"password": PASSWORD}, headers=headers)
    assert failed.status_code == 401
    assert blocked.status_code == 429
