"""
API integration tests for auth flow.
"""

import pytest


class TestRegister:
    def test_register_creates_user(self, client, db_session):
        res = client.post("/api/v1/auth/register", json={
            "email": "new@bandami.com",
            "password": "StrongPass1!",
            "full_name": "New User",
        })
        assert res.status_code == 200
        assert "Account created" in res.json()["message"]

    def test_register_duplicate_email_rejected(self, client):
        data = {"email": "dup@bandami.com", "password": "Pass123!", "full_name": "Dup"}
        r1 = client.post("/api/v1/auth/register", json=data)
        assert r1.status_code == 200
        r2 = client.post("/api/v1/auth/register", json=data)
        assert r2.status_code == 409
        assert "already registered" in r2.json()["detail"].lower()

    def test_register_missing_fields(self, client):
        res = client.post("/api/v1/auth/register", json={"email": "x@x.com"})
        assert res.status_code == 422

    def test_register_invalid_email(self, client):
        res = client.post("/api/v1/auth/register", json={
            "email": "not-an-email",
            "password": "Pass123!",
            "full_name": "Invalid",
        })
        assert res.status_code == 422


class TestLogin:
    def test_login_with_correct_credentials(self, client, db_session):
        client.post("/api/v1/auth/register", json={
            "email": "loginme@bandami.com",
            "password": "LoginPass1!",
            "full_name": "Login User",
        })

        from app.models.user import UserProfile
        from datetime import datetime, timezone
        user = db_session.query(UserProfile).filter(UserProfile.email == "loginme@bandami.com").first()
        user.email_confirmed_at = datetime.now(timezone.utc)
        db_session.commit()

        res = client.post("/api/v1/auth/login", json={
            "email": "loginme@bandami.com",
            "password": "LoginPass1!",
        })
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["email"] == "loginme@bandami.com"
        assert data["user"]["subscription_tier"] == "free"

    def test_login_wrong_password(self, client, db_session):
        client.post("/api/v1/auth/register", json={
            "email": "wrongpw@bandami.com",
            "password": "RightPass1!",
            "full_name": "PW User",
        })
        from app.models.user import UserProfile
        from datetime import datetime, timezone
        user = db_session.query(UserProfile).filter(UserProfile.email == "wrongpw@bandami.com").first()
        user.email_confirmed_at = datetime.now(timezone.utc)
        db_session.commit()

        res = client.post("/api/v1/auth/login", json={
            "email": "wrongpw@bandami.com",
            "password": "WrongPass1!",
        })
        assert res.status_code == 401

    def test_login_unverified_email(self, client):
        r = client.post("/api/v1/auth/register", json={
            "email": "unverified@bandami.com",
            "password": "Pass123!",
            "full_name": "Unverified",
        })
        assert r.status_code == 200
        res = client.post("/api/v1/auth/login", json={
            "email": "unverified@bandami.com",
            "password": "Pass123!",
        })
        assert res.status_code == 403 or res.status_code == 401

    def test_login_nonexistent_user(self, client):
        res = client.post("/api/v1/auth/login", json={
            "email": "nobody@bandami.com",
            "password": "NoSuchPass1!",
        })
        assert res.status_code == 401


class TestRefresh:
    def test_valid_refresh_returns_new_tokens(self, client, db_session):
        r = client.post("/api/v1/auth/register", json={
            "email": "refreshme@bandami.com",
            "password": "Refresh1!",
            "full_name": "Refresh User",
        })
        assert r.status_code == 200

        from app.models.user import UserProfile
        from datetime import datetime, timezone
        user = db_session.query(UserProfile).filter(UserProfile.email == "refreshme@bandami.com").first()
        if user:
            user.email_confirmed_at = datetime.now(timezone.utc)
            db_session.commit()

        login_res = client.post("/api/v1/auth/login", json={
            "email": "refreshme@bandami.com",
            "password": "Refresh1!",
        })
        assert login_res.status_code == 200
        refresh_token = login_res.json()["refresh_token"]

        res = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert res.status_code == 200
        assert "access_token" in res.json()
        assert "refresh_token" in res.json()

    def test_invalid_refresh_token(self, client):
        res = client.post("/api/v1/auth/refresh", json={"refresh_token": "garbage.token.here"})
        assert res.status_code == 401

    def test_expired_refresh_token(self, client):
        res = client.post("/api/v1/auth/refresh", json={"refresh_token": ""})
        assert res.status_code == 401 or res.status_code == 422


class TestLogout:
    def test_logout_revokes_token(self, client, db_session):
        r = client.post("/api/v1/auth/register", json={
            "email": "logoutme@bandami.com",
            "password": "Logout1!",
            "full_name": "Logout User",
        })
        assert r.status_code == 200

        from app.models.user import UserProfile
        from datetime import datetime, timezone
        user = db_session.query(UserProfile).filter(UserProfile.email == "logoutme@bandami.com").first()
        if user:
            user.email_confirmed_at = datetime.now(timezone.utc)
            db_session.commit()

        login_res = client.post("/api/v1/auth/login", json={
            "email": "logoutme@bandami.com",
            "password": "Logout1!",
        })
        assert login_res.status_code == 200
        refresh_token = login_res.json()["refresh_token"]

        res_logout = client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
        assert res_logout.status_code == 200

        res_refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert res_refresh.status_code == 401


class TestProtectedRoutes:
    def test_stats_requires_auth(self, client):
        res = client.get("/api/v1/users/me/stats")
        assert res.status_code == 401

    def test_stats_with_auth(self, client, auth_headers):
        res = client.get("/api/v1/users/me/stats", headers=auth_headers)
        assert res.status_code == 200
        assert "daily_eval_limit" in res.json()

    def test_exams_with_auth(self, client, auth_headers):
        res = client.get("/api/v1/users/me/exams", headers=auth_headers)
        assert res.status_code == 200
        assert "exams" in res.json()
        assert isinstance(res.json()["exams"], list)


class TestCORS:
    # Default CORS config allows http://localhost:3000. Production overrides via env.
    KNOWN = "http://localhost:3000"
    UNKNOWN = "https://evil.com"

    def test_cors_preflight_allows_known_origin(self, client):
        """OPTIONS preflight with a known origin must return 200 + CORS headers."""
        res = client.options(
            "/api/v1/auth/login",
            headers={"Origin": self.KNOWN, "Access-Control-Request-Method": "POST"},
        )
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == self.KNOWN

    def test_cors_post_allows_known_origin(self, client, db_session):
        """A POST with the allowed origin must include access-control-allow-origin."""
        from app.models.user import UserProfile
        from datetime import datetime, timezone
        from app.core.security import hash_password
        import uuid

        uid = str(uuid.uuid4())
        db_session.add(UserProfile(
            id=uid, email="cors@bandami.com",
            hashed_password=hash_password("CorsPass1!"),
            email_confirmed_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        res = client.post(
            "/api/v1/auth/login",
            json={"email": "cors@bandami.com", "password": "CorsPass1!"},
            headers={"Origin": self.KNOWN},
        )
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == self.KNOWN

    def test_cors_rejects_unknown_origin(self, client):
        """An unknown origin must NOT be mirrored back in Access-Control-Allow-Origin."""
        res = client.options(
            "/api/v1/auth/login",
            headers={"Origin": self.UNKNOWN, "Access-Control-Request-Method": "POST"},
        )
        allow = res.headers.get("access-control-allow-origin")
        assert allow != self.UNKNOWN
